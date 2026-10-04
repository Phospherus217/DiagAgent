"""Fault-injecting agent for testing diagnostic precision and failure attribution."""

from typing import Any, Dict, Optional, Union

from diagagent.actions.schema import (
    AddTextAction,
    BaseAction,
    SelectToolAction,
    StopAction,
)
from diagagent.agent.base import BaseAgent
from diagagent.agent.rule_agent import RuleAgent
from diagagent.observation.models import Observation


class MockFaultAgent(BaseAgent):
    """Injects controlled failures into benchmark runs to verify diagnostic accuracy."""

    def __init__(self, fault_type: str = "parameter", fault_step: int = 3, name: str = "MockFaultAgent"):
        super().__init__(name=name)
        self.fault_type = fault_type.lower().strip()
        self.fault_step = fault_step
        self.current_step = 0
        self.oracle_agent = RuleAgent()

    def reset(self, task_spec: Dict[str, Any]) -> None:
        self.task_spec = task_spec
        subgoals = task_spec.get("subgoals", [])
        has_open_image = bool(subgoals) and subgoals[0].get("id") == "open_image"
        # If open_image was processed on reset, agent's first action is step 2
        self.current_step = 1 if has_open_image else 0
        self.oracle_agent.reset(task_spec)

    def act(self, observation: Observation) -> Union[BaseAction, Dict[str, Any]]:
        self.current_step += 1
        base_act = self.oracle_agent.act(observation)

        # Inject failure when reaching specified fault step
        if self.current_step == self.fault_step:
            if self.fault_type == "parameter":
                # Matches Section 7 Demo Goal: Expected font_size=32, Actual font_size=12
                if isinstance(base_act, AddTextAction) or (hasattr(base_act, "type") and base_act.type == "add_text"):
                    return AddTextAction(
                        text="Demo",
                        position=(300, 200),
                        font_size=12,  # Deliberate wrong parameter
                    )
                return {"type": "resize_image", "width": 128, "height": 128}

            if self.fault_type == "tool":
                return SelectToolAction(tool_name="brush")  # Expected text tool

            if self.fault_type == "canvas_target":
                return AddTextAction(text="Demo", position=(25, 50), font_size=32)

            if self.fault_type == "format":
                return {"type": "resize_image", "invalid_key": True}

            if self.fault_type == "premature_stop":
                return StopAction(message="Stopping prematurely before task is finished")

        return base_act
