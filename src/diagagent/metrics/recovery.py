"""Recovery metrics and report generation for the v1.0 benchmark.

The implementation is intentionally tolerant of records produced by both the
planned manifest format and executed case directories.  It never treats a
synthetic or dry-run record as ``real_gimp`` evidence unless the record states
that backend explicitly.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

import yaml


def _read(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(*values: Any, default: float = 0.0) -> float:
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return default


def _truth(*values: Any) -> Optional[bool]:
    for value in values:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
    return None


def _f1(labels: Iterable[str], predictions: Iterable[str]) -> float:
    gold = list(labels); pred = list(predictions)
    classes = set(gold) | set(pred)
    if not classes:
        return 0.0
    scores = []
    for label in classes:
        tp = sum(g == label and p == label for g, p in zip(gold, pred))
        fp = sum(g != label and p == label for g, p in zip(gold, pred))
        fn = sum(g == label and p != label for g, p in zip(gold, pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(scores) / len(scores)


def load_recovery_manifest(manifest: Union[str, Path]) -> List[Dict[str, Any]]:
    """Load and materialize case records from a v1.0 YAML/JSON manifest."""
    path = Path(manifest).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = raw.get("cases", []) if isinstance(raw, dict) else raw
    if not isinstance(cases, list):
        raise ValueError("Recovery manifest must contain a cases list")
    rows: List[Dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        case_root = Path(row.get("run_dir") or row.get("case_dir") or "")
        if not case_root.is_absolute():
            case_root = (path.parent / case_root).resolve()
        row["_case_root"] = str(case_root)
        result = _read(case_root / "result.json") if case_root.is_dir() else {}
        for key, value in result.items():
            row.setdefault(key, value)
        for key, filename in (("diagnosis", "diagnosis.json"), ("repair_plan", "repair_plan.json"),
                              ("replay_result", "replay_result.json"), ("verification", "verification.json")):
            if key not in row and case_root.is_dir():
                value = _read(case_root / filename)
                if value:
                    row[key] = value
        rows.append(row)
    return rows


def _normalize_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    diagnosis = row.get("diagnosis") if isinstance(row.get("diagnosis"), Mapping) else {}
    verification = row.get("verification") if isinstance(row.get("verification"), Mapping) else {}
    replay = row.get("replay_result") if isinstance(row.get("replay_result"), Mapping) else {}
    repairability = str(row.get("repairability") or diagnosis.get("repairability") or "").upper()
    success = _truth(row.get("recovery_success"), row.get("recovered"), replay.get("recovered"), row.get("success"))
    if success is None and str(row.get("status", "")).upper() in {"SUCCESS", "RECOVERED", "PASS"}:
        success = True
    predicted = _truth(row.get("predicted_recovered"), verification.get("recovered"), row.get("system_recovered"))
    if predicted is None:
        predicted = success
    autonomous = _truth(row.get("autonomous"))
    if autonomous is None:
        autonomous = str(row.get("repair_mode", "")).lower() == "autonomous" and not bool(
            row.get("requires_human_approval", False))
    side_effects = row.get("side_effects") or verification.get("violated_side_effects") or []
    if not isinstance(side_effects, list):
        side_effects = [side_effects]
    expected_type = row.get("failure_type")
    predicted_type = diagnosis.get("failure_type") or diagnosis.get("type") or row.get("predicted_failure_type")
    expected_step = row.get("first_failure_step")
    predicted_step = diagnosis.get("first_failure_step", diagnosis.get("step"))
    localized = expected_step is not None and predicted_step == expected_step
    return {"case_id": row.get("case_id", ""), "backend": row.get("backend", ""),
            "repairability": repairability, "success": bool(success), "success_known": success is not None,
            "predicted_recovered": bool(predicted),
            "autonomous": bool(autonomous), "side_effect": bool(side_effects), "side_effects": side_effects,
            "localized": localized, "expected_step": expected_step, "predicted_step": predicted_step,
            "expected_type": expected_type, "predicted_type": predicted_type,
            "extra_gui_actions": _number(row.get("extra_gui_actions"), row.get("extra_actions")),
            "extra_model_calls": _number(row.get("extra_model_calls")),
            "wall_time_seconds": _number(row.get("wall_time_seconds"), row.get("recovery_wall_time_seconds")),
            "human_interventions": _number(row.get("human_interventions"), 1 if not autonomous else 0),
            "restart_count": _number(row.get("restart_count")),}


def compute_recovery_metrics(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = [_normalize_row(row) for row in records]
    repairable = [r for r in rows if r["repairability"] == "REPAIRABLE"]
    autonomous = [r for r in rows if r["autonomous"]]
    attempts = [r for r in rows if r["repairability"] in {"REPAIRABLE", "HUMAN_REVIEW_REQUIRED"}]
    predicted_recoveries = [r for r in attempts if r["predicted_recovered"]]
    real = [r for r in rows if r["backend"] == "real_gimp"]
    real_repairable = [r for r in real if r["repairability"] == "REPAIRABLE"]
    successful = sum(r["success"] for r in repairable)
    auto_successful = sum(r["success"] for r in autonomous)
    localized = [r for r in rows if r["expected_type"] is not None]
    metrics = {
        "schema_version": "1.0", "case_count": len(rows), "real_gimp_case_count": len(real),
        "repairable_failed_runs": len(repairable), "successful_recoveries": successful,
        "recovery_success_rate": successful / len(repairable) if repairable else 0.0,
        "autonomous_recovery_attempts": len(autonomous), "successful_autonomous_recoveries": auto_successful,
        "autonomous_recovery_rate": auto_successful / len(autonomous) if autonomous else 0.0,
        "predicted_recoveries": len(predicted_recoveries),
        "recovery_precision": sum(r["success"] for r in predicted_recoveries) / len(predicted_recoveries) if predicted_recoveries else 0.0,
        "side_effect_rate": sum(r["side_effect"] for r in attempts) / len(attempts) if attempts else 0.0,
        "localization_accuracy": sum(r["localized"] for r in localized) / len(localized) if localized else 0.0,
        "diagnosis_macro_f1": _f1([r["expected_type"] for r in localized], [r["predicted_type"] for r in localized]),
        "recovery_cost": {"extra_gui_actions": sum(r["extra_gui_actions"] for r in rows),
                          "extra_model_calls": sum(r["extra_model_calls"] for r in rows),
                          "wall_time_seconds": sum(r["wall_time_seconds"] for r in rows),
                          "human_interventions": sum(r["human_interventions"] for r in rows),
                          "restart_count": sum(r["restart_count"] for r in rows)},
        "real_gimp_denominators": {"repairable": len(real_repairable),
                                   "successful": sum(r["success"] for r in real_repairable)},
        "rows": rows,
    }
    return metrics


def write_recovery_report(manifest: Union[str, Path], output_dir: Optional[Union[str, Path]] = None) -> Dict[str, Path]:
    path = Path(manifest).resolve()
    out = Path(output_dir).resolve() if output_dir else path.parent
    out.mkdir(parents=True, exist_ok=True)
    metrics = compute_recovery_metrics(load_recovery_manifest(path))
    json_path = out / "recovery_metrics.json"
    csv_path = out / "recovery_metrics.csv"
    md_path = out / "recovery_report.md"
    json_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = metrics.pop("rows")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(rows[0]) if rows else ["case_id", "success"])
        writer.writeheader(); writer.writerows(rows)
    metrics["rows"] = rows
    summary = "\n".join(f"- **{key}**: {value}" for key, value in metrics.items() if key != "recovery_cost")
    md_path.write_text(f"# Recovery Metrics\n\n{summary}\n\n## Recovery Cost\n\n{metrics['recovery_cost']}\n", encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "report": md_path}


# Friendly aliases used by experiment scripts and notebooks.
recovery_metrics = compute_recovery_metrics
metrics_from_manifest = compute_recovery_metrics


__all__ = ["load_recovery_manifest", "compute_recovery_metrics", "recovery_metrics",
           "metrics_from_manifest", "write_recovery_report"]
