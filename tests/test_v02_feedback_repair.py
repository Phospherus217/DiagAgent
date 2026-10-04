import json
from pathlib import Path
import pytest
from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.feedback.store import save_feedback, diagnosis_hash
from diagagent.repair.planner import create_plan
from diagagent.repair.runner import execute_plan
from diagagent.diagnosis.report import DiagnosticReport
from diagagent.diagnosis.taxonomy import FailureCategory
from diagagent.tasks import load_task

ROOT = Path(__file__).resolve().parents[1]


def failed_run(tmp_path):
    env = GIMPEnvironment(run_root=tmp_path)
    agent = RuleAgent()
    original = agent.act
    def act(obs):
        action = original(obs)
        return {"type": "export_file"} if action.type == "export_file" else action
    agent.act = act
    AgentRuntime(env, automatic_recovery=False).run_task(load_task(ROOT / "benchmark/tasks/resize_image.yaml"), agent)
    return env.trace_recorder.run_dir


def feedback(root, **changes):
    return dict(run_id=root.name, diagnosis_sha256=diagnosis_hash(root), diagnosis_correct=True,
                repair_instruction="Add required path to export_file", author="test reviewer", source="test_fixture", **changes)


def test_repair_loop_preserves_parent_and_contract(tmp_path):
    root = failed_run(tmp_path / "original")
    original = {name: (root / name).read_bytes() for name in ("trace.jsonl", "diagnosis.json", "result.json", "task_spec.yaml")}
    saved = save_feedback(root, feedback(root))
    plan_file = create_plan(root, saved)
    plan = json.loads(plan_file.read_text())
    assert plan["status"] == "READY" and not plan["automatic_retry"]
    assert plan["actions"][-1]["path"] == "output.png"
    repaired, report = execute_plan(plan_file, tmp_path / "repaired")
    assert report.success and repaired != root
    assert (repaired / "artifacts/output.png").exists()
    assert json.loads((repaired / "repair_outcome.json").read_text())["feedback_source"] == "test_fixture"
    lineage = json.loads((repaired / "repair_lineage.json").read_text())
    assert lineage["parent_run_id"] == root.name
    assert lineage["attempt_number"] == 1
    assert lineage["attempt_policy"]["automatic_retry"] is False
    attempt = json.loads((repaired / "repair_attempt.json").read_text())
    assert attempt["status"] == "REGISTERED" and attempt["counts_toward_attempt_total"] is True
    registry = root / "repairs/attempts" / plan["plan_id"]
    outcome = json.loads((registry / "attempt_0001.outcome.json").read_text())
    assert outcome["status"] == "SUCCESS" and outcome["run_dir"] == str(repaired)
    assert all((root / name).read_bytes() == data for name, data in original.items())
    assert (repaired / "task_spec.yaml").read_bytes() == original["task_spec.yaml"]


def test_each_explicit_invocation_gets_a_new_attempt_number(tmp_path):
    root = failed_run(tmp_path / "original")
    saved = save_feedback(root, feedback(root))
    plan_file = create_plan(root, saved)
    first, first_report = execute_plan(plan_file, tmp_path / "attempt_1")
    second, second_report = execute_plan(plan_file, tmp_path / "attempt_2")
    assert first_report.success and second_report.success
    first_lineage = json.loads((first / "repair_lineage.json").read_text())
    second_lineage = json.loads((second / "repair_lineage.json").read_text())
    assert (first_lineage["attempt_number"], second_lineage["attempt_number"]) == (1, 2)
    assert first_lineage["attempt_id"] != second_lineage["attempt_id"]
    registry = root / "repairs/attempts" / json.loads(plan_file.read_text())["plan_id"]
    assert len(list(registry.glob("*.registration.json"))) == 2
    assert len(list(registry.glob("*.outcome.json"))) == 2


