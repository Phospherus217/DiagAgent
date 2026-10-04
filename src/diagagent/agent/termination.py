"""Termination policy defining explicit criteria for agent stopping and false completion detection."""

from typing import Any, Dict, Optional, Tuple
from diagagent.agent.state import RuntimeState


class TerminationPolicy:
    """Evaluates whether the agent execution loop should terminate."""

    def __init__(self, default_max_steps: int = 15):
        self.default_max_steps = default_max_steps

    def evaluate(
        self,
        state: RuntimeState,
        task_spec: Dict[str, Any],
        is_stop_action: bool = False,
        fatal_error: Optional[str] = None,
        runtime_verification: Optional[Any] = None,
    ) -> Tuple[bool, str]:
        """Returns (should_terminate, termination_reason)."""
        if fatal_error:
            return True, f"fatal_error: {fatal_error}"

        max_steps = task_spec.get("max_steps", self.default_max_steps)

        # Check budget exhaustion when a failure persists
        if runtime_verification and not getattr(runtime_verification, "passed", True):
            if (
                state.retry_budget <= 0
                and state.repair_budget <= 0
                and state.replan_budget <= 0
            ):
                return True, "budget_exhausted"

        if is_stop_action:
            # Model explicitly emitted 'stop'. Verify whether completion is genuine.
            # Check if an output artifact was expected and whether it was produced.
            expected_output = None
            if "initial_state" in task_spec and "output_file" in task_spec["initial_state"]:
                expected_output = task_spec["initial_state"]["output_file"]
            elif "success_criteria" in task_spec:
                criteria = task_spec["success_criteria"].get("final", {})
                if criteria.get("output_exists"):
                    expected_output = criteria.get("output_file", "output.png")

            if expected_output:
                # Check verification signals or artifact status
                output_ok = False
                if runtime_verification and hasattr(runtime_verification, "signals"):
                    output_ok = bool(runtime_verification.signals.get("output_file_exists"))
                if not output_ok and state.step <= 1:
                    # Model immediately stopped without taking any substantive action
                    return True, "false_completion"

            return True, "task_complete"

        if state.step >= max_steps:
            return True, "max_steps"

        return False, "running"
