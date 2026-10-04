"""Pydantic schemas for structured GUI actions."""

from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from diagagent.actions.types import ActionLevel, ActionType, get_action_level


class BaseAction(BaseModel):
    """Base class for all structured actions in DiagAgent."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: str

    @property
    def level(self) -> ActionLevel:
        return get_action_level(self.type)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


# ---------------------------------------------------------
# A0: Atomic Actions
# ---------------------------------------------------------

class ClickAction(BaseAction):
    type: str = Field(default=ActionType.CLICK.value)
    x: FiniteFloat
    y: FiniteFloat
    coordinate_space: Literal["virtual_desktop", "screenshot"] = "virtual_desktop"
    button: str = Field(default="left")
    clicks: int = Field(default=1)


class DragAction(BaseAction):
    type: str = Field(default=ActionType.DRAG.value)
    start: Tuple[FiniteFloat, FiniteFloat]
    end: Tuple[FiniteFloat, FiniteFloat]
    coordinate_space: Literal["virtual_desktop", "screenshot"] = "virtual_desktop"
    duration: float = Field(default=0.5)


class TypeAction(BaseAction):
    type: str = Field(default=ActionType.TYPE.value)
    text: str
    clear_first: bool = Field(default=False)


class HotkeyAction(BaseAction):
    type: str = Field(default=ActionType.HOTKEY.value)
    keys: Union[str, List[str]]


class WaitAction(BaseAction):
    type: str = Field(default=ActionType.WAIT.value)
    duration: float = Field(default=0.5)


# ---------------------------------------------------------
# A1: GUI Structural Actions
# ---------------------------------------------------------

class OpenMenuAction(BaseAction):
    type: str = Field(default=ActionType.OPEN_MENU.value)
    path: str  # e.g. "Image->Scale Image..." or "File->Export As..."


class ClickToolbarIconAction(BaseAction):
    type: str = Field(default=ActionType.CLICK_TOOLBAR_ICON.value)
    tool_name: str  # e.g. "Text Tool", "Crop Tool", "Paintbrush"


class ClickDialogButtonAction(BaseAction):
    type: str = Field(default=ActionType.CLICK_DIALOG_BUTTON.value)
    dialog: str
    button: str


class SetDialogFieldAction(BaseAction):
    type: str = Field(default=ActionType.SET_DIALOG_FIELD.value)
    dialog: str
    field: str
    value: Any


# ---------------------------------------------------------
# A2: Domain Tool Actions
# ---------------------------------------------------------

class ResizeImageAction(BaseAction):
    type: str = Field(default=ActionType.RESIZE_IMAGE.value)
    width: int
    height: int
    unit: str = Field(default="px")


class CropCanvasAction(BaseAction):
    type: str = Field(default=ActionType.CROP_CANVAS.value)
    region: Union[Tuple[float, float, float, float], List[float], str]
    coordinate: str = Field(default="image")


class SelectToolAction(BaseAction):
    type: str = Field(default=ActionType.SELECT_TOOL.value)
    tool_name: str


class SetColorAction(BaseAction):
    type: str = Field(default=ActionType.SET_COLOR.value)
    target: str = Field(default="foreground")  # "foreground" or "background"
    color: str = Field(default="#000000")


class AddTextAction(BaseAction):
    type: str = Field(default=ActionType.ADD_TEXT.value)
    text: str
    position: Tuple[float, float]
    font_size: int = Field(default=32)
    color: Optional[str] = None
    grounded_position: Optional[Tuple[float, float]] = None


class DrawLineAction(BaseAction):
    type: str = Field(default=ActionType.DRAW_LINE.value)
    start: Tuple[float, float]
    end: Tuple[float, float]
    brush_size: int = Field(default=10)
    color: str = Field(default="#ff0000")


class FillRectangleAction(BaseAction):
    type: str = Field(default=ActionType.FILL_RECTANGLE.value)
    region: Union[Tuple[float, float, float, float], List[float]]
    color: str = Field(default="#ff0000")


class ApplyFilterAction(BaseAction):
    type: str = Field(default=ActionType.APPLY_FILTER.value)
    filter_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ExportFileAction(BaseAction):
    type: str = Field(default=ActionType.EXPORT_FILE.value)
    path: str
    format: str = Field(default="png")
    overwrite: bool = Field(default=True)


# ---------------------------------------------------------
# Control Actions
# ---------------------------------------------------------

class StopAction(BaseAction):
    type: str = Field(default=ActionType.STOP.value)
    message: Optional[str] = None


# Action mapping registry
ACTION_MODEL_MAP: Dict[str, type] = {
    ActionType.CLICK.value: ClickAction,
    ActionType.DRAG.value: DragAction,
    ActionType.TYPE.value: TypeAction,
    ActionType.HOTKEY.value: HotkeyAction,
    ActionType.WAIT.value: WaitAction,
    ActionType.OPEN_MENU.value: OpenMenuAction,
    ActionType.CLICK_TOOLBAR_ICON.value: ClickToolbarIconAction,
    ActionType.CLICK_DIALOG_BUTTON.value: ClickDialogButtonAction,
    ActionType.SET_DIALOG_FIELD.value: SetDialogFieldAction,
    ActionType.RESIZE_IMAGE.value: ResizeImageAction,
    ActionType.CROP_CANVAS.value: CropCanvasAction,
    ActionType.SELECT_TOOL.value: SelectToolAction,
    ActionType.SET_COLOR.value: SetColorAction,
    ActionType.ADD_TEXT.value: AddTextAction,
    ActionType.DRAW_LINE.value: DrawLineAction,
    ActionType.FILL_RECTANGLE.value: FillRectangleAction,
    ActionType.APPLY_FILTER.value: ApplyFilterAction,
    ActionType.EXPORT_FILE.value: ExportFileAction,
    ActionType.STOP.value: StopAction,
}
