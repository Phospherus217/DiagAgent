"""Review-set metrics; labels come from human annotations, never task gold."""

from collections import Counter
from typing import Any, Dict, Iterable


def _label(record: Dict[str, Any], key: str):
    nested = record.get("human_label") or {}
    if key == "actual":
        return nested.get("type") or nested.get("failure_type") or record.get("corrected_failure_type")
    diagnosis = record.get("diagnosis") or {}
    return diagnosis.get("type") or diagnosis.get("failure_type") or record.get("failure_type")


def diagnosis_metrics(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(records)
    reviewed = [row for row in rows if "diagnosis_correct" in row or "correct" in (row.get("diagnosis") or {})]
    correct = sum(bool(row.get("diagnosis_correct", (row.get("diagnosis") or {}).get("correct"))) for row in reviewed)
    labels = sorted({label for row in reviewed for label in (_label(row, "actual"), _label(row, "predicted")) if label})
    f1_values = []
    for label in labels:
        tp = sum(_label(row, "predicted") == label and _label(row, "actual") == label for row in reviewed)
        fp = sum(_label(row, "predicted") == label and _label(row, "actual") != label for row in reviewed)
        fn = sum(_label(row, "predicted") != label and _label(row, "actual") == label for row in reviewed)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1_values.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return {
        "reviewed_cases": len(reviewed),
        "correct_diagnoses": correct,
        "accuracy": correct / len(reviewed) if reviewed else 0.0,
        "macro_f1": sum(f1_values) / len(f1_values) if f1_values else 0.0,
        "labels": labels,
    }


__all__ = ["diagnosis_metrics"]

