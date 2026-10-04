"""Failure taxonomy definitions and 5-way responsibility attribution."""

from enum import Enum
from typing import Dict, List


class FailureCategory(str, Enum):
    """The 5 top-level responsibility categories."""
    AGENT = "Agent"
    EXECUTION = "Execution"
    ARTIFACT = "Artifact"
    ENVIRONMENT = "Environment"
    EVALUATOR = "Evaluator"


class FailureType(str, Enum):
    # Agent Failures
    ACTION_FORMAT_ERROR = "Action Format Error"
    TOOL_SELECTION_ERROR = "Tool Selection Error"
    PARAMETER_ERROR = "Parameter Error"
    CANVAS_TARGET_ERROR = "Canvas Target Error"
    FALSE_COMPLETION = "False Completion"

    # Execution Failures
    UI_GROUNDING_ERROR = "UI Grounding Error"
    DIALOG_OPERATION_ERROR = "Dialog Operation Error"
    EXECUTION_NOOP_ERROR = "Execution No-op Error"
    FILE_IO_ERROR = "File I/O Error"

    # Artifact Failures
    ARTIFACT_ERROR = "Artifact Error"

    # Environment Failures
    ENVIRONMENT_ERROR = "Environment Error"
    LAUNCH_ERROR = "Launch / Window Error"
    AUTHENTICATION_ERROR = "Authentication Error"
    MODEL_API_ERROR = "Model API Error"

    # Evaluator Failures
    EVALUATOR_ERROR = "Evaluator Error"


FAILURE_RESPONSIBILITY_MAP: Dict[str, FailureCategory] = {
    "Execution Error": FailureCategory.EXECUTION,
    FailureType.ACTION_FORMAT_ERROR.value: FailureCategory.AGENT,
    FailureType.TOOL_SELECTION_ERROR.value: FailureCategory.AGENT,
    FailureType.PARAMETER_ERROR.value: FailureCategory.AGENT,
    FailureType.CANVAS_TARGET_ERROR.value: FailureCategory.AGENT,
    FailureType.FALSE_COMPLETION.value: FailureCategory.AGENT,

    FailureType.UI_GROUNDING_ERROR.value: FailureCategory.EXECUTION,
    FailureType.DIALOG_OPERATION_ERROR.value: FailureCategory.EXECUTION,
    FailureType.EXECUTION_NOOP_ERROR.value: FailureCategory.EXECUTION,
    FailureType.FILE_IO_ERROR.value: FailureCategory.EXECUTION,

    FailureType.ARTIFACT_ERROR.value: FailureCategory.ARTIFACT,

    FailureType.ENVIRONMENT_ERROR.value: FailureCategory.ENVIRONMENT,
    FailureType.LAUNCH_ERROR.value: FailureCategory.ENVIRONMENT,
    # Service access failures retain the existing five-way responsibility model.
    FailureType.AUTHENTICATION_ERROR.value: FailureCategory.ENVIRONMENT,
    FailureType.MODEL_API_ERROR.value: FailureCategory.ENVIRONMENT,

    FailureType.EVALUATOR_ERROR.value: FailureCategory.EVALUATOR,
}

# Strict root-cause precedence order (earlier items take priority if multiple errors coincide)
FAILURE_PRECEDENCE: List[str] = [
    FailureType.ENVIRONMENT_ERROR.value,
    FailureType.LAUNCH_ERROR.value,
    FailureType.AUTHENTICATION_ERROR.value,
    FailureType.MODEL_API_ERROR.value,
    FailureType.ACTION_FORMAT_ERROR.value,
    FailureType.UI_GROUNDING_ERROR.value,
    FailureType.TOOL_SELECTION_ERROR.value,
    FailureType.PARAMETER_ERROR.value,
    FailureType.CANVAS_TARGET_ERROR.value,
    FailureType.DIALOG_OPERATION_ERROR.value,
    FailureType.EXECUTION_NOOP_ERROR.value,
    FailureType.FILE_IO_ERROR.value,
    FailureType.FALSE_COMPLETION.value,
    FailureType.ARTIFACT_ERROR.value,
    FailureType.EVALUATOR_ERROR.value,
]


def get_responsibility(failure_type: str) -> FailureCategory:
    """Returns the top-level failure responsibility category."""
    clean = str(failure_type).strip()
    return FAILURE_RESPONSIBILITY_MAP.get(clean)


def get_precedence_rank(failure_type: str) -> int:
    """Returns precedence index for sorting root causes."""
    try:
        return FAILURE_PRECEDENCE.index(failure_type)
    except ValueError:
        return len(FAILURE_PRECEDENCE)
