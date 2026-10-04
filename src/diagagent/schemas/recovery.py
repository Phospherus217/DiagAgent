"""Versioned schemas for the v1.0 bounded self-healing runtime."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HealingState(str, Enum):
    RUNNING = "running"
    FAILURE_DETECTED = "failure_detected"
    DIAGNOSING = "diagnosing"
    DIAGNOSED = "diagnosed"
    RECOVERY_DECISION = "recovery_decision"
    REPAIR_PLANNED = "repair_planned"
    AWAITING_APPROVAL = "awaiting_approval"
    REPAIR_EXECUTING = "repair_executing"
    VERIFYING = "verifying"
    RECOVERED = "recovered"
    NOT_RECOVERED = "not_recovered"
    ESCALATED = "escalated"
    TERMINATED = "terminated"


class Repairability(str, Enum):
    REPAIRABLE = "REPAIRABLE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    NON_REPAIRABLE = "NON_REPAIRABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class VerificationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_checks: List[str] = Field(default_factory=list)
    expected_state_change: Dict[str, Any] = Field(default_factory=dict)
    expected_artifact_change: Dict[str, Any] = Field(default_factory=dict)
    forbidden_side_effects: List[str] = Field(default_factory=list)
    task_success_required: bool = True


class RecoverySession(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    recovery_session_id: str = Field(default_factory=lambda: f"recovery_{uuid4().hex}")
    run_id: str
    task_id: str
    trigger_step: Optional[int] = None
    trigger_reason: str
    diagnosis_id: str = ""
    failure_type: Optional[str] = None
    first_failure_step: Optional[int] = None
    diagnosis_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    repairability: Repairability = Repairability.INSUFFICIENT_EVIDENCE
    repairability_reason: str = ""
    repair_plan_id: Optional[str] = None
    repair_attempt_ids: List[str] = Field(default_factory=list)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    diagnosis: Dict[str, Any] = Field(default_factory=dict)
    proposed_repair: Optional[Dict[str, Any]] = None
    guard_decision: Optional[Dict[str, Any]] = None
    execution_result: Optional[Dict[str, Any]] = None
    verification_result: Optional[Dict[str, Any]] = None
    state: HealingState = HealingState.FAILURE_DETECTED
    requires_human_approval: bool = False
    approved_by_human: Optional[bool] = None
    recovery_success: Optional[bool] = None
    recovery_reason: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


class RecoveryPolicyInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    diagnosis: Any
    evidence_graph: Dict[str, Any] = Field(default_factory=dict)
    task_spec: Dict[str, Any] = Field(default_factory=dict)
    current_state: Dict[str, Any] = Field(default_factory=dict)
    feedback: Optional[Any] = None


class RecoveryPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid4().hex}")
    diagnosis_id: str = ""
    repairability: Repairability
    repairability_reason: str
    repair_type: Optional[str] = None
    target_step: Optional[int] = None
    restart_mode: Optional[str] = None
    changes: Dict[str, Any] = Field(default_factory=dict)
    repair_action: Optional[Dict[str, Any]] = None
    evidence_node_ids: List[str] = Field(default_factory=list)
    requires_human_approval: bool = False
    verification_contract: VerificationContract = Field(default_factory=VerificationContract)
    forbidden_side_effects: List[str] = Field(default_factory=list)


class RecoveryVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    process_recovered: Optional[bool] = None
    artifact_recovered: Optional[bool] = None
    task_recovered: Optional[bool] = None
    violated_side_effects: List[str] = Field(default_factory=list)
    recovered: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: List[str] = Field(default_factory=list)
    reason: str = ""
    unmet_checks: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def recovered_requires_all_checks(self):
        if self.recovered and not (
            self.process_recovered is True
            and self.artifact_recovered is True
            and self.task_recovered is True
            and not self.violated_side_effects
            and not self.unmet_checks
        ):
            raise ValueError("recovered=True requires process, artifact and task checks plus no side effects")
        return self


class HealingRunResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    recovery_session_id: str
    state: HealingState
    executed: bool = False
    recovery_success: Optional[bool] = None
    reason: str = ""
    session_path: Optional[str] = None
    outcome: Optional[str] = None
