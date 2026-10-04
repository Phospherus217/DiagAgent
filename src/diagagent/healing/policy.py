"""Conservative recovery policy mapping diagnoses to five v1.0 strategies."""

from typing import Any, Dict, Optional

from diagagent.schemas.recovery import (
    RecoveryPolicyDecision, RecoveryPolicyInput, Repairability, VerificationContract,
)


SUPPORTED = {
    "Parameter Error": ("parameter_update", "resume_from_failure"),
    "Tool Selection Error": ("tool_reselect", "resume_from_failure"),
    "Canvas Target Error": ("target_recompute", "resume_from_checkpoint"),
    "Dialog Operation Error": ("dialog_recovery", "resume_from_failure"),
    "Dialog Error": ("dialog_recovery", "resume_from_failure"),
    "File I/O Error": ("file_io_recovery", "resume_from_failure"),
}


class RecoveryPolicy:
    """Produces a proposal; it never executes an action or declares success."""

    def decide(self, policy_input: RecoveryPolicyInput) -> RecoveryPolicyDecision:
        # Keep the public policy boundary ergonomic while preserving the
        # versioned schema internally.  Existing callers often pass a plain
        # mapping when composing offline diagnostics or CLI dry-runs.
        if isinstance(policy_input, dict):
            policy_input = RecoveryPolicyInput.model_validate(policy_input)
        diagnosis = policy_input.diagnosis
        data = diagnosis.model_dump() if hasattr(diagnosis, "model_dump") else dict(diagnosis or {})
        failure_type = data.get("failure_type") or data.get("type")
        step = data.get("first_failure_step") if data.get("first_failure_step") is not None else data.get("step")
        confidence = float(data.get("confidence_score", 0.0) or 0.0)
        graph = policy_input.evidence_graph or {}
        evidence_ids = [node.get("id") for node in graph.get("nodes", [])
                        if node.get("type") in {"Failure", "Action", "State"} and node.get("id")
                        and ((node.get("evidence") or {}).get("step") == step
                             or node.get("id") in {f"action_{step}", f"failure_{step}",
                                                   f"state_{step}_before", f"state_{step}_after"})]
        evidence_ids = evidence_ids[:8]
        if not failure_type or type(step) is not int or step < 0 or not evidence_ids:
            return RecoveryPolicyDecision(
                repairability=Repairability.INSUFFICIENT_EVIDENCE,
                repairability_reason="A localized failure with supporting step evidence is required before repair.",
                target_step=step, evidence_node_ids=evidence_ids,
            )
        if failure_type not in SUPPORTED:
            return RecoveryPolicyDecision(
                repairability=Repairability.NON_REPAIRABLE if failure_type in {
                    "Window Error", "Launch / Window Error", "API Error", "Model API Error", "Evaluator Error"
                } else Repairability.HUMAN_REVIEW_REQUIRED,
                repairability_reason=f"No v1.0 automatic policy is defined for {failure_type}.",
                target_step=step, evidence_node_ids=evidence_ids, requires_human_approval=True,
            )
        repair_type, restart_mode = SUPPORTED[failure_type]
        feedback = policy_input.feedback
        action = self._feedback_action(feedback)
        if action is None:
            state_action = (policy_input.current_state or {}).get("repair_action")
            if isinstance(state_action, dict) and state_action.get("type"):
                action = state_action
        high_confidence = confidence >= 0.9
        # A concrete action can be autonomous only when it is explicit and reversible.
        state = policy_input.current_state
        responsibility = data.get("responsibility") if isinstance(data.get("responsibility"), dict) else {}
        alternatives = responsibility.get("alternatives", [])
        requires_approval = (not high_confidence or action is None or bool(alternatives)
                             or state.get("reversible") is not True
                             or state.get("low_risk") is not True or feedback is not None)
        checks = ["process_step_pass", "artifact", "task_success"]
        contract = VerificationContract(required_checks=checks,
            expected_state_change={}, expected_artifact_change={},
            forbidden_side_effects=["unexpected_crop", "unexpected_resize", "output_path_escape"])
        return RecoveryPolicyDecision(
            repairability=Repairability.REPAIRABLE,
            diagnosis_id=str(data.get("diagnosis_id", "")),
            repairability_reason="First failure and supported repair category are evidenced.",
            repair_type=repair_type, target_step=step, restart_mode=restart_mode,
            repair_action=action, evidence_node_ids=evidence_ids,
            requires_human_approval=requires_approval, verification_contract=contract,
            forbidden_side_effects=contract.forbidden_side_effects,
        )

    @staticmethod
    def _feedback_action(feedback: Any) -> Optional[Dict[str, Any]]:
        if feedback is None:
            return None
        value = feedback.model_dump() if hasattr(feedback, "model_dump") else dict(feedback)
        for key in ("repair_action", "action"):
            action = value.get(key)
            if isinstance(action, dict) and action.get("type"):
                return action
        instruction = value.get("repair_instruction")
        if isinstance(instruction, dict) and instruction.get("type"):
            return instruction
        return None
