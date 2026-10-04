"""Stable v0.2 schema entry points for diagnosis, feedback and repair."""

from diagagent.schemas.diagnosis import DiagnosisResult
from diagagent.schemas.feedback import FeedbackAnnotation, HumanFeedback
from diagagent.schemas.repair import AttemptRecord, RepairPlan, RepairOutcome
from diagagent.schemas.recovery import (
    HealingState, HealingRunResult, RecoveryPolicyDecision, RecoveryPolicyInput,
    RecoverySession, RecoveryVerificationResult, Repairability, VerificationContract,
)

__all__ = ["DiagnosisResult", "HumanFeedback", "FeedbackAnnotation", "RepairPlan", "AttemptRecord", "RepairOutcome",
           "HealingState", "HealingRunResult", "RecoveryPolicyDecision", "RecoveryPolicyInput",
           "RecoverySession", "RecoveryVerificationResult", "Repairability", "VerificationContract"]
