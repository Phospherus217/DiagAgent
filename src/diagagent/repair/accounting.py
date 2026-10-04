"""Read-only helpers for v0.2 repair attempt accounting."""

import json
from pathlib import Path
from typing import Any, Dict, List, Union


def list_attempts(parent_run: Union[str, Path], plan_id: str) -> List[Dict[str, Any]]:
    """Return registration/outcome records in attempt-number order."""

    if not plan_id or any(token in plan_id for token in ("/", "\\", ":")) or plan_id in {".", ".."}:
        raise ValueError("plan_id must be a single directory name")
    folder = Path(parent_run) / "repairs" / "attempts" / plan_id
    records: List[Dict[str, Any]] = []
    if not folder.exists():
        return records
    for path in sorted(folder.glob("attempt_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Invalid attempt record: {path}") from error
        if not isinstance(payload, dict) or type(payload.get("attempt_number")) is not int or payload["attempt_number"] < 1:
            raise ValueError(f"Invalid attempt record: {path}")
        records.append(payload)
    return sorted(records, key=lambda item: (item.get("attempt_number", 0), item.get("status", "")))


def next_attempt_number(parent_run: Union[str, Path], plan_id: str) -> int:
    """Preview the next number; only runner's exclusive registration reserves it."""

    records = list_attempts(parent_run, plan_id)
    return max((int(item.get("attempt_number", 0)) for item in records), default=0) + 1


__all__ = ["list_attempts", "next_attempt_number"]
