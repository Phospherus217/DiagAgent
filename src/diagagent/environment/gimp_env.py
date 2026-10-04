"""GIMP execution boundary with audited primitives and failure finalization."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import time
import yaml

from diagagent.actions.parser import ActionParser
from diagagent.actions.coordinates import desktop_action, raster_action
from diagagent.actions.schema import BaseAction
from diagagent.actions.validator import ActionValidator
from diagagent.config import DesktopConfig
from diagagent.tasks import normalize_task, output_name, public_task
from diagagent.environment.base import BaseEnvironment
from diagagent.environment.errors import DesktopError
from diagagent.environment.gimp_window import GIMPWindowManager
from diagagent.environment.mock_gimp import MockGIMPBackend
from diagagent.environment.real_gimp import RealGIMPBackend
from diagagent.executor.gui_executor import GUIExecutor
from diagagent.executor.mock_executor import MockExecutor
from diagagent.lowering.lowering_engine import LoweringEngine
from diagagent.lowering.structural_lowering import StructuralLowerer, ATOMIC_TYPES
from diagagent.observation.collector import ObservationCollector
from diagagent.evaluator.dual_evaluator import DualEvaluator
from diagagent.evaluator.models import StepEvaluation, DiagnosticSignals
from diagagent.diagnosis.engine import DiagnosisEngine
from diagagent.trace.recorder import TraceRecorder
from diagagent.trace.models import RunSummary, TraceStepRecord


class GIMPEnvironment(BaseEnvironment):
    def __init__(self, backend="mock", run_root="runs", gimp_executable=None, config=None):
        self.config = config or DesktopConfig()
        self.backend_name = "real_gimp" if backend in {"real", "real_gimp"} else backend
        if self.backend_name == "mock":
            self.backend = MockGIMPBackend()
            self.executor = MockExecutor(self.backend)
        elif self.backend_name == "real_gimp":
            self.backend = RealGIMPBackend(gimp_executable, self.config)
            self.executor = GUIExecutor()
            self.executor.geometry_check = self._check_input_geometry
        else:
            raise ValueError(f"Unknown backend: {backend}")
        self.run_root = Path(run_root).resolve()
        self.trace_recorder = None
        self.lowering_engine = LoweringEngine()
        self.last_observation = None
        self.step_index = 0
        self.step_evaluations = []
        self.done = False
        self.termination_reason = None
        self.last_primitive_results = []
        self.agent_type = "unspecified"
        self.total_decisions = 0
        self.executed_primitives = 0
        self.model_call_count = 0
        self.model_usage = None

    def _check_input_geometry(self):
        contract = self.backend.desktop_geometry
        if contract is not None:
            contract.check("input_dispatch")

    def prepare(self, task_spec):
        if self.trace_recorder:
            return
        self.task_spec = task_spec
        task_id = str(task_spec.get("task_id", "invalid_task"))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
            task_id = "invalid_task"
        self.trace_recorder = TraceRecorder(self.run_root, task_id)
        self.run_id = self.trace_recorder.run_id
        self.start_time = datetime.now(timezone.utc).isoformat()
        self.start_clock = time.monotonic()
        self.deadline = self.start_clock + self.config.run_timeout
        self.dual_evaluator = DualEvaluator(task_spec)
        self.obs_collector = ObservationCollector(self.trace_recorder.run_dir)
        self.trace_recorder.event("run_started", backend=self.backend_name, agent_type=self.agent_type)
        self.trace_recorder.save_json("metadata.json", {
            "schema_version": "2.0", "backend": self.backend_name, "agent_type": self.agent_type,
            "evidence_kind": "real_gui" if self.backend_name == "real_gimp" else "mock",
            "config": self.config.model_dump(), "start_time": self.start_time,
            "trace_layout": "trace.jsonl semantic records + events.jsonl atomic lifecycle",
            "source_sha256": self.source_fingerprint(),
            "config_sha256": hashlib.sha256(json.dumps(self.config.model_dump(), sort_keys=True).encode()).hexdigest(),
            "task_sha256": hashlib.sha256(json.dumps(task_spec, sort_keys=True).encode()).hexdigest(),
        })
        if self.backend_name == "real_gimp":
            self.backend.run_dir = self.trace_recorder.run_dir
            self.backend.gui_execute = self._execute_primitive
            self.backend.audit = self._event
            self.backend.deadline = self.deadline

    @staticmethod
    def source_fingerprint():
        root = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256()
        for file in sorted(root.rglob("*.py")):
            digest.update(file.relative_to(root).as_posix().encode())
            digest.update(file.read_bytes())
        return digest.hexdigest()

    def _event(self, event, **data):
        self.trace_recorder.event(event, step=self.step_index, **data)

    def launch(self):
        self.backend.launch()

    def reset(self, task_spec):
        self.prepare(task_spec)
        self.task_spec = task_spec if task_spec.get("_normalized") else normalize_task(task_spec)
        self.dual_evaluator = DualEvaluator(self.task_spec)
        self.done = False
        self.step_evaluations = []
        initial = self.task_spec["initial_state"]
        self.trace_recorder.save_json("task_public.json", public_task(self.task_spec))
        (self.trace_recorder.run_dir / "task_spec.yaml").write_text(yaml.safe_dump(self.task_spec, allow_unicode=True), encoding="utf-8")
        self.trace_recorder.save_json("asset.json", {"input_file": initial["input_file"],
            "sha256": hashlib.sha256(Path(initial["input_file"]).read_bytes()).hexdigest()})
        size = self.config.window_size if self.backend_name == "real_gimp" else initial.get("window_size", [1280, 720])
        self.window_manager = GIMPWindowManager(self.backend, tuple(size))
        self.window_manager.fix_window()
        self.last_primitive_results = []
        self.launch()
        self.backend.open_image(initial["input_file"])
        if self.backend_name == "real_gimp":
            self.trace_recorder.save_json("desktop_preflight.json", self.backend.preflight_result)
        subgoals = self.task_spec.get("subgoals", [])
        self.step_index = 1 if subgoals and subgoals[0].get("id") == "open_image" else 0
        obs = self.observe("reset")
        evaluation = None
        if self.step_index == 1:
            evaluation = self.dual_evaluator.evaluate_step(1, {"type": "open_image"}, obs, obs, {"success": True})
            self.step_evaluations.append(evaluation.model_dump())
        self.trace_recorder.record_step(TraceStepRecord(step=self.step_index, phase="reset", obs_after=obs.to_dict(),
            step_eval=evaluation.model_dump() if evaluation else None,
            execution_result={"primitive_results": self.last_primitive_results, "effect_status": "PASS"}))
        return obs

    def _execute_primitive(self, action):
        raw_action = action.to_dict() if isinstance(action, BaseAction) else action
        self._event("primitive_received", raw_action=raw_action, source="backend_or_lowering")
        try:
            action = desktop_action(ActionParser.parse(action),
                                    getattr(self.backend, "screenshot_bbox", None))
        except ValueError as error:
            raise DesktopError(str(error), "UI Grounding Error") from error
        if action.type not in ATOMIC_TYPES:
            raise DesktopError(f"Non-atomic action reached GUI executor: {action.type}", "UI Grounding Error")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Run deadline exceeded before GUI dispatch")
        if getattr(action, "duration", 0) > self.deadline - time.monotonic():
            raise TimeoutError("Insufficient remaining budget for primitive duration")
        if len(self.last_primitive_results) >= self.config.max_primitives:
            raise DesktopError("Primitive budget exceeded", "UI Grounding Error")
        if self.backend_name == "real_gimp":
            import ctypes
            from diagagent.platform.windows.desktop_session import get_virtual_screen_bbox
            contract = self.backend.desktop_geometry
            bbox = (contract.check("input_validation").bbox if contract is not None
                    else get_virtual_screen_bbox())
            owned = self.backend.windows()
            self._event("coordinate_validation", coordinate_space="virtual_desktop",
                        desktop_bbox=list(bbox), action=action.to_dict(),
                        screenshot_bbox=getattr(self.backend, "screenshot_bbox", None),
                        owned_windows=[{"hwnd": w._hWnd, "bbox": [w.left, w.top, w.right, w.bottom]} for w in owned])
            # Check before and after rounding so an invalid point cannot round in,
            # and a valid fractional point cannot round out of the target window.
            for candidate in (action, raster_action(action)):
                valid, error = ActionValidator.validate(candidate, list(ATOMIC_TYPES), bbox)
                if not valid:
                    raise DesktopError(error, "UI Grounding Error")
                points = [(candidate.x, candidate.y)] if candidate.type == "click" else [candidate.start, candidate.end] if candidate.type == "drag" else []
                if any(not any(w.left <= x < w.right and w.top <= y < w.bottom for w in owned) for x, y in points):
                    raise DesktopError("Atomic target outside owned GIMP windows", "UI Grounding Error")
            action = raster_action(action)
            if action.type in {"hotkey", "type"} and ctypes.windll.user32.GetForegroundWindow() not in [w._hWnd for w in owned]:
                raise DesktopError("Keyboard dispatch requires an owned foreground GIMP window")
        else:
            bbox = tuple(self.backend.window_bbox)
        valid, error = ActionValidator.validate(action, list(ATOMIC_TYPES), bbox)
        if not valid:
            raise DesktopError(error, "Action Format Error")
        index = len(self.last_primitive_results)
        self._event("primitive_started", primitive_index=index, action=action.to_dict())
        result = self.executor.execute(action)
        self.executed_primitives += 1
        record = {"primitive_index": index, "action": action.to_dict(), **result.model_dump()}
        self.last_primitive_results.append(record)
        self._event("primitive_finished", **record)
        if not result.success:
            raise DesktopError(result.error_message or "Primitive execution failed", result.error_type or "Execution Error")
        return result

    def execute(self, action):
        return self.step(action)

    def step(self, action):
        self.step_index += 1
        self.last_primitive_results = []
        before = self.last_observation
        self._event("action_received", raw_action=action.to_dict() if isinstance(action, BaseAction) else action)
        try:
            obj = ActionParser.parse(action)
            obj = desktop_action(obj, getattr(before, "screenshot_bbox", None))
            valid, error = ActionValidator.validate(obj, self.task_spec.get("allowed_actions"),
                                                     tuple(self.backend.window_bbox))
            if not valid:
                raise ValueError(error)
            if obj.type == "export_file":
                name = output_name(obj.path)
                if name != self.task_spec["initial_state"].get("output_file", "output.png"):
                    raise ValueError("Output filename does not match this task's output handle")
                if obj.format.lower() != "png":
                    raise ValueError("Day 1 export supports PNG only")
        except Exception as error:
            self._event("action_validated", status="FAILED", error_type="Action Format Error", error_message=str(error))
            self.record_failure(error, "Action Format Error", "action_error")
            return before, 0.0, True, {"success": False, "error": str(error),
                                      "error_type": "Action Format Error"}
        self._event("action_validated", status="SUCCEEDED", parsed_action=obj.to_dict())
        if obj.type == "stop":
            self.done = True
            self.termination_reason = "agent_stop"
            after = self.observe("final")
            self.trace_recorder.record_step(TraceStepRecord(step=self.step_index, phase="final", action=obj.to_dict(),
                obs_before=before.to_dict() if before else None, obs_after=after.to_dict(), done=True))
            return after, 0.0, True, {}
        resolved = obj
        if obj.type == "export_file":
            resolved = obj.model_copy(update={"path": str(self.trace_recorder.artifacts_dir / obj.path)})
        ui_primitives = self.window_manager.ui_primitives()
        if before and before.ui_state.image_size:
            ui_primitives["image_size"] = list(before.ui_state.image_size)
            if self.backend_name == "real_gimp":
                # The fixed profile's canvas bbox includes rulers and
                # scrollbars.  At the calibrated 100% zoom, GIMP centers the
                # image inside that region; expose the derived image bbox so
                # image-coordinate crops land on the actual pixels.
                cx1, cy1, cx2, cy2 = ui_primitives["canvas_bbox"]
                iw, ih = map(float, before.ui_state.image_size)
                scale = min(1.0, (cx2 - cx1) / iw, (cy2 - cy1) / ih)
                dw, dh = iw * scale, ih * scale
                ui_primitives["image_bbox"] = [
                    cx1 + (cx2 - cx1 - dw) / 2.0,
                    cy1 + (cy2 - cy1 - dh) / 2.0,
                    cx1 + (cx2 - cx1 - dw) / 2.0 + dw,
                    cy1 + (cy2 - cy1 - dh) / 2.0 + dh,
                ]
                ui_primitives["zoom"] = scale
        # The current profile has no zoom widget probe yet; recording the
        # fixed-layout scope prevents a crop trace from being read as a
        # cross-layout coordinate guarantee.
        ui_primitives["layout_scope"] = "fixed"
        lowered = self.lowering_engine.lower(resolved, ui_primitives)
        if not lowered or len(lowered) > self.config.max_primitives:
            raise DesktopError("Empty or excessive lowering plan", "UI Grounding Error")
        if self.backend_name == "real_gimp":
            if obj.type not in {"resize_image", "export_file", "select_tool", "crop_canvas", "wait", "click", "type"}:
                raise DesktopError(f"Day 1 real profile does not support {obj.type}", "UI Grounding Error")
            structural = StructuralLowerer(self.backend)
            self.backend._action_before_size = list(before.ui_state.image_size) if before and before.ui_state.image_size else None
            self.backend._action_before_tool = before.ui_state.active_tool if before else None
            for low in lowered:
                self._event("structural_action", action=low.to_dict(), source="manual_profile")
                for primitive in structural.lower(low):
                    self._execute_primitive(primitive)
            effect = self.backend.verify_action(resolved)
            if obj.type in {"resize_image", "export_file"}:
                self._event("dialog_verification", dialog_open_before=bool(getattr(self.backend, "active_dialog", None)),
                            dialog_closed=effect.get("dialog_closed"),
                            downstream_state_changed=effect.get("downstream_state_changed"),
                            verification_source=effect.get("source"))
            self.backend.active_dialog = None
            self.backend.dialog_confirmed = True if effect["status"] == "PASS" else None
            changed = True if effect["status"] == "PASS" else None
            if obj.type == "resize_image" and before and before.ui_state.image_size:
                changed = list(before.ui_state.image_size) != effect.get("actual_size")
            if obj.type == "crop_canvas":
                changed = effect.get("state_changed")
            if obj.type == "select_tool":
                changed = effect.get("state_changed")
        else:
            prior = self._mock_state()
            for low in lowered:
                result = self.executor.execute(low)
                self.last_primitive_results.append({"action": low.to_dict(), **result.model_dump()})
                if not result.success:
                    raise DesktopError(result.error_message or "Mock execution failed", result.error_type or "Execution Error")
            self._apply_mock_state_update(resolved)
            changed = prior != self._mock_state()
            if obj.type == "export_file":
                changed = Path(resolved.path).is_file()
            effect = {"status": "PASS" if changed else "UNKNOWN", "source": "mock_internal"}
        exec_result = {"success": True, "action_executable": True, "state_changed": changed,
                       "effect_status": effect["status"], "effect_evidence": effect,
                       "primitive_results": self.last_primitive_results}
        after = self.observe()
        evaluation = self.dual_evaluator.evaluate_step(self.step_index, obj.to_dict(), before, after, exec_result)
        self.step_evaluations.append(evaluation.model_dump())
        self.trace_recorder.record_step(TraceStepRecord(step=self.step_index, action=obj.to_dict(),
            lowered_actions=[low.to_dict() for low in lowered], obs_before=before.to_dict() if before else None,
            obs_after=after.to_dict(), execution_result=exec_result, step_eval=evaluation.model_dump(), error_type=evaluation.error_type))
        return after, float(evaluation.subgoal_completed), self.done, {"step_eval": evaluation.model_dump(), "execution_result": exec_result}

    def _mock_state(self):
        image = self.backend.image
        return (image.size if image else None, hashlib.sha256(image.tobytes()).hexdigest() if image else None,
                self.backend.active_tool, self.backend.active_dialog)

    def _apply_mock_state_update(self, action):
        if action.type == "select_tool":
            self.backend.active_tool = action.tool_name
        elif action.type == "resize_image":
            self.backend.resize_image(action.width, action.height)
        elif action.type == "crop_canvas":
            self.backend.crop_canvas(action.region)
        elif action.type == "add_text":
            self.backend.add_text(action.text, action.position, color=action.color or "#000000")
        elif action.type == "apply_filter":
            self.backend.apply_filter(action.filter_name, action.parameters)
        elif action.type == "export_file":
            self.backend.export_file(action.path)

    def observe(self, kind="step"):
        obs = self.obs_collector.collect(self.step_index, self.backend, self.window_manager, kind,
                                        f"step_{self.step_index:03d}_{kind}.png")
        self.last_observation = obs
        return obs

    def record_failure(self, error, error_type=None, termination_reason=None):
        self.done = True
        error_type = error_type or getattr(error, "error_type", "Environment Error" if isinstance(error, (FileNotFoundError, TimeoutError)) else "Internal Error")
        self.termination_reason = termination_reason or ("run_timeout" if isinstance(error, TimeoutError) else
            "environment_error" if error_type in {"Environment Error", "Launch / Window Error"} else
            "model_error" if error_type == "Model Error" else "internal_error" if error_type == "Internal Error" else "execution_error")
        self._event("run_error", error_type=error_type, error_message=str(error))
        obs = None
        try:
            if hasattr(self, "window_manager"):
                obs = self.observe("failure")
        except Exception as capture_error:
            self._event("failure_screenshot_unavailable", reason=str(capture_error))
        evaluation = StepEvaluation(step=self.step_index, subgoal_id="execution", subgoal_completed=False, status="FAIL",
            diagnostic_signals=DiagnosticSignals(action_executable=False, subgoal_completed=False),
            error_type=error_type, evidence={"error_message": str(error)}, note=str(error))
        self.step_evaluations.append(evaluation.model_dump())
        self.trace_recorder.record_step(TraceStepRecord(step=self.step_index, phase="failure", done=True,
            obs_after=obs.to_dict() if obs else None, error_type=error_type, step_eval=evaluation.model_dump(),
            execution_result={"success": False, "error_type": error_type, "error_message": str(error),
                              "primitive_results": self.last_primitive_results}))

    def evaluate_final(self):
        name = output_name(self.task_spec.get("initial_state", {}).get("output_file", "output.png"))
        artifact = self.trace_recorder.artifacts_dir / name
        artifact_eval = self.dual_evaluator.evaluate_artifact(artifact)
        self.artifact_eval_result = artifact_eval
        self._event("artifact_evaluated", artifact_evaluation=artifact_eval.model_dump(),
                    status=artifact_eval.status, artifact_path=artifact.relative_to(self.trace_recorder.run_dir).as_posix())
        report = DiagnosisEngine.diagnose(self.task_spec, self.step_evaluations, artifact_eval.model_dump())
        report.termination_reason = self.termination_reason or "max_steps"
        if self.termination_reason not in {"agent_stop", "completed"} and report.task_status == "PASS":
            report.task_status = "UNKNOWN" if self.termination_reason in {"environment_error", "model_error", "internal_error"} else "FAIL"
            report.success = False
        report = DiagnosisEngine.enrich(report, self.trace_recorder.step_records, self.trace_recorder.events)
        summary = RunSummary(task_id=self.task_spec.get("task_id", "invalid_task"), run_id=self.run_id,
            success=report.success, total_steps=len(self.step_evaluations), first_failure_step=report.first_failure_step,
            first_failure_type=report.failure_type, responsibility=report.responsibility.value if report.responsibility else None,
            duration_seconds=time.monotonic() - self.start_clock, start_time=self.start_time,
            end_time=datetime.now(timezone.utc).isoformat(), artifact_path=artifact.relative_to(self.trace_recorder.run_dir).as_posix() if artifact.exists() else None,
            backend=self.backend_name, termination_reason=self.termination_reason or "max_steps",
            total_decisions=self.total_decisions, process_status=report.process_status,
            executed_primitives=self.executed_primitives, model_call_count=self.model_call_count,
            model_usage=self.model_usage, estimated_cost=None,
            has_observed_failure=report.has_observed_failure,
            task_status=report.task_status, artifact_status=artifact_eval.status)
        self.trace_recorder.save_json("evaluation.json", {"evaluator_version": "v2-evidence", "steps": self.step_evaluations,
            "predicates": self.dual_evaluator.step_evaluator.predicates,
                                                         "artifact": artifact_eval.model_dump()})
        self.trace_recorder.save_result(summary)
        self.trace_recorder.save_diagnosis(report.model_dump())
        self._event("run_finished", success=report.success, termination_reason=summary.termination_reason)
        return {"summary": summary.model_dump(), "artifact_evaluation": artifact_eval.model_dump(), "diagnostic_report": report}

    def close(self):
        self.backend.close()
