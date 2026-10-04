"""StepEvaluator evaluates individual agent steps against declared subgoals and contracts."""

from typing import Any, Dict, List, Optional

from diagagent.actions.parser import ActionParser
from diagagent.actions.schema import BaseAction
from diagagent.actions.validator import ActionValidator
from diagagent.evaluator.models import DiagnosticSignals, StepEvaluation
from diagagent.observation.models import Observation


class StepEvaluator:
    """Evaluates fine-grained execution steps against task subgoals."""

    def __init__(self, task_spec: Dict[str, Any]):
        self.task_spec = task_spec
        self.subgoals = sorted(task_spec.get("subgoals", []), key=lambda item: item.get("order", 0))
        self.predicates = {}

    def evaluate_smoke(self, step, action, before, after, execution):
        """Action-linked predicates; waiting and retries never shift goals."""
        kind = action.get("type")
        status, error, evidence = "UNKNOWN", None, dict(execution.get("effect_evidence") or {})
        predicate = {"open_image": "image_loaded", "resize_image": "resize_effect",
                     "export_file": "artifact_exported"}.get(kind, kind)
        if kind == "open_image":
            loaded = after.ui_state.image_loaded if after else None
            status = "PASS" if loaded else "FAIL" if loaded is False else "UNKNOWN"
            error = "Environment Error" if status == "FAIL" else None
        elif kind in {"wait", "stop"}:
            status = "PASS"
        elif execution.get("success") is False:
            status, error = "FAIL", execution.get("error_type")
            evidence["error_message"] = execution.get("error_message")
        elif kind == "resize_image":
            expected = self.task_spec["success_criteria"]["final"].get("output_size")
            requested = [action.get("width"), action.get("height")]
            actual = list(after.ui_state.image_size) if after and after.ui_state.image_size else None
            prior = list(before.ui_state.image_size) if before and before.ui_state.image_size else None
            evidence.update(expected_size=expected, requested_size=requested, actual_size=actual,
                            before_size=prior, source=getattr(after, "state_source", "unavailable"))
            correct = requested == expected
            self.predicates["resize_requested_correctly"] = {
                "status": "PASS" if correct else "FAIL", "step": step, "evidence": evidence.copy()}
            if not correct:
                status, error = "FAIL", "Parameter Error"
            elif actual == requested:
                status = "PASS"
            elif actual is not None:
                status = "FAIL"
                error = "Execution No-op Error" if actual == prior and prior != requested else "Execution Error"
        elif kind == "export_file":
            status = execution.get("effect_status", "UNKNOWN")
            error = execution.get("error_type") if status == "FAIL" else None
        if predicate not in {"wait", "stop"}:
            self.predicates[predicate] = {"status": status, "step": step, "evidence": evidence.copy()}
        return StepEvaluation(step=step, subgoal_id=predicate, status=status,
            subgoal_completed=status == "PASS", error_type=error, evidence=evidence,
            diagnostic_signals=DiagnosticSignals(parameter_match=error != "Parameter Error",
                state_changed=execution.get("state_changed"), subgoal_completed=status == "PASS"))

    def get_subgoal_for_step(self, step_index: int) -> Optional[Dict[str, Any]]:
        """Maps 1-indexed step number to declared task subgoal."""
        if not self.subgoals:
            return None
        idx = max(0, min(step_index - 1, len(self.subgoals) - 1))
        return self.subgoals[idx]

    def evaluate_step(
        self,
        step_index: int,
        action: Any,
        obs_before: Optional[Observation] = None,
        obs_after: Optional[Observation] = None,
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> StepEvaluation:
        """Evaluates an execution step and returns structured StepEvaluation."""
        raw = action.to_dict() if hasattr(action, "to_dict") else action
        if str(self.task_spec.get("schema_version")) == "2.0":
            return self.evaluate_smoke(step_index, raw, obs_before, obs_after, execution_result or {})
        subgoal = self.get_subgoal_for_step(step_index)
        subgoal_id = subgoal.get("id") if subgoal else f"step_{step_index}"
        subgoal_type = subgoal.get("type") if subgoal else None

        allowed_actions = self.task_spec.get("allowed_actions")
        window_bbox = obs_before.ui_primitives.window_bbox if obs_before else None

        # 0. Handle initial open_image check
        if subgoal_id == "open_image" or subgoal_type == "ui_state":
            image_loaded = obs_after.ui_state.image_loaded if obs_after else True
            return StepEvaluation(
                step=step_index,
                subgoal_id=subgoal_id,
                subgoal_completed=image_loaded,
                diagnostic_signals=DiagnosticSignals(subgoal_completed=image_loaded),
                error_type=None if image_loaded else "Environment Error",
                note="Input image is loaded in GIMP canvas." if image_loaded else "Failed to load input image",
            )

        # Convert action to BaseAction or dict
        if isinstance(action, dict):
            try:
                action_obj = ActionParser.parse(action)
            except Exception as e:
                return StepEvaluation(
                    step=step_index,
                    subgoal_id=subgoal_id,
                    subgoal_completed=False,
                    diagnostic_signals=DiagnosticSignals(action_valid=False, subgoal_completed=False),
                    error_type="Action Format Error",
                    note=f"Action parsing failed: {e}",
                    evidence={"parse_error": str(e)},
                )
        else:
            action_obj = action

        action_dict = action_obj.to_dict() if hasattr(action_obj, "to_dict") else dict(action)
        action_type = action_dict.get("type", "")

        # 1. Action validation
        action_valid, val_err = ActionValidator.validate(action_dict, allowed_actions, window_bbox)

        # 2. Execution executable & state changed
        exec_res = execution_result or {}
        action_executable = bool(exec_res.get("action_executable", True))
        state_changed = exec_res.get("state_changed")

        # 3. Tool match
        tool_match = True
        evidence = {}
        if subgoal_type == "tool_selection" or "tool" in subgoal_id:
            expected_tool = (subgoal.get("expected_state") or {}).get("active_tool") or subgoal.get("expected_tool")
            actual_tool = action_dict.get("tool_name")
            if obs_after and obs_after.ui_state.active_tool:
                actual_tool = obs_after.ui_state.active_tool
            if expected_tool and actual_tool and str(expected_tool).lower() != str(actual_tool).lower():
                tool_match = False
                evidence["expected_tool"] = expected_tool
                evidence["actual_tool"] = actual_tool

        # 4. Parameter match
        parameter_match = True
        if subgoal_type == "parameter" or "resize" in subgoal_id or "blur" in subgoal_id:
            expected_state = subgoal.get("expected_state") or {}
            if "width" in expected_state and action_dict.get("width") is not None and action_dict.get("width") != expected_state["width"]:
                parameter_match = False
                evidence["expected_width"] = expected_state["width"]
                evidence["actual_width"] = action_dict.get("width")
            if "height" in expected_state and action_dict.get("height") is not None and action_dict.get("height") != expected_state["height"]:
                parameter_match = False
                evidence["expected_height"] = expected_state["height"]
                evidence["actual_height"] = action_dict.get("height")

            expected_artifact_change = subgoal.get("expected_artifact_change") or {}
            if "radius" in expected_artifact_change:
                expected_radius = expected_artifact_change["radius"]
                actual_radius = (action_dict.get("parameters") or {}).get("radius") or action_dict.get("radius")
                if actual_radius is not None and actual_radius != expected_radius:
                    parameter_match = False
                    evidence["expected_radius"] = expected_radius
                    evidence["actual_radius"] = actual_radius

        # Check font_size in add_text
        if action_type == "add_text":
            expected_font_size = 32
            actual_font_size = action_dict.get("font_size", 32)
            subgoal_change = (subgoal.get("expected_artifact_change") or {}) if subgoal else {}
            if "font_size" in subgoal_change:
                expected_font_size = subgoal_change["font_size"]
            if actual_font_size != expected_font_size:
                parameter_match = False
                evidence["expected_font_size"] = expected_font_size
                evidence["actual_font_size"] = actual_font_size

        # 5. Target region hit
        target_hit = True
        if subgoal_type == "canvas_operation":
            if action_type == "add_text":
                pos = action_dict.get("position")
                if pos and obs_before:
                    cbox = obs_before.ui_primitives.canvas_bbox
                    if not (cbox[0] <= pos[0] <= cbox[2] and cbox[1] <= pos[1] <= cbox[3]):
                        target_hit = False
                        evidence["target_canvas_bbox"] = list(cbox)
                        evidence["actual_position"] = list(pos)

        # 6. Dialog confirmation
        dialog_confirmed = True
        if obs_after and obs_after.ui_state.dialog_confirmed is False:
            dialog_confirmed = False

        # Determine error attribution
        error_type = None
        note = None
        subgoal_completed = True

        if not action_valid:
            error_type = "Action Format Error"
            note = val_err or "Action schema validation failed"
            subgoal_completed = False
        elif not action_executable:
            if action_type == "export_file":
                error_type = "File I/O Error"
            else:
                error_type = "Dialog Operation Error"
            note = exec_res.get("error_message", "Action was not executable in current GUI state")
            subgoal_completed = False
        elif not tool_match:
            error_type = "Tool Selection Error"
            note = f"Tool mismatch: expected {evidence.get('expected_tool')}, got {evidence.get('actual_tool')}"
            subgoal_completed = False
        elif not parameter_match:
            error_type = "Parameter Error"
            note = f"Parameter mismatch: {evidence}"
            subgoal_completed = False
        elif not target_hit:
            error_type = "Canvas Target Error"
            note = f"Action coordinates fell outside target region: {evidence}"
            subgoal_completed = False
        elif not dialog_confirmed:
            error_type = "Dialog Operation Error"
            note = "Required dialog operation was not confirmed"
            subgoal_completed = False
        elif state_changed is False and action_type not in {"wait", "stop"} and exec_res.get("effect_status") != "PASS":
            error_type = "Execution No-op Error"
            note = "Action executed but no canvas or GUI state change was observed"
            subgoal_completed = False
        elif action_type == "stop":
            subgoal_completed = True

        signals = DiagnosticSignals(
            action_valid=action_valid,
            action_executable=action_executable,
            target_hit=target_hit,
            tool_match=tool_match,
            parameter_match=parameter_match,
            dialog_confirmed=dialog_confirmed,
            state_changed=state_changed,
            subgoal_completed=subgoal_completed,
        )

        return StepEvaluation(
            step=step_index,
            subgoal_id=subgoal_id,
            subgoal_completed=subgoal_completed,
            diagnostic_signals=signals,
            error_type=error_type,
            evidence=evidence,
            note=note,
        )
