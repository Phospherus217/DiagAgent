import json

from diagagent.diagnosis.graph import build_evidence_graph
from diagagent.diagnosis.trace_loader import TraceBundle
from diagagent.evaluation.diagnosis_metric import diagnosis_metrics
from diagagent.evaluation.repair_metric import repair_metrics
from diagagent.feedback.collector import load_annotations, save_annotation
from diagagent.feedback.store import save_feedback
from diagagent.repair.replay import verify_repair
from diagagent.schemas.diagnosis import DiagnosisResult


def bundle(tmp_path):
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "screenshots" / "step_002.png").write_bytes(b"png")
    (tmp_path / "artifacts" / "output.png").write_bytes(b"png")
    return TraceBundle(
        run_id="run-1", task_id="resize", screenshots=["screenshots/step_002.png"],
        artifacts=["artifacts/output.png"], artifact_result={"status": "FAIL"},
        steps=[{"step": 2, "action": {"type": "resize_image", "width": 128},
                "step_eval": {"error_type": "Parameter Error"}}],
    )


def test_evidence_graph_links_failure_and_artifact(tmp_path):
    trace = bundle(tmp_path)
    diagnosis = DiagnosisResult(run_id="run-1", first_failure_step=2,
                                failure_type="Parameter Error", type="Parameter Error",
                                layer="Agent", failure=True, confidence="high")
    graph = build_evidence_graph(trace, diagnosis)
    assert graph["schema_version"] == "0.3"
    assert {node["type"] for node in graph["nodes"]} >= {"Task Goal", "Action", "Failure", "Artifact"}
    assert any(edge["relation"] == "causes" for edge in graph["edges"])


def test_v03_annotation_storage_and_metrics(tmp_path):
    run = tmp_path / "run-1"
    run.mkdir()
    path = save_annotation(run, {
        "run_id": "run-1", "diagnosis": {"type": "Parameter Error", "correct": True},
        "human_label": {"type": "Parameter Error"}, "repair_instruction": {"change": "fix"},
        "author": "reviewer",
    })
    assert path.exists() and len(load_annotations(run)) == 1
    path2 = save_feedback(run, {
        "run_id": "run-1", "diagnosis": {"type": "Parameter Error", "correct": True},
        "human_label": {"type": "Parameter Error"}, "repair_instruction": {"change": "fix again"},
        "author": "reviewer",
    })
    assert path2.exists() and len(load_annotations(run)) == 2
    assert diagnosis_metrics(load_annotations(run))["accuracy"] == 1.0
    assert repair_metrics([{"status": "SUCCESS"}])["repair_success_rate"] == 1.0


def test_replay_verification_records_before_after(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir(); after.mkdir()
    (before / "result.json").write_text(json.dumps({"success": False, "task_status": "FAIL"}), encoding="utf-8")
    (after / "result.json").write_text(json.dumps({"success": True, "task_status": "PASS"}), encoding="utf-8")
    (before / "task_spec.yaml").write_text("task_id: x\n", encoding="utf-8")
    (after / "task_spec.yaml").write_text("task_id: x\n", encoding="utf-8")
    result = verify_repair(before, after)
    assert result["improved"] is True
    assert result["after"]["success"] is True
    assert (after / "replay_verification.json").exists()
