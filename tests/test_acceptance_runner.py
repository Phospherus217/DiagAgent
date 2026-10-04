"""Batch failure accounting without desktop processes or external requests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import pytest

spec = importlib.util.spec_from_file_location("acceptance_runner", Path(__file__).resolve().parents[1] / "scripts/run_acceptance.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def fingerprint(_):
    return {"source_sha256": "fixed", "inputs_sha256": {}}


def fake_success(command, timeout):
    root = Path(command[command.index("--output-dir") + 1]) / "fake_run"
    root.mkdir(parents=True)
    task = "da_smoke_export_png" if "export_png.yaml" in command[4] else "da_smoke_resize_export"
    (root / "result.json").write_text(json.dumps(dict(task_id=task, task_status="PASS",
        termination_reason="agent_stop", artifact_status="PASS", process_status="PASS")))
    (root / "metadata.json").write_text(json.dumps(dict(source_sha256="fixed", backend="mock")))
    return subprocess.CompletedProcess(command, 0, "completed", "")


def test_mock_batch_never_counts_as_real_acceptance(tmp_path):
    result = batch.run_batch(tmp_path / "batch", tmp_path / "config", 5, "mock", fake_success, fingerprint)
    assert result["offline_batch_pass"] and not result["passed"]
    assert result["planned"] == 10 and result["groups"]["resize_export"]["pass"] == 5
    with pytest.raises(FileExistsError):
        batch.run_batch(tmp_path / "batch", tmp_path / "config", 5, "mock", fake_success, fingerprint)


@pytest.mark.parametrize("error,reason", [
    (subprocess.TimeoutExpired("test", 240, output=b"partial log"), "batch_timeout"),
    (KeyboardInterrupt(), "cancelled"), (OSError("cannot launch"), "batch_error")])
def test_abort_preserves_all_planned_attempts(tmp_path, error, reason):
    def fail(*args, **kwargs):
        raise error
    result = batch.run_batch(tmp_path / "batch", tmp_path / "config", 5, "mock", fail, fingerprint)
    assert result["state"] == "ABORTED" and result["abort_reason"] == reason
    assert result["attempts"][0]["status"] == "UNKNOWN"
    assert sum(r["status"] == "NOT_STARTED" for r in result["attempts"]) == 9
    assert len(json.loads((tmp_path / "batch/plan.json").read_text())["attempts"]) == 10


def test_mid_attempt_version_change_invalidates_run(tmp_path):
    reads = iter([fingerprint(None), fingerprint(None), {"source_sha256": "changed"}])
    result = batch.run_batch(tmp_path / "batch", tmp_path / "config", 5, "mock", fake_success, lambda _: next(reads))
    assert result["abort_reason"] == "version_changed_during_attempt"
    assert result["attempts"][0]["status"] == "UNKNOWN" and not result["passed"]


def test_corrupt_result_is_unknown(tmp_path):
    def corrupt(command, timeout):
        done = fake_success(command, timeout)
        target = Path(command[command.index("--output-dir") + 1]) / "fake_run/result.json"
        target.write_text('{"task_status":')
        return done
    result = batch.run_batch(tmp_path / "batch", tmp_path / "config", 5, "mock", corrupt, fingerprint)
    assert result["abort_reason"] == "batch_error" and not result["passed"]


@pytest.mark.parametrize("repeats", [0, -1, 21, True])
def test_bad_repeats_rejected_before_writes(tmp_path, repeats):
    with pytest.raises(ValueError):
        batch.run_batch(tmp_path / "batch", tmp_path / "config", repeats, "mock", fake_success, fingerprint)
    assert not (tmp_path / "batch").exists()
