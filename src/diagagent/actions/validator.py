"""Action validator checking semantic constraints, bounding boxes, and permissions."""

from typing import Any, List, Optional, Tuple, Union
import math

from diagagent.actions.schema import (
    AddTextAction,
    BaseAction,
    ClickAction,
    CropCanvasAction,
    DragAction,
    ResizeImageAction,
)


class ActionValidator:
    """Validates structured actions against task rules and GUI boundaries."""

    @staticmethod
    def _is_valid_point(point: Any) -> bool:
        return (
            isinstance(point, (list, tuple))
            and len(point) == 2
            and all(isinstance(v, (int, float)) for v in point)
        )

    @classmethod
    def validate(
        cls,
        action: Union[BaseAction, dict],
        allowed_actions: Optional[List[str]] = None,
        window_bbox: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Validates an action against constraints.

        Returns:
            (True, None) if valid, or (False, error_reason) if invalid.
        """
        if isinstance(action, dict):
            action_type = action.get("type")
        elif isinstance(action, BaseAction):
            action_type = action.type
        else:
            return False, f"Invalid action type object: {type(action)}"

        if not action_type:
            return False, "Missing action type"

        from diagagent.actions.schema import ACTION_MODEL_MAP
        if action_type not in ACTION_MODEL_MAP:
            return False, f"Unsupported action: {action_type}"
        values = action if isinstance(action, dict) else action.to_dict()
        for field in ("x", "y", "duration"):
            if field in values and (not isinstance(values[field], (int, float)) or not math.isfinite(values[field])):
                return False, f"{field} must be finite"
        if "duration" in values and not 0 <= values["duration"] <= 10:
            return False, "duration must be in [0, 10] seconds"

        # Check allowed_actions whitelist
        if allowed_actions is not None and action_type not in allowed_actions:
            return False, f"Action type '{action_type}' is not permitted in this task"

        # Bounding box and coordinate checks
        if window_bbox is not None:
            x1, y1, x2, y2 = window_bbox

            if isinstance(action, ClickAction) or (isinstance(action, dict) and action_type == "click"):
                x = action.x if isinstance(action, ClickAction) else action.get("x")
                y = action.y if isinstance(action, ClickAction) else action.get("y")
                if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
                    return False, "Click coordinates must be numeric"
                if not (x1 <= x < x2 and y1 <= y < y2):
                    return False, f"Click coordinates ({x}, {y}) outside window bbox {window_bbox}"

            elif isinstance(action, DragAction) or (isinstance(action, dict) and action_type == "drag"):
                start = action.start if isinstance(action, DragAction) else action.get("start")
                end = action.end if isinstance(action, DragAction) else action.get("end")
                for pt, name in [(start, "start"), (end, "end")]:
                    if not cls._is_valid_point(pt):
                        return False, f"Drag {name} coordinates must be a 2D point [x, y]"
                    px, py = pt
                    if not (x1 <= px < x2 and y1 <= py < y2):
                        return False, f"Drag {name} ({px}, {py}) outside window bbox {window_bbox}"

            elif isinstance(action, AddTextAction) or (isinstance(action, dict) and action_type == "add_text"):
                pos = action.position if isinstance(action, AddTextAction) else action.get("position")
                if not cls._is_valid_point(pos):
                    return False, "add_text position must be a 2D point [x, y]"
                px, py = pos
                if not (x1 <= px < x2 and y1 <= py < y2):
                    return False, f"add_text position ({px}, {py}) outside window bbox {window_bbox}"

        # Parameter sanity checks
        if isinstance(action, ResizeImageAction) or (isinstance(action, dict) and action_type == "resize_image"):
            width = action.width if isinstance(action, ResizeImageAction) else action.get("width")
            height = action.height if isinstance(action, ResizeImageAction) else action.get("height")
            if not (isinstance(width, int) and width > 0):
                return False, f"Resize width must be a positive integer, got {width}"
            if not (isinstance(height, int) and height > 0):
                return False, f"Resize height must be a positive integer, got {height}"

        elif isinstance(action, CropCanvasAction) or (isinstance(action, dict) and action_type == "crop_canvas"):
            region = action.region if isinstance(action, CropCanvasAction) else action.get("region")
            if isinstance(region, (list, tuple)):
                if len(region) != 4 or not all(isinstance(v, (int, float)) for v in region):
                    return False, "Crop region list must contain 4 numbers [x1, y1, x2, y2]"
                if region[0] >= region[2] or region[1] >= region[3]:
                    return False, f"Invalid crop coordinates: x1 < x2 and y1 < y2 required, got {region}"
            elif isinstance(region, str):
                if region not in {"center", "top_left", "bottom_right"}:
                    return False, f"Unknown named crop region: {region}"
            else:
                return False, "Crop region must be a 4-tuple or named region string"

        return True, None
