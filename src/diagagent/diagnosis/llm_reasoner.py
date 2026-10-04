"""Optional LLM explanation layer constrained by rule diagnosis evidence."""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import ValidationError

from diagagent.diagnosis.evidence_formatter import format_evidence
from diagagent.diagnosis.prompt import build_messages
from diagagent.schemas.diagnosis import DiagnosisResult, HybridDiagnosisResult
from diagagent.models.base import Message


class LLMReasoner:
    """Ask a model for explanation while rules retain failure authority."""

    def __init__(self, model):
        self.model = model

    def reason(self, bundle, rule_diagnosis: DiagnosisResult, graph=None) -> HybridDiagnosisResult:
        evidence = format_evidence(bundle, rule_diagnosis, graph)
        final_type = rule_diagnosis.failure_type or rule_diagnosis.type
        final_step = rule_diagnosis.first_failure_step if rule_diagnosis.first_failure_step is not None else rule_diagnosis.step
        final_confidence = rule_diagnosis.confidence_score
        final_explanation = rule_diagnosis.explanation
        suggestion = rule_diagnosis.suggested_repair
        response = None
        try:
            messages = [Message(**message) for message in build_messages(evidence)]
            response = self.model.query(messages)
            parsed = self._parse(response.text)
        except (Exception,):
            parsed = None
        if parsed:
            # The model may enrich language, but cannot override observed rule
            # type or step. This prevents unsupported causal hallucinations.
            if final_type is None and parsed.get("failure_type") is not None:
                parsed["failure_type"] = None
            if final_step is None:
                parsed["first_failure_step"] = None
            if parsed.get("explanation"):
                final_explanation = str(parsed["explanation"])
            if parsed.get("repair_suggestion"):
                suggestion = str(parsed["repair_suggestion"])
        return HybridDiagnosisResult(
            run_id=getattr(bundle, "run_id", ""), rule_diagnosis=rule_diagnosis,
            llm_available=bool(response), llm_failure_type=parsed.get("failure_type") if parsed else None,
            llm_first_failure_step=parsed.get("first_failure_step") if parsed else None,
            llm_confidence=parsed.get("confidence") if parsed else None,
            llm_explanation=parsed.get("explanation") if parsed else None,
            final_failure_type=final_type, final_first_failure_step=final_step,
            final_confidence=final_confidence, final_explanation=final_explanation,
            repair_suggestion=suggestion, evidence_bound=True,
        )

    diagnose = reason

    @staticmethod
    def _parse(text: str) -> Optional[dict]:
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        confidence = value.get("confidence")
        if confidence is not None:
            try:
                value["confidence"] = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                value["confidence"] = None
        return value


__all__ = ["LLMReasoner"]
