"""Data models for GUI observations and UI primitives."""

import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field


class UIPrimitives(BaseModel):
    """Core UI bounding boxes tracked across the GIMP window layout."""
    model_config = ConfigDict(extra="allow")

    window_bbox: Tuple[int, int, int, int] = (0, 0, 1280, 720)
    menu_bar_bbox: Tuple[int, int, int, int] = (0, 0, 1280, 30)
    toolbox_bbox: Tuple[int, int, int, int] = (0, 30, 90, 650)
    canvas_bbox: Tuple[int, int, int, int] = (180, 80, 980, 620)
    layer_panel_bbox: Tuple[int, int, int, int] = (1000, 80, 1270, 620)


class UIState(BaseModel):
    """Semantic UI state flags and status."""
    model_config = ConfigDict(extra="allow")

    image_loaded: bool = False
    active_tool: Optional[str] = None
    active_dialog: Optional[str] = None
    dialog_confirmed: Optional[bool] = None
    image_size: Optional[Tuple[int, int]] = None
    foreground_color: Optional[str] = None
    layers: List[Dict[str, Any]] = Field(default_factory=list)


class Observation(BaseModel):
    """Complete observation payload at a specific execution step."""
    model_config = ConfigDict(extra="allow")

    step: int = 0
    phase: str = "step"  # "initial", "step", "final"
    screenshot_path: Optional[str] = None
    ui_primitives: UIPrimitives = Field(default_factory=UIPrimitives)
    ui_state: UIState = Field(default_factory=UIState)
    ocr_tokens: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class AgentObservation(BaseModel):
    """Explicit public whitelist; evaluator and backend state never enter this view."""
    model_config = ConfigDict(extra="forbid")
    step: int
    screenshot_path: str
    timestamp: float
    ui_primitives: Optional[UIPrimitives] = None
    coordinate_space: str = "virtual_desktop"
    screenshot_coordinate_space: str = "screenshot"
    pixel_scale: float = 1.0
    bbox_source: str = "manual_profile"
    screenshot_bbox: Optional[List[int]] = None
    feedback: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_private(cls, observation, allowed):
        return cls(step=observation.step, screenshot_path=observation.screenshot_path,
            timestamp=observation.timestamp,
            ui_primitives=observation.ui_primitives if "manual_ui_bbox" in allowed else None,
            screenshot_bbox=getattr(observation, "screenshot_bbox", None))
