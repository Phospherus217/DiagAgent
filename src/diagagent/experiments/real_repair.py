"""Explicit real-GIMP repair experiment runner.

The runner prepares auditable case directories. It never starts GIMP unless a
caller supplies an executor and explicitly enables execution; this prevents a
test or metrics command from creating an unrecorded desktop run.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

import yaml


REQUIRED_CASE_KEYS = {"case_id", "failure_type"}


def validate_case_manifest(cases: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    seen = set()
    for raw in cases:
        if not isinstance(raw, dict) or not REQUIRED_CASE_KEYS.issubset(raw) or not (raw.get("task_file") or raw.get("run_dir")):
            missing = REQUIRED_CASE_KEYS.difference(raw if isinstance(raw, dict) else set())
            if isinstance(raw, dict) and not (raw.get("task_file") or raw.get("run_dir")):
                missing.add("task_file|run_dir")
            raise ValueError(f"Real repair case is missing fields: {sorted(missing)}")
        case = dict(raw)
        if case["case_id"] in seen:
            raise ValueError(f"Duplicate real repair case: {case['case_id']}")
        seen.add(case["case_id"])
        if "first_failure_step" in case and type(case["first_failure_step"]) is not int and case["first_failure_step"] is not None:
            raise ValueError(f"first_failure_step must be an integer or null: {case['case_id']}")
        case.setdefault("backend", "real_gimp")
        case.setdefault("automatic_retry", False)
        if case["automatic_retry"] is not False:
            raise ValueError("Real repair experiments cannot enable automatic retry")
        normalized.append(case)
    if not 5 <= len(normalized) <= 10:
        raise ValueError(f"Real repair manifest must contain 5-10 cases, got {len(normalized)}")
    return normalized


def _write_json(path: Path, payload: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_real_repair_manifest(manifest: Union[str, Path], output_root: Union[str, Path],
                             executor: Optional[Callable[[Dict[str, Any], Path], Dict[str, Any]]] = None,
                             execute: bool = False) -> Path:
    manifest_path = Path(manifest).resolve()
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cases = validate_case_manifest(raw.get("cases", raw) if isinstance(raw, dict) else raw)
    if execute and executor is None:
        raise ValueError("An explicit real-GIMP executor is required when execute=True")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    records = []
    for case in cases:
        case_root = root / case["case_id"]
        case_root.mkdir(parents=True, exist_ok=True)
        for directory in ("trace", "screenshots", "artifacts"):
            (case_root / directory).mkdir(exist_ok=True)
        _write_json(case_root / "case.json", case)
        if execute:
            record = dict(executor(case, case_root))
            record.setdefault("status", "RECORDED")
            record["executed_at"] = datetime.now(timezone.utc).isoformat()
            for key, filename in (("diagnosis", "diagnosis.json"), ("evidence_graph", "evidence_graph.json"),
                                  ("feedback", "feedback.json"), ("repair_plan", "repair_plan.json"),
                                  ("replay_result", "replay_result.json")):
                if isinstance(record.get(key), dict):
                    _write_json(case_root / filename, record[key])
        else:
            record = {"status": "PLANNED", "backend": "real_gimp", "automatic_retry": False,
                      "reason": "Dry-run manifest preparation; no desktop execution requested."}
        _write_json(case_root / "result.json", record)
        records.append({"case_id": case["case_id"], **record})
    summary = root / "summary.json"
    _write_json(summary, {"schema_version": "0.4", "manifest": str(manifest_path),
                           "execute": execute, "cases": records,
                           "real_gimp_evidence": execute})
    return summary


__all__ = ["validate_case_manifest", "run_real_repair_manifest"]
