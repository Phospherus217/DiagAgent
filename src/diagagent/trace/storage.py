"""Storage utilities for reading, loading, and querying recorded traces."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from diagagent.trace.models import RunSummary, TraceStepRecord


class TraceReader:
    """Reads execution traces and diagnostic logs from a run directory or file."""

    def __init__(self, target_path: Union[str, Path]):
        path = Path(target_path).resolve()
        if path.is_file() and path.name == "trace.jsonl":
            self.run_dir = path.parent
            self.trace_file = path
        elif path.is_dir():
            self.run_dir = path
            self.trace_file = path / "trace.jsonl"
        else:
            raise FileNotFoundError(f"Run directory or trace file not found at: {path}")

        self.result_file = self.run_dir / "result.json"
        self.diagnosis_file = self.run_dir / "diagnosis.json"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.artifacts_dir = self.run_dir / "artifacts"

    def read_steps(self) -> List[TraceStepRecord]:
        """Reads all steps from trace.jsonl as TraceStepRecord objects."""
        if not self.trace_file.exists():
            return []

        steps = []
        with open(self.trace_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    steps.append(TraceStepRecord(**data))
                except Exception as error:
                    raise ValueError("Incomplete or corrupt trace; cannot silently omit evidence") from error
        return steps

    def read_result(self) -> Optional[Dict[str, Any]]:
        """Reads result.json if present."""
        if not self.result_file.exists():
            return None
        with open(self.result_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def read_diagnosis(self) -> Optional[Dict[str, Any]]:
        """Reads diagnosis.json if present."""
        if not self.diagnosis_file.exists():
            return None
        with open(self.diagnosis_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_screenshots(self) -> List[Path]:
        """Lists all screenshot image files ordered by step."""
        if not self.screenshots_dir.exists():
            return []
        return sorted(list(self.screenshots_dir.glob("*.png")))
