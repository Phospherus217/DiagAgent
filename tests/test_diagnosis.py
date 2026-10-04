"""Unit tests for failure diagnosis and FirstFailureLocator."""

from diagagent.diagnosis.engine import DiagnosisEngine
from diagagent.diagnosis.first_failure import FirstFailureLocator
from diagagent.diagnosis.taxonomy import FailureCategory, get_responsibility


def test_responsibility_mapping():
    assert get_responsibility("Action Format Error") == FailureCategory.AGENT
    assert get_responsibility("Tool Selection Error") == FailureCategory.AGENT
    assert get_responsibility("Parameter Error") == FailureCategory.AGENT
    assert get_responsibility("UI Grounding Error") == FailureCategory.EXECUTION
    assert get_responsibility("Dialog Operation Error") == FailureCategory.EXECUTION
    assert get_responsibility("Artifact Error") == FailureCategory.ARTIFACT
    assert get_responsibility("Environment Error") == FailureCategory.ENVIRONMENT


def test_first_failure_locator_step_failure():
    steps = [
        {"step": 1, "subgoal_id": "open_image", "subgoal_completed": True, "error_type": None},
        {"step": 2, "subgoal_id": "select_tool", "subgoal_completed": True, "error_type": None},
        {
            "step": 3,
            "subgoal_id": "add_text",
            "subgoal_completed": False,
            "error_type": "Parameter Error",
            "evidence": {"expected_font_size": 32, "actual_font_size": 12},
        },
    ]
    loc = FirstFailureLocator.locate(step_evaluations=steps)
    assert loc["first_failure_step"] == 3
    assert loc["failure_type"] == "Parameter Error"
    assert loc["evidence"]["expected_font_size"] == 32
    assert loc["evidence"]["actual_font_size"] == 12


def test_diagnosis_engine_report_generation():
    task_spec = {"task_id": "add_text_demo", "instruction": "Add Demo text"}
    steps = [
        {"step": 1, "subgoal_id": "open_image", "subgoal_completed": True, "error_type": None},
        {"step": 2, "subgoal_id": "select_tool", "subgoal_completed": True, "error_type": None},
        {
            "step": 3,
            "subgoal_id": "add_text",
            "subgoal_completed": False,
            "error_type": "Parameter Error",
            "evidence": {"expected_font_size": 32, "actual_font_size": 12},
        },
    ]
    report = DiagnosisEngine.diagnose(task_spec=task_spec, step_evaluations=steps)
    assert report.success is False
    assert report.first_failure_step == 3
    assert report.failure_type == "Parameter Error"
    assert report.responsibility == FailureCategory.AGENT

    cli_text = report.format_cli()
    assert "Task: Add Demo text" in cli_text
    assert "1 open image PASS" in cli_text
    assert "2 select tool PASS" in cli_text
    assert "3 add text FAIL" in cli_text
    assert "First Failure: Step 3" in cli_text
    assert "Type: Parameter Error" in cli_text
    assert "expected_font_size=32" in cli_text
