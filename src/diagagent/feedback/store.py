"""Append-only feedback bound to a specific diagnosis snapshot."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from diagagent.schemas.feedback import HumanFeedback
from diagagent.trace.redaction import Redactor


def diagnosis_hash(root):
    return hashlib.sha256((Path(root) / "diagnosis.json").read_bytes()).hexdigest()


def save_feedback(run_dir, feedback):
    root = Path(run_dir).resolve()
    if isinstance(feedback, dict) and "diagnosis" in feedback:
        from diagagent.feedback.collector import save_annotation
        return save_annotation(root, feedback)
    feedback = HumanFeedback.model_validate(feedback)
    if feedback.run_id != root.name or feedback.diagnosis_sha256 != diagnosis_hash(root):
        raise ValueError("Feedback does not match this run and diagnosis snapshot")
    folder = root / "feedback"
    folder.mkdir(exist_ok=True)
    target = folder / f"{uuid4().hex}.json"
    record = {**feedback.model_dump(), "feedback_id": target.stem,
              "created_at": datetime.now(timezone.utc).isoformat()}
    with target.open("x", encoding="utf-8") as stream:
        json.dump(Redactor().clean(record), stream, ensure_ascii=False, indent=2)
    return target
