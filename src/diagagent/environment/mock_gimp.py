"""High-fidelity headless mock backend simulating GIMP operations with Pillow."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFilter


class MockGIMPBackend:
    """Headless simulation backend for GIMP."""
    name = "mock"

    def __init__(self, window_bbox: Optional[Tuple[int, int, int, int]] = None):
        self.window_bbox = list(window_bbox or [0, 0, 1280, 720])
        self.launched = False
        self.focused = False
        self.input_file = None
        self.image: Optional[Image.Image] = None
        self.active_tool: Optional[str] = None
        self.foreground_color = "#000000"
        self.active_dialog: Optional[str] = None
        self.dialog_confirmed: Optional[bool] = None
        self.layers: List[Dict[str, Any]] = [
            {"name": "Background", "visible": True, "active": True, "opacity": 100}
        ]
        self.last_action: Optional[Dict[str, Any]] = None

    def launch(self) -> None:
        self.launched = True
        self.focused = True

    def close(self) -> None:
        self.launched = False
        self.focused = False

    def fix_window(self, window_size: Union[List[int], Tuple[int, int]]) -> None:
        self.window_bbox = [0, 0, int(window_size[0]), int(window_size[1])]

    def open_image(self, input_file: Union[str, Path]) -> None:
        path = Path(input_file).resolve()
        if not path.exists():
            raise FileNotFoundError(path)

        self.input_file = str(path)
        self.image = Image.open(path).convert("RGB")

    def capture_screenshot(self, path: Union[str, Path]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        w = self.window_bbox[2] - self.window_bbox[0]
        h = self.window_bbox[3] - self.window_bbox[1]

        # Draw a synthetic GIMP interface screenshot
        img = Image.new("RGB", (w, h), (235, 235, 235))
        draw = ImageDraw.Draw(img)

        # 1. Menu bar
        draw.rectangle([0, 0, w - 1, 29], fill=(210, 210, 215))
        draw.text((10, 8), "GIMP - File Edit Select View Image Layer Colors Tools Filters Help", fill=(20, 20, 20))

        # 2. Toolbox on left
        draw.rectangle([0, 30, 90, h - 70], fill=(195, 200, 210), outline=(80, 80, 80))
        active_label = f"Tool: {self.active_tool or 'None'}"
        draw.text((8, 45), "Toolbox", fill=(0, 0, 0))
        draw.text((8, 70), active_label[:12], fill=(40, 40, 40))

        # 3. Canvas in center
        cx1, cy1, cx2, cy2 = 180, 80, max(180, w - 300), max(80, h - 100)
        draw.rectangle([cx1, cy1, cx2, cy2], fill=(255, 255, 255), outline=(50, 50, 50))

        if self.image is not None:
            thumb = self.image.copy()
            thumb.thumbnail((cx2 - cx1 - 20, cy2 - cy1 - 20))
            paste_x = cx1 + (cx2 - cx1 - thumb.width) // 2
            paste_y = cy1 + (cy2 - cy1 - thumb.height) // 2
            img.paste(thumb, (paste_x, paste_y))

        # 4. Layers panel on right
        lx1, ly1, lx2, ly2 = max(0, w - 280), 80, max(0, w - 10), max(80, h - 100)
        draw.rectangle([lx1, ly1, lx2, ly2], fill=(225, 225, 230), outline=(80, 80, 80))
        draw.text((lx1 + 10, ly1 + 15), "Layers Panel", fill=(0, 0, 0))

        img.save(target)

    def execute_atomic(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Executes low-level action and simulates state modifications."""
        self.last_action = action
        atype = action.get("type")

        if atype == "click_toolbar_icon":
            tool_name = action.get("tool_name", "")
            for simple_name in ["text", "crop", "brush", "select"]:
                if simple_name in tool_name.lower():
                    self.active_tool = simple_name
                    break
            return {"changed": True, "message": f"Selected tool {self.active_tool}"}

        if atype == "set_dialog_field":
            self.active_dialog = action.get("dialog")
            return {"changed": True, "message": "Dialog field updated"}

        if atype == "click_dialog_button":
            button = str(action.get("button", "")).lower()
            if button in {"ok", "scale", "export"}:
                self.dialog_confirmed = True
                self.active_dialog = None
            return {"changed": True, "message": f"Clicked {button}"}

        if atype == "wait":
            return {"changed": False, "message": "Waited"}

        return {"changed": True, "message": "Atomic action executed"}

    def resize_image(self, width: int, height: int) -> Tuple[int, int]:
        if self.image is None:
            raise RuntimeError("No image loaded in mock GIMP")
        self.image = self.image.resize((int(width), int(height)), Image.Resampling.LANCZOS)
        return (self.image.width, self.image.height)

    def crop_canvas(self, region: Union[str, List[float], Tuple[float, float, float, float]]) -> Tuple[int, int]:
        if self.image is None:
            raise RuntimeError("No image loaded in mock GIMP")

        w, h = self.image.width, self.image.height
        if region == "center":
            box = (int(w * 0.25), int(h * 0.25), int(w * 0.75), int(h * 0.75))
        elif isinstance(region, (list, tuple)) and len(region) == 4:
            box = (max(0, int(region[0])), max(0, int(region[1])), min(w, int(region[2])), min(h, int(region[3])))
        else:
            box = (int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8))

        self.image = self.image.crop(box)
        return (self.image.width, self.image.height)

    def add_text(self, text: str, position: Tuple[float, float], font_size: int = 32, color: str = "#000000") -> None:
        if self.image is None:
            raise RuntimeError("No image loaded in mock GIMP")
        draw = ImageDraw.Draw(self.image)
        # Position relative to canvas or image
        x = min(max(10, int(position[0])), self.image.width - 20)
        y = min(max(10, int(position[1])), self.image.height - 20)
        draw.text((x, y), text, fill=color)

    def apply_filter(self, filter_name: str, parameters: Optional[Dict[str, Any]] = None) -> None:
        if self.image is None:
            raise RuntimeError("No image loaded in mock GIMP")
        radius = (parameters or {}).get("radius", 5)
        self.image = self.image.filter(ImageFilter.GaussianBlur(radius=float(radius)))

    def export_file(self, path: Union[str, Path], format: str = "png", overwrite: bool = True) -> str:
        if self.image is None:
            raise RuntimeError("No image loaded in mock GIMP")
        target_path = Path(path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(target_path, format=format.upper())
        self.dialog_confirmed = True
        return str(target_path)
