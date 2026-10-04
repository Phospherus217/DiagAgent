"""Diagnosis engine generating comprehensive diagnostic reports and recommendations."""

from typing import Any, Dict, List, Optional

from diagagent.diagnosis.first_failure import FirstFailureLocator
from diagagent.diagnosis.report import DiagnosticReport, StepExecutionSummary
from diagagent.diagnosis.taxonomy import get_responsibility


class DiagnosisEngine:
    """Core diagnostic engine analyzing execution traces and evaluating agent behavior."""

    @classmethod
    def diagnose_bundle(cls, bundle):
        """Diagnose a :class:`TraceBundle` produced by ``load_trace``.

        This is the v0.2 integration point between trace intelligence and the
        existing diagnosis engine. It only consumes persisted public evidence.
        """
        from diagagent.diagnosis.classifier import classify_bundle
        return classify_bundle(bundle)

    @classmethod
    def enrich(cls, report, trace, events=()):
        from diagagent.diagnosis.classifier import classify
        return report.model_copy(update=classify(trace, events, report.model_dump()))

    @classmethod
    def diagnose_trace(cls, task_spec, trace, artifact_eval=None, events=()):
        """Diagnose persisted trace and runtime verification without altering evaluation."""
        evaluations = [row["step_eval"] for row in trace if row.get("step_eval")]
        report = cls.diagnose(task_spec, evaluations, artifact_eval)
        return cls.enrich(report, trace, events)

    @classmethod
    def generate_recommendation(cls, failure_type: Optional[str], evidence: Dict[str, Any]) -> Optional[str]:
        if not failure_type:
            return None

        if failure_type == "Authentication Error":
            return "Check model API authentication and endpoint configuration."
        if failure_type == "Model API Error":
            return "Inspect the model_error trace event and model service availability."
        if failure_type == "Parameter Error":
            return "Verify action parameters (dimensions, coordinates, or font sizes) against the task instruction."
        if failure_type == "Tool Selection Error":
            expected = evidence.get("expected_tool", "the required tool")
            return f"Select {expected} prior to performing canvas operations."
        if failure_type == "Canvas Target Error":
            return "Ensure click or drag coordinates fall within the canvas bounding box."
        if failure_type == "Action Format Error":
            return "Ensure output JSON conforms strictly to the specified ActionSchema."
        if failure_type == "UI Grounding Error":
            return "Verify UI button/menu coordinates and ensure target elements are currently visible."
        if failure_type == "Dialog Operation Error":
            return "Ensure dialog is open and confirm input fields before closing."
        if failure_type == "Execution No-op Error":
            return "Ensure the action creates a detectable state difference on the active layer or canvas."
        if failure_type == "File I/O Error":
            return "Check destination export path exists and has valid write permissions."
        if failure_type == "Artifact Error":
            return "Check visual quality, dimensions, and compression format of exported artifact."
        if failure_type == "False Completion":
            return "Do not trigger 'stop' action before all subgoals and export operations have verified success."

        return "Review agent execution trajectory and prompt specifications."

    @classmethod
    def diagnose(
        cls,
        task_spec: Dict[str, Any],
        step_evaluations: List[Dict[str, Any]],
        artifact_eval: Optional[Dict[str, Any]] = None,
    ) -> DiagnosticReport:
        """Runs failure diagnosis across all steps and produces a DiagnosticReport."""
        task_id = task_spec.get("task_id", "unknown_task")
        instruction = task_spec.get("instruction", "")

        first_fail_info = FirstFailureLocator.locate(
            step_evaluations=step_evaluations,
            artifact_eval=artifact_eval,
            task_spec=task_spec,
        )

        first_failure_step = first_fail_info.get("first_failure_step")
        failure_type = first_fail_info.get("failure_type")
        evidence = first_fail_info.get("evidence", {})
        subgoal = first_fail_info.get("first_failure_subgoal")

        from diagagent.diagnosis.first_failure import step_status
        def merge(values):
            return "FAIL" if "FAIL" in values else "UNKNOWN" if not values or "UNKNOWN" in values else "PASS"
        process_status = merge([step_status(s) for s in step_evaluations])
        artifact_status = (artifact_eval or {}).get("status") or (
            "PASS" if (artifact_eval or {}).get("artifact_pass") is True else
            "FAIL" if (artifact_eval or {}).get("artifact_pass") is False else "UNKNOWN")
        if str(task_spec.get("schema_version")) == "2.0":
            # Latest predicate state determines completion; process history remains immutable.
            latest = {s.get("subgoal_id"): step_status(s) for s in step_evaluations}
            required = task_spec.get("evaluation", {}).get("required_subgoals", [])
            task_status = merge([artifact_status] + [latest.get(p, "UNKNOWN") for p in required])
        else:
            task_status = merge([process_status, artifact_status])
        is_success = task_status == "PASS"

        resp = get_responsibility(failure_type) if failure_type else None
        rec = cls.generate_recommendation(failure_type, evidence)

        # Build step summaries for execution breakdown
        step_summaries = []
        for i, step_row in enumerate(step_evaluations):
            s_idx = step_row.get("step", i + 1)
            sg_id = step_row.get("subgoal_id") or f"step_{s_idx}"
            s_completed = step_row.get("subgoal_completed", True)
            s_err = step_row.get("error_type")
            step_summaries.append(
                StepExecutionSummary(
                    step=s_idx,
                    name=sg_id.replace("_", " "),
                    status=step_status(step_row),
                    error_type=s_err,
                    note=step_row.get("note"),
                )
            )

        return DiagnosticReport(
            task_id=task_id,
            task_instruction=instruction,
            success=is_success,
            task_status=task_status, process_status=process_status, artifact_status=artifact_status,
            has_observed_failure=any(step_status(s) == "FAIL" for s in step_evaluations),
            attribution_status=first_fail_info.get("attribution_status", "unknown"),
            total_steps=len(step_evaluations),
            first_failure_step=first_failure_step,
            first_failure_subgoal=subgoal,
            failure_type=failure_type,
            failure_layer="MODEL" if failure_type in {"Authentication Error", "Model API Error"} else None,
            responsibility=resp,
            evidence=evidence,
            recommendation=rec,
            step_summaries=step_summaries,
        )
