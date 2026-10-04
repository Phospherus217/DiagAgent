"""Window manager tracking coordinates and bounding boxes of GIMP UI regions."""

from typing import Any, Dict, List, Tuple


class GIMPWindowManager:
    """Manages GIMP window sizing and computes deterministic bounding boxes."""

    def __init__(self, backend: Any, window_size: Tuple[int, int] = (1280, 720)):
        self.backend = backend
        self.window_size = list(window_size)
        self.window_bbox = (0, 0, int(window_size[0]), int(window_size[1]))

    def fix_window(self) -> None:
        """Fixes or notifies the backend of the expected window geometry."""
        if hasattr(self.backend, "fix_window"):
            self.backend.fix_window(self.window_size)

    def ui_primitives(self) -> Dict[str, Tuple[int, int, int, int]]:
        """Calculates standard UI element bounding boxes for the window size."""
        w, h = self.window_size[0], self.window_size[1]
        primitives = {
            "window_bbox": (0, 0, w, h),
            "menu_bar_bbox": (0, 0, w, 30),
            "toolbox_bbox": (0, 30, 90, max(30, h - 70)),
            "canvas_bbox": (180, 80, max(180, w - 300), max(80, h - 100)),
            "layer_panel_bbox": (max(0, w - 280), 80, max(0, w - 10), max(80, h - 100)),
        }
        if getattr(self.backend, "name", "") == "real_gimp":
            left, top, right, bottom = self.backend.window_bbox
            # Manual GIMP 3.2 single-window profile at 96 DPI, verified with screenshots.
            primitives = {
                "window_bbox": (0, 0, w, h),
                "menu_bar_bbox": (8, 30, w - 8, 56),
                "toolbox_bbox": (8, 56, 200, 320),
                "canvas_bbox": (220, 115, w - 284, h - 58),
                "layer_panel_bbox": (w - 256, 554, w - 8, h - 35),
            }
            primitives = {key: (box[0] + left, box[1] + top, box[2] + left, box[3] + top)
                          for key, box in primitives.items()}
            primitives["window_bbox"] = (left, top, right, bottom)
        return primitives
