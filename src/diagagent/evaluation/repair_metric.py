"""Repair attempt and human-cost metrics."""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Union


def repair_metrics(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(records)
    successes = sum(row.get("status") == "SUCCESS" or row.get("success") is True for row in rows)
    corrections = sum(not bool(row.get("diagnosis_correct", (row.get("diagnosis") or {}).get("correct", False))) for row in rows)
    costs = [row.get("feedback_seconds") for row in rows if isinstance(row.get("feedback_seconds"), (int, float))]
    return {
        "repair_attempts": len(rows),
        "successful_repairs": successes,
        "repair_success_rate": successes / len(rows) if rows else 0.0,
        "diagnosis_corrections": corrections,
        "human_feedback_count": len(rows),
        "feedback_time_seconds_total": sum(costs) if costs else 0.0,
    }


def metrics_from_run(run_dir: Union[str, Path]) -> Dict[str, Any]:
    root = Path(run_dir).resolve()
    attempts = []
    for path in root.rglob("*.outcome.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and "attempt_number" in value:
            attempts.append(value)
    feedback = []
    for path in (root / "feedback").rglob("*.json") if (root / "feedback").exists() else []:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            feedback.append(value)
    return {"repair": repair_metrics(attempts), "diagnosis": __import__(
        "diagagent.evaluation.diagnosis_metric", fromlist=["diagnosis_metrics"]
    ).diagnosis_metrics(feedback)}


__all__ = ["repair_metrics", "metrics_from_run"]

