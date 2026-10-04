"""Execution result data models."""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    """Encapsulates the outcome of an executed action."""
    success: bool = True
    state_changed: Optional[bool] = None
    dispatched: bool = False
    effect_status: str = "UNKNOWN"
    action_executable: bool = True
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)
