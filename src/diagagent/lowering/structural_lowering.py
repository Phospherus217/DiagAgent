"""Bounded Day-1 A1 lowering; dialog geometry is read when each A1 executes."""
from diagagent.actions.parser import ActionParser
from diagagent.environment.errors import DesktopError

ATOMIC_TYPES = {"click", "drag", "type", "hotkey", "wait"}


class StructuralLowerer:
    def __init__(self, backend):
        self.backend = backend

    def lower(self, action):
        if isinstance(action, dict):
            action = ActionParser.parse(action)
        if action.type in ATOMIC_TYPES:
            return [action]
        if action.type == "click_toolbar_icon":
            # Use GIMP's documented keyboard shortcuts instead of an absolute
            # toolbar pixel.  This keeps tool selection inside the existing
            # action/lowering/executor path and is valid for the calibrated
            # profile without depending on one screenshot's icon geometry.
            shortcuts = {
                "Text Tool": ["t"],
                "Crop Tool": ["shift", "c"],
                "Paintbrush Tool": ["p"],
                "Rectangle Select Tool": ["r"],
            }
            keys = shortcuts.get(action.tool_name)
            if keys is None:
                raise DesktopError(f"Unsupported GIMP tool: {action.tool_name}", "UI Grounding Error")
            commands = [{"type": "hotkey", "keys": keys}, {"type": "wait", "duration": 0.4}]
        elif action.type == "open_menu":
            self.backend.activate(self.backend.window)
            shortcuts = {"Image->Scale Image...": ["ctrl", "alt", "s"], "File->Export As...": ["ctrl", "shift", "e"]}
            if action.path not in shortcuts:
                raise DesktopError(f"Unsupported menu: {action.path}", "UI Grounding Error")
            commands = [{"type": "hotkey", "keys": shortcuts[action.path]}]
        elif action.type == "set_dialog_field":
            dialog = self.backend.find_dialog(action.dialog)
            points = {("Scale Image", "Width"): (140, 120), ("Scale Image", "Height"): (140, 155),
                      ("Export Image", "Name"): (420, 58)}
            if (action.dialog, action.field) not in points:
                raise DesktopError(f"Unsupported field: {action.dialog}/{action.field}", "UI Grounding Error")
            dx, dy = points[action.dialog, action.field]
            commands = []
            if action.dialog == "Scale Image" and action.field == "Width":
                # Fresh owned profile starts linked; Day 1 smoke scales once per session.
                commands.append({"type": "click", "x": dialog.left + 265, "y": dialog.top + 136})
            commands.extend([
                {"type": "click", "x": dialog.left + dx, "y": dialog.top + dy},
                {"type": "type", "text": str(action.value), "clear_first": True},
                {"type": "hotkey", "keys": "tab"},
            ])
        elif action.type == "click_dialog_button":
            dialog = self.backend.find_dialog(action.dialog)
            if (action.dialog, action.button) == ("Scale Image", "Scale"):
                commands = [{"type": "click", "x": dialog.left + round(dialog.width * 0.64), "y": dialog.bottom - 22}]
            elif (action.dialog, action.button) in {("Export Image", "Export"), ("Export Image as PNG", "Export")}:
                commands = [{"type": "hotkey", "keys": "enter"}]
            else:
                raise DesktopError(f"Unsupported button: {action.dialog}/{action.button}", "UI Grounding Error")
        else:
            raise DesktopError(f"Unresolved action: {action.type}", "UI Grounding Error")
        for command in commands:
            if command["type"] in {"click", "drag"}:
                command["coordinate_space"] = "virtual_desktop"
        return [ActionParser.parse(command) for command in commands]
