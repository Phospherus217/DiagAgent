"""Typed failures at model, action parsing, and execution boundaries."""

from typing import Optional


class ModelAPIError(RuntimeError):
    """A failed model request, never an action or a model completion.

    Messages must be safe for persistence: do not pass raw HTTP bodies,
    request headers, credential-bearing URLs, or underlying exception text.
    """

    failure_layer = "MODEL"

    def __init__(self, message: str = "Model API request failed", *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = "Authentication Error" if status_code == 401 else "Model API Error"

    def to_dict(self):
        return {
            "failure_layer": self.failure_layer,
            "failure_type": self.error_type,
            "status_code": self.status_code,
            "message": str(self),
        }


class ActionParseError(ValueError):
    """A completion cannot be parsed into a supported action."""

    failure_layer = "ACTION"
    error_type = "Action Format Error"


class ExecutionError(RuntimeError):
    """An action failed at the execution boundary."""

    failure_layer = "EXECUTION"
    error_type = "Execution Error"
