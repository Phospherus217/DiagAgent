"""Append-only recovery session event log."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union
from uuid import uuid4


class RecoveryEventLog:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, *, recovery_session_id: str, state_from: str, state_to: str,
               event_type: str, payload_ref: str = "", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        record = {
            "schema_version": "1.0", "event_id": f"evt_{uuid4().hex}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "recovery_session_id": recovery_session_id, "state_from": state_from,
            "state_to": state_to, "event_type": event_type, "payload_ref": payload_ref,
        }
        if payload:
            record["payload"] = payload
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record
