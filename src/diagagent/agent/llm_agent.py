"""Extensible LLM/VLM Agent interface for prompt-based GUI planning."""

import json
from typing import Any, Callable, Dict, Optional, Union

from diagagent.actions.parser import ActionParser
from diagagent.actions.schema import BaseAction, StopAction
from diagagent.agent.base import BaseAgent
from diagagent.observation.models import Observation


class LLMAgent(BaseAgent):
    """Agent that formats GUI observations into structured prompts and parses model completions."""

    def __init__(
        self,
        model_fn: Optional[Callable[[str], str]] = None,
        name: str = "LLMAgent",
    ):
        super().__init__(name=name)
        self.model_fn = model_fn
        self.history = []

    def reset(self, task_spec: Dict[str, Any]) -> None:
        self.task_spec = task_spec
        self.history = []

    def _build_prompt(self, observation: Observation) -> str:
        task_instruction = self.task_spec.get("instruction", "")
        allowed = self.task_spec.get("allowed_actions", [])

        prompt = f"""You are a professional GUI agent operating GIMP.
Task: {task_instruction}
Allowed Actions: {json.dumps(allowed)}

Current GUI Observation:
- Active Tool: {observation.ui_state.active_tool}
- Active Dialog: {observation.ui_state.active_dialog}
- Image Loaded: {observation.ui_state.image_loaded}
- Canvas Bounding Box: {observation.ui_primitives.canvas_bbox}

Output your next action in valid JSON format:
```json
{{"type": "<action_type>", ...}}
```
"""
        return prompt

    def act(self, observation: Observation) -> Union[BaseAction, Dict[str, Any]]:
        prompt = self._build_prompt(observation)
        if not self.model_fn:
            # If no model provided, fallback to stopping
            return StopAction(message="No model function provided to LLMAgent")

        response = self.model_fn(prompt)
        return ActionParser.parse(response)
