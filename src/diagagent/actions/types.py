"""Action type definitions and level classifications."""

from enum import Enum
from typing import Set


class ActionLevel(str, Enum):
    """Abstraction level of GUI actions."""
    A0 = "A0"          # Atomic OS-level input events (mouse, keyboard, sleep)
    A1 = "A1"          # GUI structural operations (menus, buttons, input fields)
    A2 = "A2"          # High-level domain tool tasks (resize, crop, add text, filter)
    CONTROL = "control"# Agent lifecycle and execution control (stop)
    UNKNOWN = "unknown"


class ActionType(str, Enum):
    # A0 Atomic
    CLICK = "click"
    DRAG = "drag"
    TYPE = "type"
    HOTKEY = "hotkey"
    WAIT = "wait"

    # A1 Structural
    OPEN_MENU = "open_menu"
    CLICK_TOOLBAR_ICON = "click_toolbar_icon"
    CLICK_DIALOG_BUTTON = "click_dialog_button"
    SET_DIALOG_FIELD = "set_dialog_field"

    # A2 Domain Tool
    RESIZE_IMAGE = "resize_image"
    CROP_CANVAS = "crop_canvas"
    SELECT_TOOL = "select_tool"
    SET_COLOR = "set_color"
    ADD_TEXT = "add_text"
    DRAW_LINE = "draw_line"
    FILL_RECTANGLE = "fill_rectangle"
    APPLY_FILTER = "apply_filter"
    EXPORT_FILE = "export_file"

    # Control
    STOP = "stop"


A0_ACTIONS: Set[str] = {
    ActionType.CLICK.value,
    ActionType.DRAG.value,
    ActionType.TYPE.value,
    ActionType.HOTKEY.value,
    ActionType.WAIT.value,
}

A1_ACTIONS: Set[str] = {
    ActionType.OPEN_MENU.value,
    ActionType.CLICK_TOOLBAR_ICON.value,
    ActionType.CLICK_DIALOG_BUTTON.value,
    ActionType.SET_DIALOG_FIELD.value,
}

A2_ACTIONS: Set[str] = {
    ActionType.RESIZE_IMAGE.value,
    ActionType.CROP_CANVAS.value,
    ActionType.SELECT_TOOL.value,
    ActionType.SET_COLOR.value,
    ActionType.ADD_TEXT.value,
    ActionType.DRAW_LINE.value,
    ActionType.FILL_RECTANGLE.value,
    ActionType.APPLY_FILTER.value,
    ActionType.EXPORT_FILE.value,
}


def get_action_level(action_type: str) -> ActionLevel:
    """Returns the abstraction level for a given action type string."""
    val = str(action_type).lower().strip()
    if val in A0_ACTIONS:
        return ActionLevel.A0
    if val in A1_ACTIONS:
        return ActionLevel.A1
    if val in A2_ACTIONS:
        return ActionLevel.A2
    if val == ActionType.STOP.value:
        return ActionLevel.CONTROL
    return ActionLevel.UNKNOWN
