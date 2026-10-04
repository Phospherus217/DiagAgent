"""Day-1 behavior tests: no desktop or network access."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from click.testing import CliRunner

from diagagent.actions.parser import ActionParser
from diagagent.actions.validator import ActionValidator
from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.cli.main import main
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.environment.errors import DesktopError
from diagagent.environment.real_gimp import RealGIMPBackend
from diagagent.evaluator.artifact_evaluator import ArtifactEvaluator
from diagagent.lowering.structural_lowering import StructuralLowerer, ATOMIC_TYPES
from diagagent.tasks import load_task, public_task

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def task():
    return load_task(ROOT / "benchmark/smoke/resize_export.yaml")


def test_task_paths_and_public_view_are_cwd_independent(task, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    loaded = load_task(ROOT / "benchmark/smoke/resize_export.yaml")
    assert Path(loaded["initial_state"]["input_file"]).is_file()
    view = public_task(loaded)
    assert "512x512" in view["instruction"]
    assert not {"evaluation", "subgoals", "reference_actions", "initial_state"} & view.keys()


@pytest.mark.parametrize("raw", [
    {"type": "click", "x": -1, "y": 10},
    {"type": "resize_image", "width": -1, "height": 512},
    {"type": "unknown_action"},
    {"type": "export_file", "path": "../escape.png"},
    {"type": "export_file", "path": "C:\\outside.png"},
    "invalid JSON",
])
def test_invalid_actions_never_dispatch_and_are_recorded(task, tmp_path, raw):
    env = GIMPEnvironment(run_root=tmp_path)
    env.reset(task)
    env.executor.execute = lambda *_: pytest.fail("invalid action reached executor")
    _, _, done, _ = env.execute(raw)
    assert done
    final = env.evaluate_final()
    assert not final["summary"]["success"]
    events = (env.trace_recorder.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "action_received" in events and "Action Format Error" in events
    env.close()


def test_schema_accepts_legal_but_wrong_goal_dimensions():
    assert ActionValidator.validate({"type": "resize_image", "width": 256, "height": 256})[0]
    assert not ActionValidator.validate({"type": "click", "x": 1280, "y": 1}, window_bbox=(0, 0, 1280, 900))[0]


def test_startup_failure_still_has_result_trace_and_no_artifact(task, tmp_path, monkeypatch):
    env = GIMPEnvironment(run_root=tmp_path)
    def fail():
        raise DesktopError("injected launch failure")
    monkeypatch.setattr(env.backend, "launch", fail)
    report = AgentRuntime(env).run_task(task, RuleAgent())
    assert not report.success
    assert report.responsibility.value == "Environment"
    run = env.trace_recorder.run_dir
    assert all((run / name).is_file() for name in ["trace.jsonl", "result.json", "diagnosis.json", "metadata.json"])
    assert not (run / "artifacts/output.png").exists()


def test_unknown_cli_agent_is_rejected(tmp_path):
    result = CliRunner().invoke(main, ["run", str(ROOT / "benchmark/smoke/resize_export.yaml"),
                                      "--agent", "typo", "--output-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "Unsupported agent" in result.output
    assert not list(tmp_path.iterdir())


def test_artifact_real_format_and_full_decode(tmp_path):
    image = tmp_path / "fake.png"
    Image.new("RGB", (8, 8)).save(image, format="JPEG")
    result = ArtifactEvaluator({"success_criteria": {"final": {"output_format": "png"}}}).evaluate(image)
    assert not result.artifact_pass and not result.format_correct
    image.write_bytes(b"not an image")
    assert not ArtifactEvaluator({}).evaluate(image).artifact_pass


def test_structural_lowerer_queries_current_dialog_and_finishes_at_a0():
    calls = []
    dialog = SimpleNamespace(left=100, top=100, right=600, bottom=500, width=500)
    backend = SimpleNamespace(find_dialog=lambda name: calls.append(name) or dialog,
                              activate=lambda _: None, window=object())
    lowerer = StructuralLowerer(backend)
    actions = lowerer.lower(ActionParser.parse({"type": "set_dialog_field", "dialog": "Scale Image", "field": "Height", "value": 512}))
    assert calls == ["Scale Image"]
    assert actions[0].x == 240 and actions[0].y == 255
    assert all(a.type in ATOMIC_TYPES for a in actions)
    with pytest.raises(DesktopError):
        lowerer.lower(ActionParser.parse({"type": "open_menu", "path": "Unknown"}))


def test_real_backend_never_fabricates_screenshot(tmp_path):
    backend = RealGIMPBackend(gimp_executable="not-installed.exe")
    target = tmp_path / "screenshot.png"
    with pytest.raises(DesktopError):
        backend.capture_screenshot(target)
    assert not target.exists()


def test_two_smoke_tasks_have_complete_independent_runs(tmp_path):
    for file in sorted((ROOT / "benchmark/smoke").glob("*.yaml")):
        spec = load_task(file)
        env = GIMPEnvironment(run_root=tmp_path)
        report = AgentRuntime(env).run_task(spec, RuleAgent())
        assert report.success
        run = env.trace_recorder.run_dir
        data = json.loads((run / "result.json").read_text())
        assert data["duration_seconds"] > 0 and data["termination_reason"] == "completed"
        assert data["backend"] == "mock"
        records = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        assert all((run / r["obs_after"]["screenshot_path"]).exists() for r in records)
        assert len(records[0]["obs_after"]["ui_primitives"]) == 5


def test_evaluator_exception_writes_unknown_result_and_closes(task, tmp_path, monkeypatch):
    env = GIMPEnvironment(run_root=tmp_path)
    def fail():
        raise RuntimeError("broken evaluator")
    monkeypatch.setattr(env, "evaluate_final", fail)
    report = AgentRuntime(env).run_task(task, RuleAgent())
    assert not report.success and report.failure_type == "Evaluator Error"
    result = json.loads((env.trace_recorder.run_dir / "result.json").read_text())
    assert result["task_status"] == "UNKNOWN"
    assert not env.backend.launched


def test_no_step_budget_silent_success(task, tmp_path):
    env = GIMPEnvironment(run_root=tmp_path)
    report = AgentRuntime(env).run_task(task, RuleAgent(), max_steps=1)
    assert not report.success
    result = json.loads((env.trace_recorder.run_dir / "result.json").read_text())
    assert result["termination_reason"] == "max_steps"
