import json
import shutil
from pathlib import Path
from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.cli.dashboard import generate_html_report
from diagagent.cli.diagnose import diagnose_cli_run, recompute_run
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.tasks import load_task
from diagagent.trace.paths import evidence_path


def completed_run(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = GIMPEnvironment(run_root=tmp_path)
    AgentRuntime(env).run_task(load_task(root / "benchmark/smoke/resize_export.yaml"), RuleAgent())
    return env.trace_recorder.run_dir


def test_moved_run_recomputes_without_overwriting_original(tmp_path):
    run = completed_run(tmp_path / "original")
    moved = tmp_path / "moved"
    shutil.copytree(run, moved)
    original = {n: (moved / n).read_bytes() for n in ["trace.jsonl", "diagnosis.json", "result.json"]}
    output = diagnose_cli_run(moved, recompute=True)
    assert json.loads(output.read_text())["diagnosis"]["task_status"] == "PASS"
    assert all((moved / n).read_bytes() == data for n, data in original.items())
    html_path = generate_html_report(moved)
    html = html_path.read_text(encoding="utf-8")
    assert 'screenshots/step_001_reset.png' in html
    assert 'artifacts/output.png' in html
    (moved / "screenshots/step_001_reset.png").unlink()
    recomputed = recompute_run(moved)
    assert recomputed["diagnosis"]["task_status"] == "UNKNOWN"
    assert recomputed["diagnosis"]["artifact_status"] == "PASS"
    assert recomputed["diagnosis"]["process_status"] == "UNKNOWN"


def test_dashboard_escapes_untrusted_text_and_blocks_outside_paths(tmp_path):
    run = completed_run(tmp_path / "runs")
    diagnosis = json.loads((run / "diagnosis.json").read_text())
    diagnosis["task_instruction"] = '<script>alert("x")</script>'
    (run / "diagnosis.json").write_text(json.dumps(diagnosis), encoding="utf-8")
    page = generate_html_report(run).read_text(encoding="utf-8")
    assert '<script>' not in page and '&lt;script&gt;' in page
    outside = tmp_path / "private.png"
    outside.write_bytes(b"private")
    assert evidence_path(run, str(outside)) is None
    assert evidence_path(run, "../../private.png") is None


def test_incomplete_run_is_unknown(tmp_path):
    assert recompute_run(tmp_path)["diagnosis"]["task_status"] == "UNKNOWN"


def test_partial_trace_retains_valid_prefix_and_artifact(tmp_path):
    run = completed_run(tmp_path)
    with (run / "trace.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"step":')
    result = recompute_run(run)
    assert result["steps"] and result["artifact"]["status"] == "PASS"
    assert result["diagnosis"]["task_status"] == "UNKNOWN"
    assert any("Corrupt/incomplete" in warning for warning in result["warnings"])


def test_missing_task_contract_cannot_certify_existing_artifact(tmp_path):
    run = completed_run(tmp_path)
    (run / "task_spec.yaml").write_text("", encoding="utf-8")
    result = recompute_run(run)
    assert result["artifact"]["status"] == "UNKNOWN"
    assert not result["diagnosis"]["success"]
