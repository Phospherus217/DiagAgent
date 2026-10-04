"""Replay verification records for explicit repair attempts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Union


def _read_result(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "result.json"
    if not path.exists():
        return {"success": False, "status": "UNKNOWN", "missing": "result.json"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"success": False, "status": "UNKNOWN", "invalid": "result.json"}
    return {"success": bool(value.get("success", False)), "status": value.get("task_status", "UNKNOWN"),
            "run_id": value.get("run_id"), "termination_reason": value.get("termination_reason")}


def verify_repair(parent_run: Union[str, Path], repair_run: Union[str, Path]) -> Dict[str, Any]:
    """Compare persisted parent and repair outcomes without executing anything."""

    before_root, after_root = Path(parent_run).resolve(), Path(repair_run).resolve()
    before, after = _read_result(before_root), _read_result(after_root)
    payload = {
        "schema_version": "0.3",
        "verification_id": hashlib.sha256(f"{before_root}|{after_root}".encode()).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parent_run_id": before_root.name,
        "repair_run_id": after_root.name,
        "before": before,
        "after": after,
        "improved": not before.get("success", False) and after.get("success", False),
        "same_evaluator_contract": (before_root / "task_spec.yaml").exists() and (after_root / "task_spec.yaml").exists(),
    }
    target = after_root / "replay_verification.json"
    if not target.exists():
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


__all__ = ["verify_repair"]

