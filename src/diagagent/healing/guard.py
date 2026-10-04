"""Pre-execution safety and evidence boundary for recovery plans."""

from typing import Any, Dict, Iterable, Optional

from diagagent.actions.parser import ActionParser
from diagagent.actions.validator import ActionValidator
from diagagent.schemas.recovery import RecoveryPolicyDecision, Repairability
from diagagent.tasks import output_name
from diagagent.lowering.lowering_engine import LoweringEngine


class GuardViolation(ValueError):
    """Raised when a repair plan must not execute."""

    def __init__(self, message: str, code: str = "INVALID_ACTION"):
        super().__init__(message)
        self.code = code


class HealingGuard:
    def __init__(self, max_recovery_attempts: int = 1):
        if max_recovery_attempts < 1:
            raise ValueError("max_recovery_attempts must be at least one")
        self.max_recovery_attempts = max_recovery_attempts

    def check(self, **kwargs) -> Dict[str, Any]:
        """Machine-readable view; validate remains the enforcing boundary."""
        try:
            self.validate(**kwargs)
        except GuardViolation as error:
            return {"decision": "DENY", "code": error.code, "reason": str(error)}
        return {"decision": "ALLOW", "code": "ALLOW", "reason": "Repair satisfies the task and attempt constraints."}

    def validate(self, *, diagnosis: Any, evidence_graph: Dict[str, Any],
                 decision: RecoveryPolicyDecision, task_spec: Dict[str, Any],
                 current_state: Optional[Dict[str, Any]] = None,
                 existing_attempts: int = 0, human_approved: Optional[bool] = None) -> None:
        data = diagnosis.model_dump() if hasattr(diagnosis, "model_dump") else dict(diagnosis or {})
        diagnosis_id = data.get("diagnosis_id")
        step = data.get("first_failure_step") if data.get("first_failure_step") is not None else data.get("step")
        if not diagnosis_id:
            raise GuardViolation("diagnosis_id is required")
        if step is None or type(step) is not int or step < 0:
            raise GuardViolation("first_failure_step is required")
        if decision.repairability != Repairability.REPAIRABLE:
            raise GuardViolation("repairability does not permit execution")
        node_ids = {node.get("id") for node in evidence_graph.get("nodes", []) if node.get("id")}
        if not decision.evidence_node_ids or not set(decision.evidence_node_ids).issubset(node_ids):
            raise GuardViolation("repair plan must reference supporting evidence graph nodes")
        if decision.target_step is None or decision.target_step != step:
            raise GuardViolation("repair target must match the diagnosed first failure step", "WRONG_TARGET_STEP")
        if existing_attempts >= self.max_recovery_attempts:
            raise GuardViolation("recovery attempt budget exhausted", "BUDGET_EXCEEDED")
        if decision.repair_action is None:
            raise GuardViolation("repair_action is required")
        if decision.requires_human_approval and human_approved is not True:
            raise GuardViolation("human approval is required before execution")
        allowed = task_spec.get("allowed_actions")
        try:
            parsed = ActionParser.parse(decision.repair_action)
        except Exception as error:
            raise GuardViolation(f"repair action schema rejected: {error}") from error
        if allowed is not None and parsed.type not in allowed:
            raise GuardViolation("repair action is outside allowed_actions", "OUT_OF_SCOPE")
        bbox = (current_state or {}).get("window_bbox")
        valid, error = ActionValidator.validate(parsed, allowed, window_bbox=bbox)
        if not valid:
            raise GuardViolation(f"repair action rejected by action contract: {error}",
                                 "OUT_OF_SCOPE" if "outside window bbox" in str(error) else "INVALID_ACTION")
        try:
            lowered = LoweringEngine().lower(parsed, ui_primitives=current_state or {})
        except Exception as error:
            raise GuardViolation(f"repair action cannot be lowered: {error}") from error
        if not lowered:
            raise GuardViolation("repair action lowered to an empty execution plan")
        if parsed.type == "export_file":
            initial = (task_spec.get("initial_state") or {}).get("output_file")
            try:
                path_name = output_name(parsed.path)
                initial_name = output_name(initial) if initial else None
            except ValueError as error:
                raise GuardViolation("repair export path is outside the original output handle", "INVALID_OUTPUT_PATH") from error
            if initial_name and path_name != initial_name:
                raise GuardViolation("repair export path is outside the original output handle", "INVALID_OUTPUT_PATH")
        if decision.forbidden_side_effects:
            # These are declarative constraints; actual violations are evaluated
            # independently after execution. A plan cannot silently waive them.
            if any(not isinstance(item, str) or not item for item in decision.forbidden_side_effects):
                raise GuardViolation("forbidden side effects must be named strings")
