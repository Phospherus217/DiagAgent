"""Diagnosis package for DiagAgent."""

from diagagent.diagnosis.taxonomy import (
    FailureCategory,
    FailureType,
    FAILURE_PRECEDENCE,
    FAILURE_RESPONSIBILITY_MAP,
    get_responsibility,
)
from diagagent.diagnosis.first_failure import FirstFailureLocator
from diagagent.diagnosis.report import DiagnosticReport, StepExecutionSummary
from diagagent.diagnosis.engine import DiagnosisEngine
from diagagent.diagnosis.trace_loader import TraceBundle, load_trace

__all__ = [
    "FailureCategory",
    "FailureType",
    "FAILURE_PRECEDENCE",
    "FAILURE_RESPONSIBILITY_MAP",
    "get_responsibility",
    "FirstFailureLocator",
    "DiagnosticReport",
    "StepExecutionSummary",
    "DiagnosisEngine",
    "TraceBundle",
    "load_trace",
]
