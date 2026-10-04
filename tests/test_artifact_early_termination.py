"""Offline runtime regressions: mock GUI/model, real local artifact checks."""
import json
from pathlib import Path

import pytest

from diagagent.agent.runtime import AgentRuntime
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.evaluator.models import ArtifactEvaluation
from diagagent.models.mock import MockModel
from diagagent.tasks import load_task


RESIZE = {"type": "resize_image", "width": 512, "height": 512}
EXPORT = {"type": "export_file", "path": "output.png", "format": "png"}
STOP = {"type": "stop"}


class BoundedModel(MockModel):
    def query(self, messages, observation=None):
        if not self.responses:
            self.query_count += 1
            raise TimeoutError("Unexpected post-completion model call")
        return super().query(messages, observation)


@pytest.fixture
def setup_run(tmp_path):
    task = load_task(Path(__file__).resolve().parents[1] / "benchmark/smoke/resize_export.yaml")
    env = GIMPEnvironment(backend="mock", run_root=tmp_path)
    return task, env


def read_json(env, name):
    return json.loads((env.trace_recorder.run_dir / name).read_text(encoding="utf-8"))


def read_rows(env, name):
    return [json.loads(line) for line in
            (env.trace_recorder.run_dir / name).read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("max_steps", [2, 8])
def test_export_success_enters_terminal_without_another_model_call(setup_run, max_steps):
    task, env = setup_run
    model = BoundedModel(responses=[RESIZE, EXPORT])
    report = AgentRuntime(env, model=model).run(task, max_steps=max_steps)

    assert model.query_count == env.model_call_count == env.total_decisions == 2
    assert env.done and env.termination_reason == "completed"
    assert report.success and report.artifact_status == "PASS"
    assert report.first_failure_step is None
    assert not env.backend.launched
    result = read_json(env, "result.json")
    assert result["termination_reason"] == "completed" and result["success"]
    assert read_json(env, "diagnosis.json")["termination_reason"] == "completed"
    assert read_json(env, "evaluation.json")["artifact"]["status"] == "PASS"
    rows = read_rows(env, "trace.jsonl")
    assert [row["action"]["type"] for row in rows if row.get("action")] == ["resize_image", "export_file"]
    assert rows[-1]["done"] and rows[-1]["phase"] == "final"
    assert rows[-1]["step"] == rows[-2]["step"] == 3
    assert rows[-1]["artifact_evaluation"]["status"] == "PASS"
    assert all((env.trace_recorder.run_dir / row["obs_after"]["screenshot_path"]).is_file() for row in rows)
    events = read_rows(env, "events.jsonl")
    assert len([e for e in events if e["event"] == "model_request"]) == 2
    assert not any(e["event"] in {"model_error", "run_error"} for e in events)
    assert events[-1]["event"] == "run_finished"


def test_artifact_fail_continues_loop_and_preserves_diagnosis(setup_run, monkeypatch):
    task, env = setup_run
    # Export decodes successfully, but fails the unchanged required size check.
    export = env.backend.export_file

    def wrong_size(path):
        export(path)
        from PIL import Image
        Image.new("RGB", (256, 256)).save(path)

    monkeypatch.setattr(env.backend, "export_file", wrong_size)
    model = BoundedModel(responses=[RESIZE, EXPORT, STOP])
    report = AgentRuntime(env, model=model).run(task)

    assert model.query_count == 3 and env.termination_reason == "agent_stop"
    assert not report.success and report.artifact_status == "FAIL"
    assert report.failure_type == "Artifact Error"
    assert read_json(env, "diagnosis.json")["failure_type"] == "Artifact Error"
    events = read_rows(env, "events.jsonl")
    check = next(e for e in events if e["event"] == "artifact_completion_checked")
    assert check["artifact_evaluation"]["status"] == "FAIL"
    assert not any(e["event"] == "runtime_completed" for e in events)
    # Private evaluator evidence remains out of the next model request.
    next_prompt = model.history[-1][-1].content
    assert all(key not in next_prompt for key in ("artifact_evaluation", "success_criteria", "subgoals", "size_correct"))


def test_non_terminal_action_keeps_model_loop(setup_run):
    task, env = setup_run
    model = BoundedModel(responses=[RESIZE, {"type": "wait", "duration": 0}, EXPORT])
    report = AgentRuntime(env, model=model).run(task)
    assert model.query_count == 3 and report.success
    events = read_rows(env, "events.jsonl")
    checks = [e for e in events if e["event"] == "artifact_completion_checked"]
    assert len(checks) == 1 and checks[0]["step"] == 4


def test_artifact_unknown_does_not_complete(setup_run, monkeypatch):
    task, env = setup_run
    from diagagent.evaluator.dual_evaluator import DualEvaluator
    monkeypatch.setattr(DualEvaluator, "evaluate_artifact",
                        lambda *_: ArtifactEvaluation(status="UNKNOWN", artifact_pass=False))
    model = BoundedModel(responses=[RESIZE, EXPORT, STOP])
    report = AgentRuntime(env, model=model).run(task)
    assert model.query_count == 3 and env.termination_reason == "agent_stop"
    assert not report.success and report.artifact_status == "UNKNOWN"


@pytest.mark.parametrize("execution", [
    {"success": False, "effect_status": "PASS"},
    {"success": True, "effect_status": "FAIL"},
    {"success": True, "effect_status": "UNKNOWN"},
])
def test_export_without_execution_success_does_not_complete(setup_run, monkeypatch, execution):
    task, env = setup_run
    step = env.step

    def unverified_export(action):
        obs, reward, done, info = step(action)
        if action.type == "export_file":
            info["execution_result"].update(execution)
        return obs, reward, done, info

    monkeypatch.setattr(env, "step", unverified_export)
    model = BoundedModel(responses=[RESIZE, EXPORT, STOP])
    AgentRuntime(env, model=model).run(task)
    assert model.query_count == 3 and env.termination_reason == "agent_stop"
    assert not any(e["event"] == "runtime_completed" for e in read_rows(env, "events.jsonl"))


def test_intermediate_export_does_not_skip_later_task_action(setup_run):
    task, env = setup_run
    task["subgoals"].append({"id": "later_action", "order": 4, "type": "ui_state"})
    model = BoundedModel(responses=[RESIZE, EXPORT, STOP])
    AgentRuntime(env, model=model).run(task)
    assert model.query_count == 3 and env.termination_reason == "agent_stop"


def test_completion_preserves_earlier_failure_taxonomy(setup_run):
    task, env = setup_run
    wrong = {"type": "resize_image", "width": 256, "height": 256}
    model = BoundedModel(responses=[wrong, RESIZE, EXPORT])
    report = AgentRuntime(env, model=model).run(task)
    assert model.query_count == 3 and env.termination_reason == "completed"
    assert report.success and report.process_status == "FAIL"
    assert report.has_observed_failure and report.failure_type == "Parameter Error"
    assert report.first_failure_step == 2
    assert read_json(env, "diagnosis.json")["failure_type"] == "Parameter Error"


def test_completion_check_exception_preserves_evaluator_failure(setup_run, monkeypatch):
    task, env = setup_run
    from diagagent.evaluator.dual_evaluator import DualEvaluator
    evaluate = DualEvaluator.evaluate_artifact
    calls = []

    def fail_once(self, artifact):
        calls.append(artifact)
        if len(calls) == 1:
            raise RuntimeError("injected evaluator failure")
        return evaluate(self, artifact)

    monkeypatch.setattr(DualEvaluator, "evaluate_artifact", fail_once)
    model = BoundedModel(responses=[RESIZE, EXPORT])
    report = AgentRuntime(env, model=model).run(task)
    assert model.query_count == 2 and not report.success
    assert report.failure_type == "Evaluator Error"
    assert env.termination_reason != "completed" and not env.backend.launched
    assert read_json(env, "diagnosis.json")["failure_type"] == "Evaluator Error"
