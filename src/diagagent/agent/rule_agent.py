"""Rule-based expert agent that deterministically solves benchmark tasks."""

from typing import Any, Dict, List, Optional, Union

from diagagent.actions.schema import (
    AddTextAction,
    ApplyFilterAction,
    BaseAction,
    CropCanvasAction,
    ExportFileAction,
    ResizeImageAction,
    SelectToolAction,
    StopAction,
    WaitAction,
)
from diagagent.agent.base import BaseAgent
from diagagent.observation.models import Observation


class RuleAgent(BaseAgent):
    """Deterministic oracle baseline agent executing exact subgoals from task spec."""

    def __init__(self, name: str = "RuleAgent"):
        super().__init__(name=name)
        self.subgoal_queue: List[Dict[str, Any]] = []
        self.step_pointer: int = 0

    def reset(self, task_spec: Dict[str, Any]) -> None:
        self.task_spec = task_spec
        self.step_pointer = 0
        raw_subgoals = sorted(task_spec.get("subgoals", []), key=lambda s: s.get("order", 0))

        # Filter out "open_image" if it's already fulfilled on environment reset
        self.subgoal_queue = [s for s in raw_subgoals if s.get("id") != "open_image"]

    def act(self, observation: Observation) -> Union[BaseAction, Dict[str, Any]]:
        if self.step_pointer >= len(self.subgoal_queue):
            return StopAction()

        subgoal = self.subgoal_queue[self.step_pointer]
        self.step_pointer += 1

        sg_id = subgoal.get("id", "")
        sg_type = subgoal.get("type", "")
        exp_action = subgoal.get("expected_action", {})
        action_types = exp_action.get("action_type", [])

        # 1. Resize image
        if "resize" in sg_id or "resize_image" in action_types or (sg_type == "parameter" and "width" in subgoal.get("expected_state", {})):
            st = subgoal.get("expected_state", {})
            w = st.get("width", 512)
            h = st.get("height", 512)
            return ResizeImageAction(width=int(w), height=int(h))

        # 2. Crop canvas
        if "crop" in sg_id or "crop_canvas" in action_types:
            return CropCanvasAction(region="center")

        # 3. Select tool
        if sg_type == "tool_selection" or "select" in sg_id:
            exp_tool = (subgoal.get("expected_state") or {}).get("active_tool", "text")
            return SelectToolAction(tool_name=exp_tool)

        # 4. Add text
        if "add_text" in sg_id or "text" in sg_id or "watermark" in sg_id:
            txt_info = subgoal.get("expected_artifact_change", {})
            text = txt_info.get("text", "Demo")
            font_size = txt_info.get("font_size", 32)
            color = txt_info.get("color")
            cbox = observation.ui_primitives.canvas_bbox
            cx = (cbox[0] + cbox[2]) // 2
            cy = (cbox[1] + cbox[3]) // 2
            return AddTextAction(text=text, position=(cx, cy), font_size=font_size, color=color)

        # 5. Apply filter (e.g. Gaussian Blur)
        if "blur" in sg_id or "filter" in sg_id:
            change_info = subgoal.get("expected_artifact_change", {})
            radius = change_info.get("radius", 5)
            return ApplyFilterAction(filter_name="Gaussian Blur", parameters={"radius": radius})

        # 6. Export file
        if "export" in sg_id or sg_type == "artifact":
            output_file = (self.task_spec.get("initial_state") or {}).get("output_file", "output.png")
            return ExportFileAction(path=output_file, format="png")

        # Default fallback
        return WaitAction(duration=0.5)
