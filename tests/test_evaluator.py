"""Unit tests for StepEvaluator and ArtifactEvaluator."""

from pathlib import Path
from PIL import Image
from diagagent.evaluator.artifact_evaluator import ArtifactEvaluator
from diagagent.evaluator.step_evaluator import StepEvaluator
from diagagent.observation.models import Observation


def test_step_evaluator_parameter_mismatch():
    task_spec = {
        "task_id": "test_param",
        "subgoals": [
            {
                "id": "resize_image",
                "order": 1,
                "type": "parameter",
                "expected_state": {"width": 512, "height": 512},
            }
        ],
    }
    evaluator = StepEvaluator(task_spec)

    # Correct parameter
    eval_ok = evaluator.evaluate_step(
        step_index=1,
        action={"type": "resize_image", "width": 512, "height": 512},
    )
    assert eval_ok.subgoal_completed is True
    assert eval_ok.error_type is None

    # Incorrect parameter
    eval_bad = evaluator.evaluate_step(
        step_index=1,
        action={"type": "resize_image", "width": 256, "height": 256},
    )
    assert eval_bad.subgoal_completed is False
    assert eval_bad.error_type == "Parameter Error"
    assert eval_bad.evidence["expected_width"] == 512
    assert eval_bad.evidence["actual_width"] == 256


def test_step_evaluator_tool_mismatch():
    task_spec = {
        "task_id": "test_tool",
        "subgoals": [
            {
                "id": "select_tool",
                "order": 1,
                "type": "tool_selection",
                "expected_state": {"active_tool": "text"},
            }
        ],
    }
    evaluator = StepEvaluator(task_spec)

    eval_bad = evaluator.evaluate_step(
        step_index=1,
        action={"type": "select_tool", "tool_name": "brush"},
    )
    assert eval_bad.subgoal_completed is False
    assert eval_bad.error_type == "Tool Selection Error"


def test_artifact_evaluator(tmp_path):
    artifact_file = tmp_path / "output.png"
    Image.new("RGB", (512, 512), "red").save(artifact_file)

    task_spec = {
        "task_id": "test_art",
        "initial_state": {"output_file": "output.png"},
        "success_criteria": {
            "final": {
                "output_exists": True,
                "output_format": "png",
                "output_size": [512, 512],
            }
        },
    }
    evaluator = ArtifactEvaluator(task_spec)
    res = evaluator.evaluate(artifact_file)

    assert res.artifact_pass is True
    assert res.format_correct is True
    assert res.size_correct is True
    assert res.error_type is None
