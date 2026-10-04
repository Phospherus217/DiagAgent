"""Base executor interface."""

from abc import ABC, abstractmethod
from diagagent.actions.schema import BaseAction
from diagagent.executor.models import ExecutionResult


class BaseExecutor(ABC):
    """Abstract interface for executing lowered GUI primitives."""

    @abstractmethod
    def execute(self, action: BaseAction) -> ExecutionResult:
        """Executes an action and returns the execution result.

        Args:
            action: The action to execute.

        Returns:
            ExecutionResult detailing success, state change, and errors.
        """
        pass
