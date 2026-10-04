"""LoweringEngine that coordinates lowerers for various target applications."""

from typing import Any, Dict, List, Optional, Union

from diagagent.actions.parser import ActionParser
from diagagent.actions.schema import BaseAction
from diagagent.lowering.base import BaseLowerer
from diagagent.lowering.gimp_lowering import GIMPLowerer


class LoweringEngine:
    """Manages application-specific lowering rules and coordinates action translation."""

    def __init__(self, app_name: str = "gimp", custom_lowerer: Optional[BaseLowerer] = None):
        self.app_name = app_name.lower().strip()
        if custom_lowerer:
            self.lowerer = custom_lowerer
        elif self.app_name == "gimp":
            self.lowerer = GIMPLowerer()
        else:
            self.lowerer = GIMPLowerer()

    def lower(
        self, action: Union[BaseAction, dict], ui_primitives: Optional[Dict[str, Any]] = None
    ) -> List[BaseAction]:
        """Lowers an action (Action model or dict) into atomic/structural actions."""
        if isinstance(action, dict):
            action_obj = ActionParser.parse(action)
        else:
            action_obj = action

        return self.lowerer.lower(action_obj, ui_primitives=ui_primitives)
