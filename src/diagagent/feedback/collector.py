"""Append-only storage for v0.3 human diagnosis annotations."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Union
from uuid import uuid4

from diagagent.schemas.feedback import FeedbackAnnotation
from diagagent.trace.redaction import Redactor


def save_annotation(run_dir: Union[str, Path], annotation) -> Path:
    root = Path(run_dir).resolve()
    parsed = FeedbackAnnotation.model_validate(annotation)
    if parsed.run_id != root.name:
        raise ValueError("Feedback run_id does not match the run directory")
    diagnosis_path = root / "diagnosis.json"
    digest = hashlib.sha256(diagnosis_path.read_bytes()).hexdigest() if diagnosis_path.exists() else None
    if parsed.diagnosis.get("sha256") and parsed.diagnosis["sha256"] != digest:
        raise ValueError("Feedback diagnosis snapshot does not match the run")
    record = parsed.model_dump()
    record.update({
        "feedback_id": parsed.feedback_id or uuid4().hex,
        "created_at": parsed.feedback_time or datetime.now(timezone.utc).isoformat(),
        "diagnosis_sha256": digest,
    })
    folder = root / "feedback" / "annotations"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{record['feedback_id']}.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(Redactor().clean(record), stream, ensure_ascii=False, indent=2)
    return target


def load_annotations(run_dir: Union[str, Path]):
    folder = Path(run_dir).resolve() / "feedback" / "annotations"
    if not folder.exists():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]


__all__ = ["save_annotation", "load_annotations"]

