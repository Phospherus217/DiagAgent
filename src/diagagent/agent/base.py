"""Base agent interface."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Union

from diagagent.actions.schema import BaseAction
from diagagent.observation.models import Observation


class BaseAgent(ABC):
    """Abstract base class for all GUI agents."""

    def __init__(self, name: str = "BaseAgent"):
        self.name = name
        self.task_spec: Optional[Dict[str, Any]] = None

    @abstractmethod
    def reset(self, task_spec: Dict[str, Any]) -> None:
        """Initializes or resets agent state for a new task."""
        self.task_spec = task_spec

    @abstractmethod
    def act(self, observation: Observation) -> Union[BaseAction, Dict[str, Any]]:
        """Selects the next action given current GUI observation."""
        pass
