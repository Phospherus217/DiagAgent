"""Recovery models for bounded automated error handling and self-correction."""

from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel


class RecoveryDecision(BaseModel):
    """Result of evaluating a recovery policy given runtime failures."""

    decision: Literal[
        "continue",
        "retry",
        "repair_format",
        "reobserve",
        "reopen_dialog",
        "replan",
        "abort",
    ]
    reason: str
    action_hint: Optional[str] = None
    consumed_budget: Optional[str] = None
    feedback_for_model: Optional[Dict[str, Any]] = None
