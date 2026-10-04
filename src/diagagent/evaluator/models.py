"""Data models for step-level and artifact-level evaluation results."""

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class DiagnosticSignals(BaseModel):
    """Detailed boolean diagnostic signals extracted during step execution."""
    model_config = ConfigDict(extra="allow")

    action_valid: bool = True
    action_executable: bool = True
    target_hit: bool = True
    tool_match: bool = True
    parameter_match: bool = True
    dialog_confirmed: bool = True
    state_changed: Optional[bool] = None
    subgoal_completed: bool = True


class StepEvaluation(BaseModel):
    """Evaluation result for an individual execution step."""
    model_config = ConfigDict(extra="allow")

    step: int
    status: Optional[str] = None
    subgoal_id: Optional[str] = None
    subgoal_completed: bool = True
    diagnostic_signals: DiagnosticSignals = Field(default_factory=DiagnosticSignals)
    error_type: Optional[str] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None


class ArtifactEvaluation(BaseModel):
    """Evaluation result for the final output artifact."""
    model_config = ConfigDict(extra="allow")

    artifact_pass: bool = False
    status: str = "UNKNOWN"
    checks: list = Field(default_factory=list)
    output_exists: bool = False
    file_nonempty: bool = False
    format_correct: bool = False
    size_correct: bool = True
    content_correct: bool = True
    error_type: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None
