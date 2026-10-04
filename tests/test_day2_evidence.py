"""Evidence semantics: waiting, correction, uncertainty and observed faults."""
import json
from pathlib import Path
from PIL import Image
import pytest
from diagagent.agent.runtime import AgentRuntime
from diagagent.agent.base import BaseAgent
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.environment.errors import DesktopError
from diagagent.tasks import load_task
from diagagent.evaluator.artifact_evaluator import ArtifactEvaluator
from diagagent.diagnosis.engine import DiagnosisEngine

ROOT = Path(__file__).resolve().parents[1]


class SequenceAgent(BaseAgent):
    def __init__(self, actions):
        super().__init__()
        self.actions = iter(actions)
    def reset(self, task_spec):
        self.task_spec = task_spec
    def act(self, observation):
        # The runtime must not give non-oracle agents evaluator state.
        assert not hasattr(observation, "ui_state")
        return next(self.actions)


def run(tmp_path, actions, modify=None):
    task = load_task(ROOT / "benchmark/smoke/resize_export.yaml")
    env = GIMPEnvironment(run_root=tmp_path)
    if modify:
        modify(env)
    report = AgentRuntime(env).run_task(task, SequenceAgent(actions))
    return report, env.trace_recorder.run_dir


RESIZE = {"type": "resize_image", "width": 512, "height": 512}
EXPORT = {"type": "export_file", "path": "output.png"}
STOP = {"type": "stop"}


def test_wait_does_not_advance_parameter_predicate(tmp_path):
    report, _ = run(tmp_path, [{"type": "wait"}, RESIZE, EXPORT, STOP])
    assert report.success and report.process_status == "PASS"


def test_corrected_parameter_retains_first_failure_but_task_passes(tmp_path):
    report, _ = run(tmp_path, [dict(RESIZE, width=256, height=256), RESIZE, EXPORT, STOP])
    assert report.success and report.task_status == "PASS"
    assert report.process_status == "FAIL" and report.has_observed_failure
    assert report.failure_type == "Parameter Error" and report.first_failure_step == 2


def test_explicit_noop_requires_observed_size_evidence(tmp_path):
    report, _ = run(tmp_path, [RESIZE, EXPORT, STOP],
                    lambda env: setattr(env.backend, "resize_image", lambda *_: None))
    assert report.failure_type == "Execution No-op Error"
    assert report.responsibility.value == "Execution"
    assert report.evidence["actual_size"] == [800, 600]


def test_export_failure_keeps_execution_cause(tmp_path):
    def modify(env):
        def fail(*_):
            raise DesktopError("controlled write failure", "File I/O Error")
        env.backend.export_file = fail
    report, _ = run(tmp_path, [RESIZE, EXPORT, STOP], modify)
    assert report.failure_type == "File I/O Error"
    assert report.responsibility.value == "Execution"


def test_missing_artifact_alone_does_not_invent_failing_step():
    report = DiagnosisEngine.diagnose({"task_id": "missing"}, [],
        {"status": "FAIL", "artifact_pass": False, "error_type": "Artifact Error"})
    assert report.first_failure_step is None
    assert report.failure_type == "Artifact Error"


def test_absent_evidence_is_unknown_not_noop_or_pass():
    report = DiagnosisEngine.diagnose({"task_id": "missing"},
        [{"step": 1, "status": "UNKNOWN", "subgoal_completed": False}])
    assert report.task_status == "UNKNOWN" and not report.success
    assert report.failure_type is None and report.responsibility is None


def test_required_blur_reference_missing_cannot_pass(tmp_path):
    output = tmp_path / "out.png"
    Image.new("RGB", (32, 32)).save(output)
    result = ArtifactEvaluator({"success_criteria": {"final": {"blur_metric_change": True}},
        "initial_state": {"input_file": str(tmp_path / "missing.png")}}).evaluate(output)
    assert not result.artifact_pass and result.status == "UNKNOWN"
    assert result.error_type == "Evaluator Error"


def test_unknown_semantic_content_is_not_certified(tmp_path):
    output = tmp_path / "out.png"
    Image.new("RGB", (32, 32)).save(output)
    task = load_task(ROOT / "benchmark/tasks/add_text.yaml")
    result = ArtifactEvaluator(task).evaluate(output)
    assert result.status == "UNKNOWN" and not result.artifact_pass
