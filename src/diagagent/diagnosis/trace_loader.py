"""Load a persisted run into the v0.2 diagnostic trace contract.

The loader is deliberately read-only. It gathers the public run evidence
that the diagnosis loop is allowed to consume and does not read task gold
labels or mutate the run directory.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TraceBundle(BaseModel):
    """Normalized evidence bundle consumed by diagnosis components."""

    model_config = ConfigDict(extra="allow")

    run_id: str
    task_id: str = ""
    task: str = ""
    public_task: Dict[str, Any] = Field(default_factory=dict)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    screenshots: List[str] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)
    evaluator_results: List[Dict[str, Any]] = Field(default_factory=list)
    model_events: List[Dict[str, Any]] = Field(default_factory=list)
    execution_events: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_result: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    events: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_hashes: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_public_trace_alias(cls, value):
        if isinstance(value, dict) and "steps" not in value and "trace" in value:
            value = dict(value)
            value["steps"] = value["trace"]
        return value

    @property
    def trace(self) -> List[Dict[str, Any]]:
        """Compatibility alias used by the existing diagnosis engine."""
        return self.steps

def _read_json(path: Path, warnings: List[str]) -> Dict[str, Any]:
    if not path.exists():
        warnings.append(f"Missing {path.name}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Invalid JSON in {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"JSON in {path} is not an object")
    return payload


def _read_jsonl(path: Path, warnings: List[str]) -> List[Dict[str, Any]]:
    if not path.exists():
        warnings.append(f"Missing {path.name}")
        return []
    rows: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            raise ValueError(f"Invalid JSON in {path}:{line_number}") from error
        if not isinstance(row, dict):
            raise ValueError(f"Trace row in {path}:{line_number} is not an object")
        rows.append({**row, "_source": path.name, "_line": line_number})
    return rows


def load_trace(run_dir: Union[str, Path]) -> TraceBundle:
    """Load ``trace.jsonl`` and related evidence from a run directory.

    Missing optional files are reported in ``warnings``. Existing malformed
    evidence raises ``ValueError`` so callers cannot silently diagnose a
    partial or corrupted run.
    """

    root = Path(run_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Run directory not found: {root}")
    warnings: List[str] = []
    metadata = _read_json(root / "metadata.json", warnings)
    # Public requirements only: no gold subgoals or task evaluator contract.
    public = _read_json(root / "task_public.json", warnings) if (root / "task_public.json").exists() else {}

    trace = _read_jsonl(root / "trace.jsonl", warnings)
    events = _read_jsonl(root / "events.jsonl", warnings)
    model_events = [row for row in events if str(row.get("event", "")).startswith("model")]
    execution_events = [
        row for row in events
        if not str(row.get("event", "")).startswith("model")
    ]

    evaluation = _read_json(root / "evaluation.json", warnings)
    artifact_result = evaluation.get("artifact") if isinstance(evaluation.get("artifact"), dict) else {}
    if not artifact_result:
        artifact_result = _read_json(root / "artifact_result.json", warnings)
    run_id = str(metadata.get("run_id") or root.name)
    task = str(metadata.get("task_id") or public.get("task_id") or metadata.get("task") or "")
    return TraceBundle(
        run_id=run_id,
        task_id=task,
        task=task,
        public_task={key: public[key] for key in ("task_id", "instruction", "allowed_actions", "max_steps") if key in public},
        steps=trace,
        screenshots=sorted(str(path.relative_to(root)).replace("\\", "/") for path in (root / "screenshots").glob("*") if path.is_file()) if (root / "screenshots").exists() else [],
        artifacts=sorted(str(path.relative_to(root)).replace("\\", "/") for path in (root / "artifacts").glob("*") if path.is_file()) if (root / "artifacts").exists() else [],
        evaluator_results=([artifact_result] if artifact_result else []) + [row.get("step_eval", {}) for row in trace if row.get("step_eval")],
        model_events=model_events,
        execution_events=execution_events,
        artifact_result=artifact_result,
        warnings=warnings,
        metadata=metadata,
        events=events,
        source_hashes={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in (root / name for name in (
                           "metadata.json", "task_public.json", "trace.jsonl", "events.jsonl",
                           "evaluation.json", "artifact_result.json")) if path.is_file()},
    )
