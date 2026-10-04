"""GIMP-specific semantic action lowering rules."""

from typing import Any, Dict, List, Optional, Tuple

from diagagent.actions.schema import (
    AddTextAction,
    ApplyFilterAction,
    BaseAction,
    ClickAction,
    ClickDialogButtonAction,
    ClickToolbarIconAction,
    CropCanvasAction,
    DragAction,
    ExportFileAction,
    HotkeyAction,
    OpenMenuAction,
    ResizeImageAction,
    SelectToolAction,
    SetColorAction,
    SetDialogFieldAction,
    TypeAction,
    WaitAction,
)
from diagagent.lowering.base import BaseLowerer

GIMP_TOOL_ICON_NAMES = {
    "text": "Text Tool",
    "crop": "Crop Tool",
    "brush": "Paintbrush Tool",
    "paintbrush": "Paintbrush Tool",
    "rectangle_select": "Rectangle Select Tool",
}


class GIMPLowerer(BaseLowerer):
    """Lowering rules specific to the GNU Image Manipulation Program (GIMP)."""

    def __init__(self, default_canvas_bbox: Tuple[int, int, int, int] = (180, 80, 980, 620)):
        self.default_canvas_bbox = default_canvas_bbox

    def lower(self, action: BaseAction, ui_primitives: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        ui_primitives = ui_primitives or {}
        action_type = action.type

        # Already low-level atomic or structural actions: no-op lowering
        if action_type in {
            "click", "drag", "type", "hotkey", "wait",
            "open_menu", "click_toolbar_icon", "click_dialog_button", "set_dialog_field", "stop"
        }:
            return [action]

        if isinstance(action, SelectToolAction) or action_type == "select_tool":
            tool_name = getattr(action, "tool_name", None) or (action.to_dict() if hasattr(action, "to_dict") else {}).get("tool_name", "")
            icon_name = GIMP_TOOL_ICON_NAMES.get(str(tool_name).lower(), tool_name)
            return [
                ClickToolbarIconAction(tool_name=icon_name),
                WaitAction(duration=0.5),
            ]

        if isinstance(action, SetColorAction) or action_type == "set_color":
            color = str(getattr(action, "color", "#000000")).lstrip("#")
            return [
                ClickAction(x=45, y=690),
                WaitAction(duration=0.5),
                SetDialogFieldAction(dialog="Change Foreground Color", field="HTML notation", value=color),
                HotkeyAction(keys="enter"),
                WaitAction(duration=0.2),
                ClickDialogButtonAction(dialog="Change Foreground Color", button="OK"),
                WaitAction(duration=0.5),
            ]

        if isinstance(action, ResizeImageAction) or action_type == "resize_image":
            width = getattr(action, "width", 512)
            height = getattr(action, "height", 512)
            return [
                OpenMenuAction(path="Image->Scale Image..."),
                WaitAction(duration=0.5),
                SetDialogFieldAction(dialog="Scale Image", field="Width", value=width),
                SetDialogFieldAction(dialog="Scale Image", field="Height", value=height),
                ClickDialogButtonAction(dialog="Scale Image", button="Scale"),
                WaitAction(duration=0.5),
            ]

        if isinstance(action, CropCanvasAction) or action_type == "crop_canvas":
            canvas_box = ui_primitives.get("canvas_bbox", self.default_canvas_bbox)
            cx1, cy1, cx2, cy2 = canvas_box
            cw = cx2 - cx1
            ch = cy2 - cy1

            region = getattr(action, "region", "center")
            coordinate = getattr(action, "coordinate", "image")
            image_size = ui_primitives.get("image_size")

            def image_to_screen(x, y):
                if not image_size or len(image_size) != 2:
                    raise ValueError("image coordinates require observed image_size")
                iw, ih = float(image_size[0]), float(image_size[1])
                if iw <= 0 or ih <= 0:
                    raise ValueError("observed image_size must be positive")
                # Real-GIMP profiles may provide a screenshot-calibrated
                # image bounding box.  It is stronger than the outer canvas
                # dock bbox, which also contains rulers and scrollbars.
                image_box = ui_primitives.get("image_bbox")
                if image_box and len(image_box) == 4:
                    ix1, iy1, ix2, iy2 = map(float, image_box)
                    return ix1 + float(x) * ((ix2 - ix1) / iw), iy1 + float(y) * ((iy2 - iy1) / ih)
                zoom = ui_primitives.get("zoom")
                scale = float(zoom) if zoom is not None else min(cw / iw, ch / ih)
                display_w, display_h = iw * scale, ih * scale
                ox, oy = cx1 + (cw - display_w) / 2.0, cy1 + (ch - display_h) / 2.0
                return ox + float(x) * scale, oy + float(y) * scale

            if region == "center":
                image_region = (0.25, 0.25, 0.75, 0.75)
                if image_size:
                    iw, ih = image_size
                    points = [(iw * image_region[0], ih * image_region[1]),
                              (iw * image_region[2], ih * image_region[3])]
                    start, end = image_to_screen(*points[0]), image_to_screen(*points[1])
                else:
                    start = (cx1 + cw * 0.25, cy1 + ch * 0.25)
                    end = (cx1 + cw * 0.75, cy1 + ch * 0.75)
            elif isinstance(region, (list, tuple)) and len(region) == 4:
                if coordinate == "screen":
                    start, end = (region[0], region[1]), (region[2], region[3])
                elif not image_size:
                    # Backward-compatible fallback for callers that supplied
                    # the historical absolute canvas coordinates without an
                    # observation.  Real-GIMP execution always supplies
                    # image_size and uses the image-to-canvas transform.
                    start, end = (region[0], region[1]), (region[2], region[3])
                else:
                    start = image_to_screen(region[0], region[1])
                    end = image_to_screen(region[2], region[3])
            else:
                if coordinate == "screen":
                    start = (cx1 + cw * 0.2, cy1 + ch * 0.2)
                    end = (cx1 + cw * 0.8, cy1 + ch * 0.8)
                else:
                    raise ValueError(f"Unsupported crop region: {region}")

            return [
                ClickToolbarIconAction(tool_name="Crop Tool"),
                WaitAction(duration=0.3),
                DragAction(start=start, end=end, duration=0.4),
                WaitAction(duration=0.2),
                HotkeyAction(keys="enter"),
                WaitAction(duration=0.5),
            ]

        if isinstance(action, AddTextAction) or action_type == "add_text":
            lowered: List[BaseAction] = []
            color = getattr(action, "color", None)
            if color:
                lowered.extend(self.lower(SetColorAction(target="foreground", color=color), ui_primitives))

            # Prefer grounded_position if available, else canonical position
            pos = getattr(action, "grounded_position", None) or getattr(action, "position", (200, 200))
            text = getattr(action, "text", "")

            lowered.extend([
                ClickToolbarIconAction(tool_name="Text Tool"),
                WaitAction(duration=0.3),
                ClickAction(x=pos[0], y=pos[1]),
                WaitAction(duration=0.2),
                TypeAction(text=text),
                WaitAction(duration=0.5),
            ])
            return lowered

        if isinstance(action, ApplyFilterAction) or action_type == "apply_filter":
            filter_name = getattr(action, "filter_name", "")
            params = getattr(action, "parameters", {}) or {}
            lowered = [
                OpenMenuAction(path=f"Filters->Blur->{filter_name}..."),
                WaitAction(duration=0.5),
            ]
            for k, v in params.items():
                lowered.append(SetDialogFieldAction(dialog=filter_name, field=k, value=v))
            lowered.extend([
                ClickDialogButtonAction(dialog=filter_name, button="OK"),
                WaitAction(duration=0.5),
            ])
            return lowered

        if isinstance(action, ExportFileAction) or action_type == "export_file":
            path = getattr(action, "path", "output.png")
            return [
                OpenMenuAction(path="File->Export As..."),
                WaitAction(duration=0.5),
                SetDialogFieldAction(dialog="Export Image", field="Name", value=path),
                ClickDialogButtonAction(dialog="Export Image", button="Export"),
                WaitAction(duration=0.5),
                ClickDialogButtonAction(dialog="Export Image as PNG", button="Export"),
                WaitAction(duration=0.5),
            ]

        # Fallback: single action
        return [action]
