import json

from diagagent.diagnosis.trace_loader import TraceBundle, load_trace
from diagagent.diagnosis.engine import DiagnosisEngine
from diagagent.diagnosis.report import write_diagnostic_report
from diagagent.repair.accounting import next_attempt_number
from diagagent.schemas.feedback import HumanFeedback


def test_load_trace_builds_unified_bundle_without_mutating_run(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"task_id": "resize"}), encoding="utf-8")
    (tmp_path / "task_spec.yaml").write_text("task_id: resize\n", encoding="utf-8")
    (tmp_path / "trace.jsonl").write_text('{"step": 1, "step_eval": {"status": "PASS"}}\n', encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(
        '{"event": "model_response", "step": 1}\n{"event": "action_received", "step": 1}\n',
        encoding="utf-8",
    )
    (tmp_path / "evaluation.json").write_text(
        json.dumps({"artifact": {"status": "PASS"}}), encoding="utf-8"
    )
    before = (tmp_path / "trace.jsonl").read_bytes()
    bundle = load_trace(tmp_path)
    assert isinstance(bundle, TraceBundle)
    assert bundle.run_id == tmp_path.name
    assert bundle.task == "resize"
    assert len(bundle.steps) == 1
    assert len(bundle.model_events) == 1
    assert len(bundle.execution_events) == 1
    assert bundle.artifact_result["status"] == "PASS"
    assert (tmp_path / "trace.jsonl").read_bytes() == before
    report = DiagnosisEngine.diagnose_bundle(bundle)
    assert report.diagnosis_status == "INSUFFICIENT_EVIDENCE"  # no run_finished record


def test_feedback_schema_is_shared_by_feedback_store():
    feedback = HumanFeedback(
        run_id="run",
        diagnosis_sha256="hash",
        diagnosis_correct=True,
        repair_instruction="Use the required output path",
        author="reviewer",
    )
    assert feedback.source == "human"


def test_attempt_accounting_is_read_only_and_gap_safe(tmp_path):
    folder = tmp_path / "repairs" / "attempts" / "plan"
    folder.mkdir(parents=True)
    (folder / "attempt_0002.registration.json").write_text(
        json.dumps({"attempt_number": 2, "status": "REGISTERED"}), encoding="utf-8"
    )
    assert next_attempt_number(tmp_path, "plan") == 3


def test_diagnostic_loop_writes_versioned_machine_and_human_reports(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"task_id": "resize"}), encoding="utf-8")
    (tmp_path / "trace.jsonl").write_text(
        json.dumps({"step": 2, "error_type": "Parameter Error", "step_eval": {
            "status": "FAIL", "error_type": "Parameter Error",
            "evidence": {"expected": 512, "actual": 128}}}) + "\n", encoding="utf-8"
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"event": "run_finished", "success": False}) + "\n", encoding="utf-8"
    )
    (tmp_path / "evaluation.json").write_text(
        json.dumps({"artifact": {"status": "FAIL", "artifact_pass": False}}), encoding="utf-8"
    )
    report_dir = write_diagnostic_report(tmp_path)
    assert report_dir.parent.name == "diagnoses"
    assert {path.name for path in report_dir.iterdir()} == {
        "diagnosis.json", "diagnosis.md", "first_failure.json", "evidence_graph.json", "sources.json"
    }
    diagnosis = json.loads((report_dir / "diagnosis.json").read_text(encoding="utf-8"))
    first_failure = json.loads((report_dir / "first_failure.json").read_text(encoding="utf-8"))
    assert diagnosis["failure_type"] == "Parameter Error"
    assert first_failure["first_failure_step"] == 2
    assert "Earliest observed failure" in (report_dir / "diagnosis.md").read_text(encoding="utf-8")
