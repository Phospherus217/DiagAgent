"""Agent runtime managing the closed-loop interaction between Model, Agent, and GUI Environment."""

from typing import Any, Dict, List, Optional, Union
import json
import time

from diagagent.actions.parser import ActionParser
from diagagent.agent.base import BaseAgent
from diagagent.agent.context import ContextBuilder
from diagagent.agent.state import RuntimeState
from diagagent.agent.termination import TerminationPolicy
from diagagent.core.errors import ModelAPIError
from diagagent.diagnosis.report import DiagnosticReport
from diagagent.environment.base import BaseEnvironment
from diagagent.models.base import Model, ModelResponse
from diagagent.recovery.policy import RecoveryPolicy
from diagagent.trace.models import TraceStepRecord
from diagagent.verification.verifier import RuntimeVerifier


class AgentRuntime:
    """Orchestrates closed-loop agent execution, verification, diagnosis, and recovery."""

    def __init__(
        self,
        env: BaseEnvironment,
        model: Optional[Model] = None,
        verifier: Optional[RuntimeVerifier] = None,
        recovery_policy: Optional[RecoveryPolicy] = None,
        termination_policy: Optional[TerminationPolicy] = None,
        context_builder: Optional[ContextBuilder] = None,
        automatic_recovery: bool = True,
        human_feedback: Optional[Dict[str, Any]] = None,
    ):
        self.env = env
        self.model = model
        self.verifier = verifier or RuntimeVerifier()
        self.recovery_policy = recovery_policy or RecoveryPolicy()
        self.termination_policy = termination_policy or TerminationPolicy()
        self.context_builder = context_builder or ContextBuilder()
        self.automatic_recovery = automatic_recovery
        self.human_feedback = human_feedback or {}

    def _complete_after_export(self, action, info, observation, verification) -> bool:
        """Stop on verified final export without asking the model to emit stop."""
        raw = action.to_dict() if hasattr(action, "to_dict") else action
        execution = info.get("execution_result", {})
        if (
            raw.get("type") != "export_file"
            or info.get("error")
            or execution.get("success") is not True
            or execution.get("effect_status") != "PASS"
            or not verification.passed
        ):
            return False

        # An intermediate export must not skip subsequent declared task work.
        subgoals = sorted(self.env.task_spec.get("subgoals", []),
                          key=lambda goal: goal.get("order", 0))
        if subgoals and subgoals[-1].get("type") != "artifact":
            return False

        from diagagent.tasks import output_name
        name = output_name(self.env.task_spec.get("initial_state", {}).get("output_file", "output.png"))
        artifact = self.env.trace_recorder.artifacts_dir / name
        try:
            artifact_result = self.env.dual_evaluator.evaluate_artifact(artifact)
        except Exception as error:
            self.env.record_failure(error, "Evaluator Error", "execution_error")
            return True
        self.env.trace_recorder.event(
            "artifact_completion_checked", step=self.env.step_index,
            artifact_evaluation=artifact_result.model_dump(),
        )
        if artifact_result.status != "PASS" or not artifact_result.artifact_pass:
            return False

        self.env.done = True
        self.env.termination_reason = "completed"
        # Preserve the export record and append a terminal marker at the same
        # step, without inventing another model decision, action, or screenshot.
        self.env.trace_recorder.record_step(TraceStepRecord(
            step=self.env.step_index, phase="final", done=True,
            obs_after=observation.to_dict(),
            termination_reason="completed",
            artifact_evaluation=artifact_result.model_dump(),
            note="Final export executed successfully and artifact verification passed.",
        ))
        self.env.trace_recorder.event(
            "runtime_completed", step=self.env.step_index,
            termination_reason="completed", trigger="artifact_verified_export",
        )
        return True

    def run(
        self,
        task_spec: Dict[str, Any],
        agent: Optional[BaseAgent] = None,
        model: Optional[Model] = None,
        max_steps: Optional[int] = None,
    ) -> DiagnosticReport:
        """Entry point running a task spec with either a Model or an Agent."""
        return self.run_task(task_spec=task_spec, agent=agent, model=model, max_steps=max_steps)

    def run_task(
        self,
        task_spec: Dict[str, Any],
        agent: Optional[BaseAgent] = None,
        model: Optional[Model] = None,
        max_steps: Optional[int] = None,
    ) -> DiagnosticReport:
        """Executes a complete task run from reset through final evaluation."""
        limit = max_steps if max_steps is not None else task_spec.get("max_steps", 20)
        if type(limit) is not int or limit <= 0:
            raise ValueError("max_steps must be a positive integer")

        active_model = model or self.model
        from diagagent.agent.rule_agent import RuleAgent
        from diagagent.agent.mock_agent import MockFaultAgent
        from diagagent.tasks import public_task

        if active_model is not None:
            self.env.agent_type = "model"
        elif isinstance(agent, MockFaultAgent):
            self.env.agent_type = "fault"
        elif isinstance(agent, RuleAgent):
            self.env.agent_type = "oracle"
        else:
            self.env.agent_type = "custom"

        self.env.prepare(task_spec)
        if active_model is not None:
            self.env.trace_recorder.redactor.register(getattr(active_model, "api_key", None))

        state = RuntimeState(
            run_id=self.env.run_id,
            task_id=str(task_spec.get("task_id", "unspecified")),
            retry_budget=self.recovery_policy.max_action_retries,
            repair_budget=self.env.config.max_format_repairs if self.automatic_recovery else 0,
            replan_budget=self.recovery_policy.max_replans,
        )

        public_feedback: Dict[str, Any] = dict(self.human_feedback)
        history: List[Dict[str, Any]] = []

        try:
            obs = self.env.reset(task_spec)
            state.last_observation = obs

            if agent is not None:
                agent.reset(
                    self.env.task_spec
                    if isinstance(agent, (RuleAgent, MockFaultAgent))
                    else public_task(self.env.task_spec)
                )

            for step_idx in range(1, limit + 1):
                state.step = step_idx
                step_start_time = time.monotonic()

                if time.monotonic() >= self.env.deadline:
                    raise TimeoutError("Run deadline exceeded")

                self.env.trace_recorder.event("decision_started", next_step=self.env.step_index + 1)
                self.env.total_decisions += 1

                from diagagent.observation.models import AgentObservation
                public_obs = (
                    obs
                    if isinstance(agent, (RuleAgent, MockFaultAgent))
                    else AgentObservation.from_private(
                        obs,
                        self.env.task_spec.get("allowed_observations", ["screenshot"]),
                    )
                )
                if isinstance(public_obs, AgentObservation):
                    public_obs.feedback = public_feedback

                # Decision Phase
                action: Any = None
                action_parse_error: Optional[str] = None

                if active_model is not None:
                    # Model Query Loop
                    messages = self.context_builder.build_context(
                        task_spec=self.env.task_spec,
                        observation=public_obs,
                        history=history,
                        feedback=public_feedback,
                    )
                    metadata_fn = getattr(active_model, "request_metadata", None)
                    request_metadata = metadata_fn() if callable(metadata_fn) else {}
                    request_metadata = {**request_metadata, "message_count": len(messages)}
                    model_step = self.env.step_index + 1
                    call_id = self.env.model_call_count + 1
                    self.env.trace_recorder.event(
                        "model_request", step=model_step, call_id=call_id,
                        status="STARTED", model_request_metadata=request_metadata,
                        model=request_metadata.get("model", type(active_model).__name__),
                        messages=[m.model_dump() for m in messages],
                        observation_screenshot=str(public_obs.screenshot_path),
                    )
                    self.env.model_call_count += 1
                    query_started = time.monotonic()
                    try:
                        model_resp = active_model.query(messages, observation=public_obs)
                    except ModelAPIError as error:
                        self.env.trace_recorder.event(
                            "model_error", step=model_step, call_id=call_id, status="FAILED",
                            model_request_metadata=request_metadata, model_error=error.to_dict(),
                            latency_ms=int((time.monotonic() - query_started) * 1000),
                        )
                        raise
                    self.env.trace_recorder.event(
                        "model_response", step=model_step, call_id=call_id, status="SUCCEEDED",
                        latency_ms=model_resp.latency_ms,
                        model=model_resp.model, response_text=model_resp.text,
                        response=model_resp.raw,
                    )

                    # Parse action text from model
                    try:
                        action_raw = json.loads(model_resp.text.strip())
                        action = ActionParser.parse(action_raw)
                    except Exception as e:
                        action_parse_error = str(e)
                        action = {"type": "invalid_format", "raw_text": model_resp.text}
                    self.env.trace_recorder.event(
                        "action_parsed", step=model_step, call_id=call_id,
                        status="FAILED" if action_parse_error else "SUCCEEDED",
                        error_type="Action Format Error" if action_parse_error else None,
                        error_message=action_parse_error,
                        parsed_action=action.to_dict() if hasattr(action, "to_dict") else action,
                    )
                elif agent is not None:
                    if hasattr(agent, "deadline"):
                        agent.deadline = self.env.deadline
                    action = agent.act(public_obs)
                else:
                    raise ValueError("AgentRuntime requires either a Model or an Agent to run.")

                # If model produced a format error in text, handle format repair
                if action_parse_error:
                    from diagagent.verification.models import VerificationResult
                    synthetic_verif = VerificationResult(
                        passed=False,
                        suspected_failure="Action Format Error",
                        evidence=[f"Failed to parse action JSON: {action_parse_error}"],
                    )
                    rec_decision = self.recovery_policy.decide(
                        synthetic_verif, state, action_error=action_parse_error
                    )
                    if rec_decision.decision == "repair_format":
                        public_feedback = rec_decision.feedback_for_model or {
                            "action_error": action_parse_error,
                            "repair_remaining": state.repair_budget,
                        }
                        self.env.trace_recorder.event(
                            "recovery_triggered",
                            step=step_idx,
                            decision=rec_decision.decision,
                            reason=rec_decision.reason,
                        )
                        continue
                    else:
                        self.env.step_index += 1
                        self.env.last_primitive_results = []
                        self.env.record_failure(
                            ValueError(action_parse_error), "Action Format Error", "action_error"
                        )
                        break

                # Execute action in environment
                obs_before = obs
                obs, reward, done, info = self.env.step(action)
                obs_after = obs
                state.last_observation = obs_after

                # Runtime Verification Phase
                verif_result = self.verifier.verify(
                    before=obs_before,
                    action=action,
                    execution=info,
                    after=obs_after,
                )
                state.latest_verification = verif_result
                self.env.trace_recorder.event("runtime_verification", step=self.env.step_index,
                    verification=verif_result.model_dump(),
                    status="SUCCEEDED" if verif_result.passed else "FAILED",
                    latency_ms=int((time.monotonic() - step_start_time) * 1000))

                # Record history turn
                history_entry = {
                    "step": step_idx,
                    "action": action.to_dict() if hasattr(action, "to_dict") else action,
                    "verification": verif_result.model_dump(),
                }
                history.append(history_entry)

                public_feedback = {**self.human_feedback, "execution_status": "FAILED" if info.get("error") else "SUCCEEDED"}

                # Handle action format error retry if environment rejected action
                if done and self.env.termination_reason == "action_error" and state.repair_budget > 0:
                    state.repair_budget -= 1
                    state.total_repairs += 1
                    self.env.done = False
                    self.env.termination_reason = None
                    public_feedback = {"action_error": info.get("error"), "repair_remaining": state.repair_budget}
                    continue

                if done:
                    break

                if self._complete_after_export(action, info, obs_after, verif_result):
                    break

                if not self.automatic_recovery:
                    if not verif_result.passed:
                        self.env.done = True
                        self.env.termination_reason = "execution_error"
                        break
                    continue

                # Recovery Evaluation for other execution issues
                rec_decision = self.recovery_policy.decide(
                    verif_result, state, action_error=info.get("error")
                )
                state.latest_recovery = rec_decision
                if rec_decision.decision == "abort":
                    self.env.done = True
                    self.env.termination_reason = "execution_error"
                    break

                # Termination Evaluation
                is_stop = False
                if isinstance(action, dict):
                    is_stop = action.get("type") == "stop"
                elif hasattr(action, "type"):
                    is_stop = action.type == "stop"

                should_term, term_reason = self.termination_policy.evaluate(
                    state=state,
                    task_spec=self.env.task_spec,
                    is_stop_action=is_stop,
                    runtime_verification=verif_result,
                )

                if should_term and not is_stop:
                    self.env.done = True
                    self.env.termination_reason = term_reason
                    break

            else:
                self.env.record_failure(
                    RuntimeError("Maximum decisions reached without stop"),
                    "False Completion",
                    "max_steps",
                )
        except KeyboardInterrupt as error:
            self.env.record_failure(error, "Environment Error", "cancelled")
        except ModelAPIError as error:
            # No action exists: count the failed decision without dispatching or
            # attributing the previous step's primitives to the model failure.
            self.env.step_index += 1
            self.env.last_primitive_results = []
            self.env.record_failure(error, error.error_type, "model_error")
        except Exception as error:
            self.env.record_failure(error)
        finally:
            try:
                try:
                    final_results = self.env.evaluate_final()
                except Exception as error:
                    from diagagent.diagnosis.taxonomy import FailureCategory
                    report = DiagnosticReport(
                        task_id=task_spec.get("task_id", "invalid_task"),
                        failure_type="Evaluator Error",
                        responsibility=FailureCategory.EVALUATOR,
                        evidence={"error_message": str(error)},
                    )
                    self.env.trace_recorder.save_json("result.json", {
                        "run_id": self.env.run_id,
                        "task_id": report.task_id,
                        "success": False,
                        "task_status": "UNKNOWN",
                        "termination_reason": self.env.termination_reason or "internal_error",
                        "evaluation_error": str(error),
                    })
                    self.env.trace_recorder.save_diagnosis(report.model_dump())
                    self.env.trace_recorder.event("evaluation_error", error_message=str(error))
                    final_results = {"diagnostic_report": report}
            finally:
                self.env.close()

        return final_results["diagnostic_report"]
