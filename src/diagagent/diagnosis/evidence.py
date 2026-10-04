"""Load evidence without mutating original runs or accessing hidden task labels."""
import json
from pathlib import Path


def load_evidence(run_dir):
    root = Path(run_dir)
    warnings = []
    def rows(name):
        result = []
        path = root / name
        if not path.exists():
            warnings.append(f"Missing {name}")
            return result
        for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = json.loads(text)
                if not isinstance(row, dict):
                    raise ValueError("Not an object")
                result.append(row)
            except ValueError:
                warnings.append(f"Invalid {name}:{line}")
        return result
    trace, events = rows("trace.jsonl"), rows("events.jsonl")
    try:
        diagnosis = json.loads((root / "diagnosis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        diagnosis = {}
        warnings.append("Missing/invalid diagnosis.json")
    return trace, events, diagnosis, warnings
