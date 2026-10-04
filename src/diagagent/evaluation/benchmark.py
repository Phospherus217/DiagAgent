"""Reproducible diagnostic benchmark for controlled and natural failures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union

import yaml

from diagagent.diagnosis.classifier import classify_bundle
from diagagent.diagnosis.trace_loader import TraceBundle, load_trace


def outcome_only(bundle: TraceBundle) -> Dict[str, Any]:
    artifact = bundle.artifact_result or {}
    if artifact.get("status") == "FAIL" or artifact.get("artifact_pass") is False:
        return {"failure_type": "Artifact Error", "first_failure_step": None}
    if artifact.get("status") == "PASS" or artifact.get("artifact_pass") is True:
        return {"failure_type": None, "first_failure_step": None}
    return {"failure_type": None, "first_failure_step": None}


def trace_only(bundle: TraceBundle) -> Dict[str, Any]:
    # Keep process evidence but remove final artifact evidence from the input.
    stripped = bundle.model_copy(update={"artifact_result": {}, "artifacts": [], "evaluator_results": []})
    result = classify_bundle(stripped)
    return {"failure_type": result.failure_type, "first_failure_step": result.first_failure_step}


def diagagent_baseline(bundle: TraceBundle) -> Dict[str, Any]:
    result = classify_bundle(bundle)
    return {"failure_type": result.failure_type, "first_failure_step": result.first_failure_step,
            "confidence": result.confidence_score, "diagnosis_id": result.diagnosis_id}


def _match(prediction: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    type_ok = prediction.get("failure_type") == expected.get("failure_type")
    expected_step = expected.get("first_failure_step")
    step_ok = expected_step is None or prediction.get("first_failure_step") == expected_step
    return type_ok and step_ok


def evaluate_cases(cases: Iterable[Dict[str, Any]], root: Union[str, Path] = ".") -> Dict[str, Any]:
    root = Path(root).resolve()
    rows: List[Dict[str, Any]] = []
    for case in cases:
        bundle = load_trace(root / case["run_dir"] if not Path(case["run_dir"]).is_absolute() else case["run_dir"])
        expected = {"failure_type": case.get("failure_type"), "first_failure_step": case.get("first_failure_step")}
        predictions = {
            "outcome_only": outcome_only(bundle),
            "trace_only": trace_only(bundle),
            "diagagent": diagagent_baseline(bundle),
        }
        rows.append({"case_id": case.get("case_id", bundle.run_id), "expected": expected,
                     "predictions": predictions,
                     "bundle_warnings": bundle.warnings})
    metrics = {}
    def macro_f1(baseline):
        labels = sorted(({row["expected"].get("failure_type") for row in rows} | {
            row["predictions"][baseline].get("failure_type") for row in rows
        }) - {None})
        scores = []
        for label in labels:
            tp = sum(row["expected"].get("failure_type") == label and row["predictions"][baseline].get("failure_type") == label for row in rows)
            fp = sum(row["expected"].get("failure_type") != label and row["predictions"][baseline].get("failure_type") == label for row in rows)
            fn = sum(row["expected"].get("failure_type") == label and row["predictions"][baseline].get("failure_type") != label for row in rows)
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
        return sum(scores) / len(scores) if scores else 0.0
    for baseline in ("outcome_only", "trace_only", "diagagent"):
        correct = sum(_match(row["predictions"][baseline], row["expected"]) for row in rows)
        metrics[baseline] = {"cases": len(rows), "correct": correct,
                             "accuracy": correct / len(rows) if rows else 0.0,
                             "macro_f1": macro_f1(baseline)}
    return {"schema_version": "0.4", "cases": rows, "metrics": metrics,
            "baselines": ["Outcome Only", "Trace Only", "DiagAgent"]}


def run_benchmark(manifest: Union[str, Path], output: Union[str, Path]) -> Path:
    manifest_path = Path(manifest).resolve()
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cases = data.get("cases", data) if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError("Benchmark manifest must contain a cases list")
    payload = evaluate_cases(cases, manifest_path.parent)
    target = Path(output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


__all__ = ["outcome_only", "trace_only", "diagagent_baseline", "evaluate_cases", "run_benchmark"]
