import json
from pathlib import Path
from diagagent.agent.runtime import AgentRuntime
from diagagent.config import DesktopConfig
from diagagent.diagnosis.classifier import classify
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.models.base import ModelResponse
from diagagent.tasks import load_task
from diagagent.trace.recorder import TraceRecorder
from diagagent.verification.verifier import RuntimeVerifier

ROOT = Path(__file__).resolve().parents[1]


def test_complete_model_evidence_and_secret_scrubbing(tmp_path):
    class Model:
        api_key = "opaque-test-credential"
        def __init__(self):
            self.actions = iter(['{"type":"resize_image","width":512,"height":512}',
                                 '{"type":"export_file","path":"output.png"}'])
        def query(self, *args, **kwargs):
            return ModelResponse(text=next(self.actions), model="fixture",
                raw={"nested": {"Authorization": "Bearer opaque-test-credential"},
                     "echo": self.api_key}, latency_ms=5)
    env = GIMPEnvironment(run_root=tmp_path)
    report = AgentRuntime(env, model=Model(), automatic_recovery=False).run_task(load_task(ROOT / "benchmark/smoke/resize_export.yaml"))
    assert report.success
    root = env.trace_recorder.run_dir
    for name in ("model", "action", "environment", "artifact", "diagnosis"):
        rows = [json.loads(line) for line in (root / "traces" / f"{name}_trace.jsonl").read_text().splitlines()]
        assert rows and all("status" in row and "latency_ms" in row and "error_type" in row for row in rows)
    for file in root.rglob("*.json*"):
        text = file.read_text(encoding="utf-8")
        assert "opaque-test-credential" not in text and "Authorization" not in text
    model_trace = (root / "traces/model_trace.jsonl").read_text()
    assert '"messages"' in model_trace and '"response_text"' in model_trace
    assert '"call_id": 1' in model_trace
    assert '"action_validated"' in (root / "traces/action_trace.jsonl").read_text()


def test_recovered_parse_failure_is_still_diagnosed(tmp_path):
    class Model:
        def __init__(self):
            self.texts = iter(['not json', '{"type":"resize_image","width":512,"height":512}',
                               '{"type":"export_file","path":"output.png"}'])
        def query(self, *args, **kwargs):
            return ModelResponse(text=next(self.texts))
    env = GIMPEnvironment(run_root=tmp_path)
    report = AgentRuntime(env, model=Model()).run_task(load_task(ROOT / "benchmark/smoke/resize_export.yaml"))
    diagnosis = json.loads(env.trace_recorder.diagnosis_file.read_text())
    assert report.success
    assert diagnosis["failure"] and diagnosis["type"] == "Action Format Error" and diagnosis["step"] == 2


def test_no_retry_profile_keeps_failed_response(tmp_path):
    class Model:
        calls = 0
        def query(self, *args, **kwargs):
            self.calls += 1
            return ModelResponse(text="not json")
    model = Model()
    env = GIMPEnvironment(run_root=tmp_path, config=DesktopConfig(max_format_repairs=1))
    AgentRuntime(env, model=model, automatic_recovery=False).run_task(load_task(ROOT / "benchmark/smoke/resize_export.yaml"))
    assert model.calls == 1
    diagnosis = json.loads(env.trace_recorder.diagnosis_file.read_text())
    assert diagnosis["step"] == 2 and diagnosis["type"] == "Action Format Error"
    assert not any(row["event"] == "recovery_triggered" for row in env.trace_recorder.events)


def test_insufficient_evidence_and_no_artifact_cause_inference():
    assert classify([])["diagnosis_status"] == "INSUFFICIENT_EVIDENCE"
    result = classify([], report={"failure_type": "Artifact Error", "artifact_status": "FAIL"})
    assert result["step"] is None and result["type"] == "Artifact Error"
    result = classify([], [{"event": "model_error", "step": 2,
        "model_error": {"failure_type": "Authentication Error", "status_code": 401}}])
    assert result["layer"] == "Environment" and result["v02_attribution"]["failure_layer"] == "MODEL"


def test_nested_credentials_and_text_are_scrubbed(tmp_path):
    recorder = TraceRecorder(tmp_path, "safe")
    recorder.redactor.register("opaque-secret")
    recorder.event("model_response", text='{"api_key":"opaque-secret","answer":"ok"}',
                   error="Authorization: Bearer opaque-secret", nested={"api-key": "opaque-secret"})
    assert "opaque-secret" not in (recorder.run_dir / "events.jsonl").read_text()
    assert "Authorization" not in (recorder.run_dir / "events.jsonl").read_text()


def test_runtime_verifier_reads_top_level_and_nested_execution_failures():
    verifier = RuntimeVerifier()
    failed = verifier.verify(None, {"type": "export_file"}, {
        "success": False, "error": "path missing", "error_type": "Action Format Error"}, None)
    assert not failed.passed and failed.suspected_failure == "Action Format Error"
    passed = verifier.verify(None, {"type": "resize_image"}, {
        "execution_result": {"success": True, "state_changed": True}}, None)
    assert passed.passed and passed.signals["state_changed"] is True
