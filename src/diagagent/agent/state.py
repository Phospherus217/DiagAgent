"""Runtime state models for tracking closed-loop agent execution and budget limits."""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class RuntimeState(BaseModel):
    """Encapsulates mutable state throughout an agent run."""

    run_id: str
    task_id: str
    step: int = 0
    history: List[Dict[str, Any]] = Field(default_factory=list)
    last_observation: Optional[Any] = None
    status: Literal["running", "success", "failed", "aborted", "max_steps"] = "running"

    # Recovery and execution budgets
    retry_budget: int = 1
    repair_budget: int = 1
    replan_budget: int = 2

    # Usage counters
    total_retries: int = 0
    total_repairs: int = 0
    total_replans: int = 0

    # Intermediate artifacts
    latest_action: Optional[Any] = None
    latest_verification: Optional[Any] = None
    latest_recovery: Optional[Any] = None
    termination_reason: Optional[str] = None
