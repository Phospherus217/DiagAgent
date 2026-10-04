"""Verification result models for runtime assertion of GUI execution facts."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    """Holds runtime verification status, observable signals, and evidence."""

    passed: bool
    signals: Dict[str, Any] = Field(default_factory=dict)
    suspected_failure: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)
