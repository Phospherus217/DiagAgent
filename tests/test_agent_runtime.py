"""Integration tests for AgentRuntime and Agent Loop."""

from pathlib import Path
from PIL import Image
import yaml

from diagagent.agent.mock_agent import MockFaultAgent
from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.environment.gimp_env import GIMPEnvironment


def test_agent_runtime_rule_success(tmp_path):
    input_img = tmp_path / "input.jpg"
    Image.new("RGB", (640, 480), "white").save(input_img)

    task_spec = {
        "task_id": "test_runtime_ok",
        "instruction": "Resize image and export",
        "initial_state": {
            "input_file": str(input_img),
            "output_file": "output.png",
            "window_size": [1280, 720],
        },
        "subgoals": [
            {
                "id": "resize_image",
                "order": 1,
                "type": "parameter",
                "expected_state": {"width": 512, "height": 512},
            },
            {
                "id": "export_png",
                "order": 2,
                "type": "artifact",
            },
        ],
        "success_criteria": {
            "final": {
                "output_exists": True,
                "output_format": "png",
                "output_size": [512, 512],
            }
        },
        "max_steps": 5,
    }

    env = GIMPEnvironment(backend="mock", run_root=tmp_path / "runs")
    agent = RuleAgent()
    runtime = AgentRuntime(env=env)

    try:
        report = runtime.run_task(task_spec=task_spec, agent=agent)
        assert report.success is True
        assert report.first_failure_step is None
    finally:
        env.close()


def test_agent_runtime_mock_fault_injection(tmp_path):
    input_img = tmp_path / "input.jpg"
    Image.new("RGB", (640, 480), "white").save(input_img)

    task_spec = {
        "task_id": "test_runtime_fault",
        "instruction": "Add Demo text",
        "initial_state": {
            "input_file": str(input_img),
            "output_file": "output.png",
            "window_size": [1280, 720],
        },
        "subgoals": [
            {
                "id": "select_tool",
                "order": 1,
                "type": "tool_selection",
                "expected_state": {"active_tool": "text"},
            },
            {
                "id": "add_text",
                "order": 2,
                "type": "canvas_operation",
                "expected_artifact_change": {"text": "Demo", "font_size": 32},
            },
            {
                "id": "export_png",
                "order": 3,
                "type": "artifact",
            },
        ],
        "success_criteria": {
            "final": {
                "output_exists": True,
                "output_format": "png",
            }
        },
        "max_steps": 6,
    }

    env = GIMPEnvironment(backend="mock", run_root=tmp_path / "runs")
    # Injects parameter error at step 2 (font_size=12 instead of 32)
    agent = MockFaultAgent(fault_type="parameter", fault_step=2)
    runtime = AgentRuntime(env=env)

    try:
        report = runtime.run_task(task_spec=task_spec, agent=agent)
        assert report.success is False
        assert report.first_failure_step == 2
        assert report.failure_type == "Parameter Error"
        assert report.responsibility.value == "Agent"
    finally:
        env.close()
