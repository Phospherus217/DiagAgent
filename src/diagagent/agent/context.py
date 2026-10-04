"""ContextBuilder for formatting multimodal LLM/VLM interaction prompts while strictly guarding against evaluator leakage."""

import json
from typing import Any, Dict, List, Optional
from diagagent.models.base import Message
from diagagent.observation.models import AgentObservation, Observation


SYSTEM_PROMPT = """You are DiagAgent, an intelligent GUI automation agent operating GIMP.
Your goal is to complete the given image editing task step by step by emitting structured JSON actions.

Available Semantic Actions (A2):
- {"type": "resize_image", "width": <int>, "height": <int>, "unit": "px"}
- {"type": "crop_canvas", "width": <int>, "height": <int>, "x": <int>, "y": <int>}
- {"type": "add_text", "text": "<str>", "x": <int>, "y": <int>, "size": <int>, "color": "<str>"}
- {"type": "export_file", "path": "<str>", "format": "png", "overwrite": true}
- {"type": "stop"}

Available Structural / Dialog Actions (A1):
- {"type": "open_menu", "menu_path": ["File", "Export As..."]}
- {"type": "click_dialog_button", "dialog": "Export Image", "button": "Export"}
- {"type": "set_dialog_field", "dialog": "Scale Image", "field": "Width", "value": "<val>"}

Available Primitive Actions (A0):
- {"type": "click", "x": <int>, "y": <int>}
- {"type": "type_text", "text": "<str>"}
- {"type": "hotkey", "keys": ["ctrl", "s"]}
- {"type": "wait", "duration_seconds": 1.0}

Format Rules:
1. Output ONLY a valid JSON object representing a single action. Do not include markdown code fences or conversational text.
2. When the task is completely finished, emit {"type": "stop"}.
"""


class ContextBuilder:
    """Builds clean, non-leaking message context for multimodal agent models."""

    def __init__(self, system_prompt: str = SYSTEM_PROMPT, max_history_turns: int = 5):
        self.system_prompt = system_prompt
        self.max_history_turns = max_history_turns

    def build_context(
        self,
        task_spec: Dict[str, Any],
        observation: Optional[Any],
        history: Optional[List[Dict[str, Any]]] = None,
        feedback: Optional[Dict[str, Any]] = None,
    ) -> List[Message]:
        """Constructs dialogue messages, ensuring no evaluator-only gold fields are leaked."""
        messages: List[Message] = []

        # 1. System instruction
        messages.append(Message(role="system", content=self.system_prompt))

        # 2. Public task specification (strictly filter out internal gold/evaluator configs)
        clean_task = {
            "task_id": task_spec.get("task_id", "unspecified"),
            "instruction": task_spec.get("instruction", ""),
            "allowed_actions": task_spec.get("allowed_actions", []),
        }
        if "initial_state" in task_spec:
            # Include input file and output file if public
            init_state = task_spec["initial_state"]
            clean_task["initial_state"] = {
                k: v for k, v in init_state.items()
                if k in ("input_file", "output_file", "window_size")
            }

        user_content_lines = [
            "Current Task:",
            json.dumps(clean_task, indent=2, ensure_ascii=False),
        ]

        # 3. Recent history
        if history:
            recent_steps = history[-self.max_history_turns:]
            user_content_lines.append("\nRecent Action History:")
            for h in recent_steps:
                step_num = h.get("step", 0)
                action_summary = h.get("action", {})
                verif_summary = h.get("verification", {})
                user_content_lines.append(
                    f"- Step {step_num}: Action={json.dumps(action_summary)} | Result={'OK' if verif_summary.get('passed', True) else 'FAILED'}"
                )

        # 4. Diagnostic/recovery feedback if any
        if feedback:
            # Strip any evaluator-only keys
            clean_feedback = {
                k: v for k, v in feedback.items()
                if not k.startswith("evaluator_") and not k.startswith("gold_")
            }
            if clean_feedback:
                user_content_lines.append(f"\nExecution Feedback: {json.dumps(clean_feedback)}")

        # 5. Current observation summary (public attributes only)
        obs_images: List[str] = []
        if observation is not None:
            user_content_lines.append("\nCurrent GUI Observation:")
            obs_info: Dict[str, Any] = {}
            for field in ("coordinate_space", "screenshot_coordinate_space", "screenshot_bbox", "pixel_scale"):
                value = getattr(observation, field, None)
                if value is not None:
                    obs_info[field] = value
            obs_info["coordinate_rule"] = (
                "Pointer actions default to virtual_desktop pixels. For screenshot pixels, set "
                "coordinate_space='screenshot' on click/drag. The runtime adds screenshot_bbox's "
                "left/top once (scale 1). HWND/UI bboxes are virtual_desktop; negative desktop "
                "coordinates are valid. Never add the origin to already desktop coordinates.")
            if hasattr(observation, "active_dialog") and observation.active_dialog:
                obs_info["active_dialog"] = observation.active_dialog
            if hasattr(observation, "active_tool") and observation.active_tool:
                obs_info["active_tool"] = observation.active_tool
            if hasattr(observation, "window_focused"):
                obs_info["window_focused"] = observation.window_focused
            if hasattr(observation, "ui_primitives") and observation.ui_primitives:
                # Include non-confidential window/canvas bboxes if available
                obs_info["ui_layout"] = {
                    k: v for k, v in (observation.ui_primitives.model_dump() if hasattr(observation.ui_primitives, "model_dump")
                                     else observation.ui_primitives).items()
                    if k in ("window_bbox", "canvas_bbox", "menu_bar_bbox")
                }
            user_content_lines.append(json.dumps(obs_info, indent=2, ensure_ascii=False))

            # Attach screenshot path if present
            if hasattr(observation, "screenshot_path") and observation.screenshot_path:
                obs_images.append(str(observation.screenshot_path))

        user_content_lines.append("\nPlease output the next JSON action:")
        user_message = Message(
            role="user",
            content="\n".join(user_content_lines),
            images=obs_images,
        )
        messages.append(user_message)
        return messages
