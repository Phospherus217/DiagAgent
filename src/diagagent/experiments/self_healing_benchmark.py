"""Offline accounting for the v1.0 self-healing benchmark.

The benchmark consumes persisted case outcomes.  It does not execute a GUI,
retry a task, or reinterpret synthetic records as real-GIMP evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Union

import yaml

from diagagent.metrics.recovery import compute_recovery_metrics


BASELINES = ("B0_no_repair", "B1_naive_retry", "B2_diagagent_healing")


def _case_records(cases: Iterable[Mapping[str, Any]], baseline: str) -> List[Dict[str, Any]]:
    rows = []
    for case in cases:
        outcome = case.get(baseline) or case.get("baselines", {}).get(baseline) or {}
        if not isinstance(outcome, Mapping):
            outcome = {}
        row = dict(case)
        row.update(outcome)
        row["case_id"] = case.get("case_id", "")
        row["baseline"] = baseline
        # A benchmark row must explicitly identify its policy.  In particular,
        # B1 cannot be counted as a diagnosis-guided repair.
        row.setdefault("repairability", "REPAIRABLE" if baseline != "B0_no_repair" else "NON_REPAIRABLE")
        row.setdefault("backend", case.get("backend", ""))
        rows.append(row)
    return rows


def evaluate_self_healing_cases(cases: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    materialized = list(cases)
    tables = {}
    for baseline in BASELINES:
        rows = _case_records(materialized, baseline)
        tables[baseline] = {"baseline": baseline, "cases": rows,
                            "metrics": compute_recovery_metrics(rows)}
    return {"schema_version": "1.0", "baselines": list(BASELINES), "results": tables}


def run_self_healing_benchmark(manifest: Union[str, Path], output: Union[str, Path]) -> Path:
    source = Path(manifest).resolve()
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    cases = data.get("cases", data) if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError("Self-healing benchmark manifest must contain a cases list")
    target = Path(output).resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evaluate_self_healing_cases(cases), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


__all__ = ["BASELINES", "evaluate_self_healing_cases", "run_self_healing_benchmark"]
