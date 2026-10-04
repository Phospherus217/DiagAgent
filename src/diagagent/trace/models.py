"""Data models for execution traces, steps, and run summaries."""

from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TraceStepRecord(BaseModel):
    """Record of a single execution step within the trace (legacy-compatible)."""
    model_config = ConfigDict(extra="allow")

    step: int
    phase: str = "step"  # "reset", "step", "final"
    timestamp: float = Field(default_factory=time.time)
    action: Optional[Dict[str, Any]] = None
    lowered_actions: Optional[List[Dict[str, Any]]] = None
    obs_before: Optional[Dict[str, Any]] = None
    obs_after: Optional[Dict[str, Any]] = None
    execution_result: Optional[Dict[str, Any]] = None
    step_eval: Optional[Dict[str, Any]] = None
    done: bool = False
    error_type: Optional[str] = None
    note: Optional[str] = None


class TraceStep(BaseModel):
    """Complete structured trace step complying with DiagAgent v2.0 closed-loop specification."""
    model_config = ConfigDict(extra="allow")

    schema_version: str = "2.0"
    step: int

    observation_before: Optional[Dict[str, Any]] = None

    model_response: Optional[Dict[str, Any]] = None
    parsed_action: Optional[Dict[str, Any]] = None
    validation_result: Optional[Dict[str, Any]] = None

    lowered_actions: List[Dict[str, Any]] = Field(default_factory=list)

    execution_result: Optional[Dict[str, Any]] = None

    observation_after: Optional[Dict[str, Any]] = None
    runtime_verification: Optional[Dict[str, Any]] = None

    failure: Optional[Dict[str, Any]] = None
    recovery: Optional[Dict[str, Any]] = None

    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at: Optional[str] = None
    latency_ms: int = 0


class RunSummary(BaseModel):
    """Overall summary of a task execution run."""
    model_config = ConfigDict(extra="allow")

    task_id: str
    run_id: str
    success: bool = False
    task_status: str = "UNKNOWN"
    process_status: str = "UNKNOWN"
    artifact_status: str = "UNKNOWN"
    termination_reason: Optional[str] = None
    total_steps: int = 0
    first_failure_step: Optional[int] = None
    first_failure_type: Optional[str] = None
    responsibility: Optional[str] = None
    duration_seconds: float = 0.0
    start_time: str = ""
    end_time: str = ""
    artifact_path: Optional[str] = None
