"""Machine-readable v0.2 diagnosis schema."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DiagnosisResult(BaseModel):
    """Evidence-backed diagnosis returned by the classifier."""

    model_config = ConfigDict(extra="allow")

    diagnosis_version: str = "0.2"
    diagnosis_id: str = ""
    run_id: str = ""
    first_failure_step: Optional[int] = None
    failure_type: Optional[str] = None
    responsibility: Dict[str, Any] = Field(default_factory=dict)
    suggested_repair: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    failure: bool = False
    step: Optional[int] = None
    type: Optional[str] = None
    layer: Optional[str] = None
    confidence: str = "unknown"
    confidence_score: float = 0.0
    explanation: str = ""
    v02_attribution: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    diagnosis_status: str = "INSUFFICIENT_EVIDENCE"


class HybridDiagnosisResult(BaseModel):
    """Rule-guarded diagnosis with optional language-model reasoning."""

    schema_version: str = "0.4"
    run_id: str = ""
    rule_diagnosis: DiagnosisResult
    llm_available: bool = False
    llm_failure_type: Optional[str] = None
    llm_first_failure_step: Optional[int] = None
    llm_confidence: Optional[float] = None
    llm_explanation: Optional[str] = None
    final_failure_type: Optional[str] = None
    final_first_failure_step: Optional[int] = None
    final_confidence: float = 0.0
    final_explanation: str = ""
    repair_suggestion: Optional[str] = None
    evidence_bound: bool = True
