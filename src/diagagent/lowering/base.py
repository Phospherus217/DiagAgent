"""Base interface for semantic lowering engines."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from diagagent.actions.schema import BaseAction


class BaseLowerer(ABC):
    """Abstract base class for converting high-level semantic actions into lower-level GUI primitives."""

    @abstractmethod
    def lower(self, action: BaseAction, ui_primitives: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        """Lowers a semantic action into a list of executable sub-actions.

        Args:
            action: The high-level semantic action (A2 or A1).
            ui_primitives: Current UI layout and bounding box information.

        Returns:
            A list of lower-level actions (A1 or A0).
        """
        pass
