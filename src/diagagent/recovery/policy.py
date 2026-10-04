"""Bounded recovery policy implementing the 4 core recovery rules (R1-R4) with loop prevention."""

from typing import Any, Dict, Optional
from diagagent.agent.state import RuntimeState
from diagagent.recovery.models import RecoveryDecision
from diagagent.verification.models import VerificationResult


class RecoveryPolicy:
    """Decides automated, bounded recovery actions based on runtime verification results."""

    def __init__(
        self,
        max_format_repairs: int = 1,
        max_action_retries: int = 1,
        max_replans: int = 2,
    ):
        self.max_format_repairs = max_format_repairs
        self.max_action_retries = max_action_retries
        self.max_replans = max_replans

    def decide(
        self,
        verification: VerificationResult,
        state: RuntimeState,
        action_error: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> RecoveryDecision:
        """Determines recovery strategy while enforcing budget bounds."""
        if verification.passed and not action_error:
            return RecoveryDecision(decision="continue", reason="Step passed verification.")

        suspected = verification.suspected_failure or "Action Error"

        # R1: Action Format Error
        if action_error or suspected in ("Action Format Error", "Parse Error", "Format Error"):
            if state.repair_budget > 0:
                state.repair_budget -= 1
                state.total_repairs += 1
                return RecoveryDecision(
                    decision="repair_format",
                    reason=f"Action format invalid: {action_error or 'JSON syntax error'}",
                    action_hint="Format output strictly as single JSON action object",
                    consumed_budget="repair_budget",
                    feedback_for_model={
                        "error_type": "Format Error",
                        "error_message": action_error or "Invalid JSON action format",
                        "repair_remaining": state.repair_budget,
                    },
                )
            else:
                return RecoveryDecision(
                    decision="abort",
                    reason="Format repair budget exhausted.",
                    consumed_budget="repair_budget",
                )

        # R2: Execution No-op Error
        if suspected == "Execution No-op":
            if state.retry_budget > 0:
                state.retry_budget -= 1
                state.total_retries += 1
                return RecoveryDecision(
                    decision="reobserve",
                    reason="Action produced no GUI state change; triggering re-observation and retry.",
                    action_hint="Re-examine current window state and retry action",
                    consumed_budget="retry_budget",
                    feedback_for_model={
                        "error_type": "Execution No-op",
                        "warning": "Previous action executed but caused no observable change.",
                        "retries_remaining": state.retry_budget,
                    },
                )
            else:
                return RecoveryDecision(
                    decision="abort",
                    reason="No-op retry budget exhausted.",
                    consumed_budget="retry_budget",
                )

        # R3: Dialog Error (missing dialog or lost focus)
        if suspected in ("Dialog Operation Error", "Dialog Missing", "Focus Lost"):
            if state.retry_budget > 0:
                state.retry_budget -= 1
                state.total_retries += 1
                return RecoveryDecision(
                    decision="reopen_dialog",
                    reason="Expected dialog was not active or focus was lost.",
                    action_hint="Refocus target window or reopen required dialog",
                    consumed_budget="retry_budget",
                    feedback_for_model={
                        "error_type": "Dialog Operation Error",
                        "warning": "Dialog interaction missed; window refocus required.",
                        "retries_remaining": state.retry_budget,
                    },
                )
            else:
                return RecoveryDecision(
                    decision="abort",
                    reason="Dialog recovery retry budget exhausted.",
                    consumed_budget="retry_budget",
                )

        # R4: Parameter Error
        if suspected in ("Parameter Error", "Invalid Parameter"):
            if state.replan_budget > 0:
                state.replan_budget -= 1
                state.total_replans += 1
                return RecoveryDecision(
                    decision="replan",
                    reason="Action parameter invalid or out of allowed bounds.",
                    action_hint="Adjust parameters to match task instruction and image dimensions",
                    consumed_budget="replan_budget",
                    feedback_for_model={
                        "error_type": "Parameter Error",
                        "warning": "Parameters were rejected; please re-evaluate constraints.",
                        "replans_remaining": state.replan_budget,
                    },
                )
            else:
                return RecoveryDecision(
                    decision="abort",
                    reason="Replan budget exhausted.",
                    consumed_budget="replan_budget",
                )

        # Default fallback if budget is still available
        if state.retry_budget > 0:
            state.retry_budget -= 1
            state.total_retries += 1
            return RecoveryDecision(
                decision="retry",
                reason=f"Retrying step after unclassified execution issue: {verification.evidence}",
                consumed_budget="retry_budget",
            )

        return RecoveryDecision(
            decision="abort",
            reason="Unrecoverable error or all recovery budgets exhausted.",
        )
