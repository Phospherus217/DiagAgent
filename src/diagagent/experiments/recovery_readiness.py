"""Readiness accounting for admission to formal real-GIMP recovery E2E."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Union

import yaml

from diagagent.experiments.reference_recovery import REPAIR_TYPES, reference_action, _compile_action


def assess_case(case: Mapping[str, Any], reference_root: Union[str, Path]) -> Dict[str, Any]:
    case_id = str(case.get("case_id", "")); root = Path(reference_root).resolve() / case_id
    probe_path = root / "reference_probe.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.exists() else {}
    failure = str(case.get("failure_type", ""))
    if probe.get("status") == "READY" and probe.get("task_pass") is True:
        status = "READY"; reason = "Reference policy, lowering, execution, verification and artifact evidence are complete."
    elif probe.get("status") == "READY":
        status = "EVIDENCE_INSUFFICIENT"; reason = "Executor and artifact checks passed, but independent task completion was not established."
    elif probe.get("status") == "BACKEND_NOT_QUALIFIED":
        status = "BACKEND_NOT_QUALIFIED"; reason = probe.get("reason", "Real-GIMP backend did not qualify.")
    elif failure == "Tool Selection Error":
        status = "BACKEND_NOT_QUALIFIED"
        reason = "The calibrated profile has no machine-verifiable active-tool signal; keyboard dispatch is proxy-only."
    elif failure == "Canvas Target Error":
        status = "EVIDENCE_INSUFFICIENT"
        reason = "Image-to-canvas mapping and dimension proxy are implemented, but target location needs real evidence."
    elif failure in REPAIR_TYPES:
        try:
            _compile_action(case, reference_action(case))
            status = "EVIDENCE_INSUFFICIENT"
            reason = "Lowering is structurally available; no completed reference real-GIMP evidence package exists."
        except Exception as error:
            status = "LOWERING_MISSING"
            reason = str(error)
    else:
        status = "LOWERING_MISSING"; reason = "No registered v1.0 recovery lowering exists."
    return {"case_id": case_id, "failure_type": failure,
            "repair_type": REPAIR_TYPES.get(failure), "status": status, "reason": reason,
            "reference_probe": str(probe_path) if probe_path.exists() else None,
            "retry_used": bool(probe.get("retry_used", False)),
            "lowering_success": probe.get("lowering_success", False),
            "execution_success": probe.get("execution_success", False),
            "verification_success": probe.get("verification_success", False),
            "artifact_pass": probe.get("artifact_pass", False)}


def assess_manifest(manifest: Union[str, Path], reference_root: Union[str, Path]) -> List[Dict[str, Any]]:
    path = Path(manifest).resolve(); raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = raw.get("cases", raw) if isinstance(raw, dict) else raw
    return [assess_case(case, reference_root) for case in cases]


def write_readiness_report(manifest: Union[str, Path], output: Union[str, Path], reference_root: Union[str, Path]) -> Path:
    rows = assess_manifest(manifest, reference_root)
    target = Path(output).resolve(); target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# v1.0 Real-GIMP Recovery Readiness", "", "This report is an admission gate, not formal E2E evidence. DRY_RUN probes are excluded from real-GIMP claims and denominators.", "",
             "| Case | Failure | Repair type | Status | Reason |", "|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['case_id']} | {row['failure_type']} | {row.get('repair_type') or '—'} | {row['status']} | {row['reason']} |")
    lines.extend(["", "## Gate conditions", "", "A case is READY only when reference policy, lowering, real-GIMP execution, independent verification, artifact/process evidence, and `retry_used=false` are all recorded."])
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


__all__ = ["assess_case", "assess_manifest", "write_readiness_report"]
