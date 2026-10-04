"""Tests for Click CLI interface."""

from pathlib import Path
from click.testing import CliRunner

from diagagent.cli.main import main


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "diagnose" in result.output
    assert "benchmark" in result.output
    assert "dashboard" in result.output


def test_cli_run_task(tmp_path):
    runner = CliRunner()
    task_file = Path(__file__).parent.parent / "benchmark" / "tasks" / "resize_image.yaml"

    result = runner.invoke(
        main,
        ["run", str(task_file), "--backend", "mock", "--output-dir", str(tmp_path / "runs")],
    )
    assert result.exit_code == 0
    assert "Task:" in result.output
    assert "Execution:" in result.output
    assert "Diagnosis:" in result.output
    assert "Result: PASS" in result.output


def test_cli_run_fault_simulation(tmp_path):
    runner = CliRunner()
    task_file = Path(__file__).parent.parent / "benchmark" / "tasks" / "add_text.yaml"

    result = runner.invoke(
        main,
        [
            "run",
            str(task_file),
            "--agent",
            "mock_fault",
            "--fault-type",
            "parameter",
            "--fault-step",
            "3",
            "--backend",
            "mock",
            "--output-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 1
    assert "Task: Add Demo text" in result.output
    assert "1 open image PASS" in result.output
    assert "2 select tool PASS" in result.output
    assert "3 add text FAIL" in result.output
    assert "First Failure: Step 3" in result.output
    assert "Type: Parameter Error" in result.output
    assert "Responsibility: Agent" in result.output
    assert "expected_font_size=32" in result.output
