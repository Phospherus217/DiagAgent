"""Schemas for explicit repair plans and attempt accounting."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RepairPlan(BaseModel):
    model_config = ConfigDict(extra="allow")

    plan_id: str
    parent_run: str
    parent_run_id: str
    diagnosis_sha256: str
    task_sha256: str
    feedback_file: str
    feedback_sha256: str
    feedback_source: str
    failure_type: Optional[str] = None
    repair_instruction: str = ""
    status: str
    strategy: str = ""
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    automatic_retry: bool = False
    target_step: Optional[int] = None
    action: Optional[Dict[str, Any]] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    verification: Dict[str, Any] = Field(default_factory=dict)


class AttemptRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    attempt_id: str
    attempt_number: int = Field(ge=1)
    plan_id: str
    parent_run_id: str
    status: str = "REGISTERED"
    counts_toward_attempt_total: bool = True
    attempt_policy: Dict[str, Any] = Field(default_factory=dict)


class RepairOutcome(AttemptRecord):
    status: str
    success: bool = False
    task_status: str = "UNKNOWN"
    failure_type: Optional[str] = None
    responsibility: Optional[str] = None
    run_id: Optional[str] = None
    run_dir: Optional[str] = None
