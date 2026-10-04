"""Observation collector that queries environment and window state to build Observation models."""

from pathlib import Path
import time
from typing import Any, Optional

from diagagent.observation.models import Observation, UIPrimitives, UIState


class ObservationCollector:
    """Collects GUI screenshots, UI primitives, and internal state probes."""

    def __init__(self, run_dir: Optional[Path] = None):
        self.run_dir = run_dir

    def collect(
        self,
        step: int,
        backend: Any,
        window_manager: Any = None,
        phase: str = "step",
        screenshot_name: Optional[str] = None,
    ) -> Observation:
        """Captures screenshot and assembles observation."""
        screenshot_path_str = None
        if screenshot_name and self.run_dir:
            ss_dir = self.run_dir / "screenshots"
            ss_dir.mkdir(parents=True, exist_ok=True)
            target_path = ss_dir / screenshot_name
            if hasattr(backend, "capture_screenshot"):
                backend.capture_screenshot(target_path)
                screenshot_path_str = str(target_path)

        ui_prims = UIPrimitives()
        if window_manager and hasattr(window_manager, "ui_primitives"):
            prims_data = window_manager.ui_primitives()
            ui_prims = UIPrimitives(**prims_data)
        elif hasattr(backend, "window_bbox"):
            ui_prims.window_bbox = tuple(backend.window_bbox)

        ui_state = UIState()
        if hasattr(backend, "image") and backend.image is not None:
            ui_state.image_loaded = True
            ui_state.image_size = (backend.image.width, backend.image.height)
        if getattr(backend, "name", "") == "real_gimp":
            ui_state.image_loaded = backend.image_loaded
            size = backend.current_image_size()
            ui_state.image_size = tuple(size) if size else None
        if hasattr(backend, "active_tool"):
            ui_state.active_tool = backend.active_tool
        if hasattr(backend, "active_dialog"):
            ui_state.active_dialog = backend.active_dialog
        if hasattr(backend, "dialog_confirmed"):
            ui_state.dialog_confirmed = backend.dialog_confirmed
        if hasattr(backend, "foreground_color"):
            ui_state.foreground_color = backend.foreground_color
        if hasattr(backend, "layers"):
            ui_state.layers = list(backend.layers)

        return Observation(
            step=step,
            phase=phase,
            screenshot_path=screenshot_path_str,
            ui_primitives=ui_prims,
            ui_state=ui_state,
            timestamp=time.time(),
            coordinate_space="virtual_desktop",
            screenshot_coordinate_space="screenshot",
            screenshot_metadata=getattr(backend, "screenshot_metadata", None),
            state_source="window_title" if getattr(backend, "name", "") == "real_gimp" else "mock_internal",
            bbox_source="manual_profile",
            screenshot_bbox=getattr(backend, "screenshot_bbox", backend.window_bbox),
        )
