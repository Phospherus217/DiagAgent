"""Base environment interface for GUI task execution."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

from diagagent.actions.schema import BaseAction
from diagagent.observation.models import Observation


class BaseEnvironment(ABC):
    """Abstract environment interface following a Gym-like lifecycle."""

    @abstractmethod
    def launch(self) -> None:
        """Launches or connects to the application process."""
        pass

    @abstractmethod
    def reset(self, task_spec: Dict[str, Any]) -> Observation:
        """Resets the environment with the specified task configuration."""
        pass

    @abstractmethod
    def step(self, action: Any) -> Tuple[Observation, float, bool, Dict[str, Any]]:
        """Executes an action, returning (observation, reward, done, info)."""
        pass

    @abstractmethod
    def observe(self, kind: str = "step") -> Observation:
        """Captures and returns the current observation."""
        pass

    @abstractmethod
    def evaluate_final(self) -> Dict[str, Any]:
        """Performs final artifact and session evaluation upon task termination."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Cleans up and terminates the environment session."""
        pass
