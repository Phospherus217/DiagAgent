import json
from pathlib import Path
import pytest
from diagagent.agent.base import BaseAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.tasks import load_task


class OfflineAgent(BaseAgent):
    def __init__(self, actions):
        super().__init__()
        self.actions = iter(actions)
        self.observations = []
    def reset(self, task_spec):
        self.task_spec = task_spec
    def act(self, observation):
        self.observations.append(observation.model_dump())
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        return action


def execute(tmp_path, actions):
    task = load_task(Path(__file__).resolve().parents[1] / "benchmark/smoke/export_png.yaml")
    env = GIMPEnvironment(run_root=tmp_path)
    agent = OfflineAgent(actions)
    report = AgentRuntime(env).run_task(task, agent)
    return env, agent, report, json.loads((env.trace_recorder.run_dir / "result.json").read_text())


def test_format_repair_is_bounded_and_does_not_leak_answers(tmp_path):
    env, agent, report, result = execute(tmp_path, ["not JSON", {"type": "export_file", "path": "output.png"}, {"type": "stop"}])
    assert result["total_decisions"] == 2 and report.success
    assert report.process_status == "FAIL" and report.failure_type == "Action Format Error"
    assert "action_error" in agent.observations[1]["feedback"]
    assert all("ui_state" not in observation for observation in agent.observations)
    assert not {"evaluation", "reference_actions", "subgoals", "success_criteria"} & agent.task_spec.keys()
    env, _, report, result = execute(tmp_path / "repeat", ["bad", "bad", {"type": "stop"}])
    assert result["total_decisions"] == 2 and result["termination_reason"] == "action_error"


@pytest.mark.parametrize("error,reason", [(KeyboardInterrupt(), "cancelled"), (TimeoutError("test"), "run_timeout"), (RuntimeError("test"), "internal_error")])
def test_failure_always_finalizes_and_closes(tmp_path, error, reason):
    env, _, report, result = execute(tmp_path, [error])
    assert result["termination_reason"] == reason and not report.success
    assert not env.backend.launched
    assert (env.trace_recorder.run_dir / "diagnosis.json").is_file()
