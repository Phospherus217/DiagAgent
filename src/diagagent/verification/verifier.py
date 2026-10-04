"""RuntimeVerifier assessing factual execution feedback without accessing benchmark gold."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from diagagent.verification.models import VerificationResult


class RuntimeVerifier:
    """Verifies observable execution facts from before/after observations and system events."""

    def verify(
        self,
        before: Optional[Any],
        action: Optional[Any],
        execution: Optional[Any],
        after: Optional[Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """Evaluates observable signals without leaking offline evaluation gold."""
        signals: Dict[str, Any] = {}
        evidence: List[str] = []
        suspected_failure: Optional[str] = None
        passed = True

        # 1. Action & Schema check
        action_type = ""
        if isinstance(action, dict):
            action_type = action.get("type", "")
        elif hasattr(action, "type"):
            action_type = getattr(action, "type", "")

        signals["action_type"] = action_type

        # 2. Execution result check
        exec_success = True
        exec_error = None
        state_changed = True

        if execution is not None:
            if isinstance(execution, dict):
                nested = execution.get("execution_result")
                payload = nested if isinstance(nested, dict) else execution
                exec_error = execution.get("error") or payload.get("error") or payload.get("error_message")
                exec_success = payload.get("success", not bool(exec_error))
                state_changed = payload.get("state_changed", False if exec_error else True)
            else:
                exec_success = getattr(execution, "success", True)
                exec_error = getattr(execution, "error", None)
                state_changed = getattr(execution, "state_changed", True)

        signals["execution_success"] = exec_success
        signals["state_changed"] = state_changed

        if not exec_success:
            passed = False
            suspected_failure = execution.get("error_type", "Execution Error") if isinstance(execution, dict) else "Execution Error"
            evidence.append(f"Execution returned failure: {exec_error}")

        # 3. Detect Execution No-op: execution claimed success, but state did not change
        if exec_success and not state_changed and action_type not in ("wait", "stop", ""):
            passed = False
            suspected_failure = "Execution No-op"
            evidence.append(f"Action '{action_type}' completed without measurable GUI or state change.")

        # 4. Check Dialog Signals
        if action_type in ("open_menu", "set_dialog_field", "click_dialog_button"):
            before_dialog = getattr(before, "active_dialog", None) if before else None
            after_dialog = getattr(after, "active_dialog", None) if after else None
            signals["before_dialog"] = before_dialog
            signals["after_dialog"] = after_dialog

            if action_type == "open_menu" and not after_dialog:
                # Menu was supposed to open a dialog or subview
                evidence.append("Dialog did not appear after menu activation.")

        # 5. Check Output File Signal
        if action_type in ("export_file", "export_image", "export_png"):
            output_path = None
            if isinstance(action, dict):
                output_path = action.get("output_path") or action.get("filepath")
            elif hasattr(action, "output_path"):
                output_path = getattr(action, "output_path", None)

            if output_path:
                file_exists = Path(output_path).exists()
                signals["output_file_exists"] = file_exists
                if file_exists:
                    file_size = Path(output_path).stat().st_size
                    signals["output_file_size"] = file_size
                    evidence.append(f"Output artifact verified on disk ({file_size} bytes).")
                else:
                    passed = False
                    suspected_failure = "File I/O Error"
                    evidence.append(f"Exported artifact not found on disk: {output_path}")

        # 6. Check window focus
        if after and hasattr(after, "window_focused"):
            signals["window_focused"] = after.window_focused
            if not after.window_focused and action_type not in ("stop", ""):
                evidence.append("Target window lost focus during or after execution.")

        return VerificationResult(
            passed=passed,
            signals=signals,
            suspected_failure=suspected_failure,
            evidence=evidence,
        )
