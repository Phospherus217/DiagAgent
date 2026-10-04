from pathlib import Path
from diagagent.diagnosis.injection import run_injections


def test_controlled_failure_suite_and_clean_control(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    summary = run_injections("injections", Path(__file__).resolve().parents[1] / "benchmark/tasks")
    assert summary["passed"], [r for r in summary["cases"] if not (r["detected"] and r["first_failure_correct"] and r["responsibility_correct"] and r["type_correct"])]
    assert len(summary["cases"]) >= 10
    assert summary["real_gui_acceptance"] is False
    assert all(value == 1 for value in summary["metrics"].values())
