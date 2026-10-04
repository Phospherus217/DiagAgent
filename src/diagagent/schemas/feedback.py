"""Human feedback schema used by the repair loop."""

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from diagagent.diagnosis.responsibility import RESPONSIBILITY


class HumanFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str
    diagnosis_sha256: str
    diagnosis_correct: bool
    corrected_failure_type: Optional[str] = None
    corrected_step: Optional[int] = Field(default=None, ge=0)
    repair_instruction: str = Field(min_length=1, max_length=10000)
    author: str = Field(min_length=1, max_length=200)
    source: Literal["human", "test_fixture"] = "human"

    @model_validator(mode="after")
    def correction_required(self):
        if not self.diagnosis_correct and not self.corrected_failure_type:
            raise ValueError("A rejected diagnosis requires corrected_failure_type")
        if self.corrected_failure_type and self.corrected_failure_type not in RESPONSIBILITY:
            raise ValueError("Unknown corrected failure type")
        if self.diagnosis_correct and (self.corrected_failure_type or self.corrected_step is not None):
            raise ValueError("An accepted diagnosis cannot also correct its type or step")
        return self


class FeedbackAnnotation(BaseModel):
    """v0.3 nested annotation format used by review tools."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    run_id: str
    diagnosis: Dict[str, Any] = Field(default_factory=dict)
    human_label: Dict[str, Any] = Field(default_factory=dict)
    repair_instruction: Dict[str, Any] = Field(default_factory=dict)
    author: str = "anonymous"
    feedback_time: Optional[str] = None
    feedback_id: Optional[str] = None

    @property
    def diagnosis_correct(self) -> bool:
        return bool(self.diagnosis.get("correct", False))

    def repair_text(self) -> str:
        value = self.repair_instruction
        if isinstance(value, dict):
            return str(value.get("change") or value.get("instruction") or value.get("text") or value)
        return str(value)

