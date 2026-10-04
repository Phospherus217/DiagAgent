import json

import pytest
from pathlib import Path

from diagagent.actions.schema import CropCanvasAction, SelectToolAction
from diagagent.environment.errors import DesktopError
from diagagent.experiments.reference_recovery import run_reference_case
from diagagent.experiments.recovery_readiness import assess_case
from diagagent.lowering.gimp_lowering import GIMPLowerer
from diagagent.lowering.structural_lowering import StructuralLowerer
from diagagent.healing.real_executor import RealGIMPRecoveryExecutor


class Backend:
    def __init__(self):
        self.window = None


class FakeEnv:
    backend_name = "real_gimp"
    trace_recorder = None

    def execute(self, action):
        return None, None, None, {"execution_result": {"success": True,
            "effect_evidence": {"status": "PASS"}}}

    def evaluate_final(self):
        return {"artifact_evaluation": {"artifact_pass": True}, "summary": {"success": True}}


def test_tool_recovery_uses_existing_lowering_and_atomic_shortcut():
    lowered = GIMPLowerer().lower(SelectToolAction(tool_name="Crop Tool"))
    assert [item.type for item in lowered] == ["click_toolbar_icon", "wait"]
    structural = StructuralLowerer(Backend()).lower(lowered[0])
    assert structural[0].type == "hotkey"
    assert structural[0].keys == ["shift", "c"]


def test_unknown_tool_is_rejected_by_real_structural_adapter():
    try:
        StructuralLowerer(Backend()).lower({"type": "click_toolbar_icon", "tool_name": "Unknown Tool"})
    except DesktopError as error:
        assert error.error_type == "UI Grounding Error"
    else:
        raise AssertionError("unsupported tool must not be executable")


def test_real_executor_adapter_reuses_environment_and_keeps_signals_independent():
    result = RealGIMPRecoveryExecutor(FakeEnv()).execute({"type": "resize_image", "width": 512, "height": 512})
    assert result["process_recovered"] is True
    assert result["artifact_recovered"] is True
    assert result["task_recovered"] is True


def test_canvas_recovery_maps_image_coordinates_with_fixed_layout_scope():
    lowered = GIMPLowerer().lower(
        CropCanvasAction(region=[0, 0, 512, 384], coordinate="image"),
        {"canvas_bbox": (220, 115, 996, 662), "image_size": [1024, 768], "layout_scope": "fixed"},
    )
    drag = next(action for action in lowered if action.type == "drag")
    assert drag.start == pytest.approx((243.2, 115.0), abs=0.2)
    assert drag.end == pytest.approx((607.85, 388.5), abs=0.2)


def test_canvas_recovery_prefers_calibrated_image_bbox():
    lowered = GIMPLowerer().lower(
        CropCanvasAction(region="center", coordinate="image"),
        {"canvas_bbox": (220, 115, 1156, 842), "image_bbox": (288, 178, 1088, 778),
         "image_size": [800, 600], "layout_scope": "fixed"},
    )
    drag = next(action for action in lowered if action.type == "drag")
    assert drag.start == pytest.approx((488, 328), abs=0.2)
    assert drag.end == pytest.approx((888, 628), abs=0.2)


def test_reference_probe_is_dry_run_and_persists_no_retry(tmp_path):
    path = run_reference_case({"case_id": "SH-TOOL", "failure_type": "Tool Selection Error",
                               "backend": "real_gimp", "expected_tool": "Crop Tool"}, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "DRY_RUN"
    assert payload["lowering_success"] is True
    assert payload["retry_used"] is False
    assert not payload["execution_success"]
    with pytest.raises(FileExistsError):
        run_reference_case({"case_id": "SH-TOOL", "failure_type": "Tool Selection Error",
                            "backend": "real_gimp", "expected_tool": "Crop Tool"}, tmp_path)


def test_dialog_reference_probe_records_semantic_and_atomic_lowering(tmp_path):
    path = run_reference_case({"case_id": "SH-DIALOG", "failure_type": "Dialog Error",
                               "backend": "real_gimp", "width": 512, "height": 512,
                               "reference_suffix": [{"type": "export_file", "path": "output.png", "format": "png"}]}, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    lowered = json.loads((path.parent / "lowered_actions.json").read_text(encoding="utf-8"))
    assert payload["lowering_success"] is True
    assert any(item["type"] == "set_dialog_field" for item in lowered["actions"])
    assert any(item["type"] in {"hotkey", "type"} for item in lowered["atomic_actions"])
    assert payload["retry_used"] is False


def test_readiness_does_not_promote_dry_run_to_ready(tmp_path):
    row = assess_case({"case_id": "SH-TOOL", "failure_type": "Tool Selection Error"}, tmp_path)
    assert row["status"] == "BACKEND_NOT_QUALIFIED"


def test_reference_audit_package_requires_independent_task_completion(tmp_path):
    from diagagent.experiments.reference_recovery import _persist_recovery_audit
    root = tmp_path / "SH-001"
    run = root / "run"
    (run / "artifacts").mkdir(parents=True)
    (run / "trace.jsonl").write_text("{}\n", encoding="utf-8")
    (run / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (run / "screenshots").mkdir()
    (run / "result.json").write_text(json.dumps({"run_id": "run", "task_id": "task", "success": False}), encoding="utf-8")
    (run / "evaluation.json").write_text(json.dumps({"artifact": {"artifact_pass": True}}), encoding="utf-8")
    result = {"run_dir": str(run), "verification_success": True, "artifact_pass": True}
    _persist_recovery_audit(root, {"case_id": "SH-001", "failure_type": "Parameter Error"}, result,
                            {"type": "resize_image", "width": 512, "height": 512}, {},
                            {"summary": {"run_id": "run", "task_id": "task", "success": False},
                             "artifact_evaluation": {"artifact_pass": True}})
    verification = json.loads((root / "verification.json").read_text(encoding="utf-8"))
    assert verification["recovered"] is False
    assert verification["task_recovered"] is False