def test_environment_failure_is_reported_and_counted_without_retry(tmp_path, monkeypatch):
    root = failed_run(tmp_path / "original")
    saved = save_feedback(root, feedback(root))
    plan_file = create_plan(root, saved)

    def environment_failure(self, task, agent):
        return DiagnosticReport(
            task_id=task["task_id"],
            success=False,
            task_status="UNKNOWN",
            failure_type="Launch / Window Error",
            responsibility=FailureCategory.ENVIRONMENT,
        )

    monkeypatch.setattr("diagagent.repair.runner.AgentRuntime.run_task", environment_failure)
    repaired, report = execute_plan(plan_file, tmp_path / "failed_attempt")
    assert not report.success
    outcome = json.loads((repaired / "repair_outcome.json").read_text())
    assert outcome["status"] == "ENVIRONMENT_FAILED"
    assert outcome["counts_toward_attempt_total"] is True
    assert outcome["attempt_policy"]["automatic_retry"] is False
    registry = root / "repairs/attempts" / json.loads(plan_file.read_text())["plan_id"]
    assert len(list(registry.glob("*.registration.json"))) == 1
    assert len(list(registry.glob("*.outcome.json"))) == 1


def test_setup_failure_keeps_an_auditable_attempt_outcome(tmp_path, monkeypatch):
    root = failed_run(tmp_path / "original")
    saved = save_feedback(root, feedback(root))
    plan_file = create_plan(root, saved)

    def fail_environment_setup(*args, **kwargs):
        raise RuntimeError("controlled setup failure")

    monkeypatch.setattr("diagagent.repair.runner.GIMPEnvironment", fail_environment_setup)
    with pytest.raises(RuntimeError, match="controlled setup failure"):
        execute_plan(plan_file, tmp_path / "failed_setup")
    registry = root / "repairs/attempts" / json.loads(plan_file.read_text())["plan_id"]
    outcome = json.loads((registry / "attempt_0001.outcome.json").read_text())
    assert outcome["status"] == "SETUP_FAILED"
    assert outcome["run_id"] is None
    assert outcome["counts_toward_attempt_total"] is True
    assert outcome["attempt_policy"]["automatic_retry"] is False


def test_feedback_rejects_wrong_snapshot_and_incomplete_correction(tmp_path):
    root = failed_run(tmp_path)
    data = feedback(root)
    data["diagnosis_sha256"] = "wrong"
    with pytest.raises(ValueError, match="snapshot"):
        save_feedback(root, data)
    data = feedback(root)
    data["diagnosis_correct"] = False
    with pytest.raises(ValueError, match="corrected_failure_type"):
        save_feedback(root, data)


def test_environment_repair_does_not_silently_retry(tmp_path):
    root = failed_run(tmp_path)
    data = feedback(root)
    data.update(diagnosis_correct=False, corrected_failure_type="API Error", repair_instruction="Check service credentials")
    plan = create_plan(root, save_feedback(root, data))
    assert json.loads(plan.read_text())["status"] == "NEEDS_REPLAN"
    with pytest.raises(ValueError, match="concrete action"):
        execute_plan(plan, tmp_path / "should_not_exist")
    assert not (tmp_path / "should_not_exist").exists()


def test_repair_rejects_stale_feedback(tmp_path):
    root = failed_run(tmp_path)
    saved = save_feedback(root, feedback(root))
    plan = create_plan(root, saved)
    saved.write_text('{}')
    with pytest.raises(ValueError, match="feedback changed"):
        execute_plan(plan, tmp_path / "new")


def test_plan_is_revalidated_before_any_execution(tmp_path):
    root = failed_run(tmp_path)
    saved = save_feedback(root, feedback(root))
    plan = create_plan(root, saved)
    payload = json.loads(plan.read_text())
    payload["actions"][-1]["path"] = "../outside.png"
    plan.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="filename"):
        execute_plan(plan, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_dashboard_and_feedback_cli(tmp_path):
    from click.testing import CliRunner
    from diagagent.cli.main import main
    from diagagent.cli.dashboard import generate_html_report
    root = failed_run(tmp_path)
    page = generate_html_report(root).read_text(encoding="utf-8")
    assert "Human Correction" in page and "AI Diagnosis v0.2" in page
    assert diagnosis_hash(root) in page
    assert (root / "feedback_form.js").exists()
    payload = tmp_path / "human_feedback.json"
    payload.write_text(json.dumps(feedback(root)))
    result = CliRunner().invoke(main, ["feedback", str(root), "--file", str(payload)])
    assert result.exit_code == 0, result.output
    saved = Path(result.output.strip())
    result = CliRunner().invoke(main, ["repair-plan", str(root), "--feedback", str(saved)])
    assert result.exit_code == 0, result.output
    plan = Path(result.output.strip())
    result = CliRunner().invoke(main, ["repair-run", str(plan), "--output-dir", str(tmp_path / "fixed")])
    assert result.exit_code == 0, result.output
