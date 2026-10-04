"""Screenshot backend abstraction and capture pipeline with environment safety guards."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from PIL import Image
from pydantic import BaseModel, Field

from diagagent.environment.errors import DesktopError
from diagagent.platform.windows.desktop_session import ensure_interactive_desktop, run_on_default_desktop
from diagagent.platform.windows.desktop_geometry import DesktopGeometryContract, GeometryContractError


class ScreenshotMetadata(BaseModel):
    """Metadata bound to each captured screenshot."""

    timestamp: float = Field(default_factory=time.time)
    iso_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    width: int
    height: int
    backend: str
    path: str
    run_id: Optional[str] = None
    step: Optional[int] = None
    observation_id: Optional[str] = None
    coordinate_space: str = "screenshot"
    desktop_coordinate_space: str = "virtual_desktop"
    desktop_origin: Tuple[int, int] = (0, 0)
    screenshot_bbox: Optional[Tuple[int, int, int, int]] = None
    capture_bbox: Optional[Tuple[int, int, int, int]] = None
    pixel_scale: float = 1.0
    geometry_before: Optional[Dict[str, Any]] = None
    geometry_after: Optional[Dict[str, Any]] = None
    geometry_source: Optional[str] = None


class BaseScreenshotBackend(ABC):
    """Abstract interface for desktop screenshot capture implementations."""

    name: str = "base"
    desktop_origin: Tuple[int, int] = (0, 0)

    @abstractmethod
    def capture(self) -> Image.Image:
        """Captures full screen or primary display."""
        ...


class PILImageGrabBackend(BaseScreenshotBackend):
    """Primary screenshot backend powered by PIL.ImageGrab."""

    name: str = "pil_imagegrab"

    def __init__(self, all_screens=False, geometry_contract=None):
        self.all_screens = all_screens
        self.geometry_contract = geometry_contract
        self.geometry_evidence = {}

    def capture(self) -> Image.Image:
        from PIL import ImageGrab

        def _grab():
            ensure_interactive_desktop()
            contract = self.geometry_contract
            if self.all_screens and contract is None:
                contract = DesktopGeometryContract()
                self.geometry_contract = contract
            before = contract.check("capture_before") if contract else None
            image = ImageGrab.grab(all_screens=self.all_screens)
            after = contract.check("capture_after") if contract else None
            self.desktop_origin = (before.origin_x, before.origin_y) if before else (0, 0)
            if before:
                if image.size != (before.width, before.height):
                    contract.reject_capture(before, after, image.size)
                self.geometry_evidence = dict(geometry_before=before.to_dict(),
                    geometry_after=after.to_dict(), geometry_source=before.source)
            return image

        try:
            return run_on_default_desktop(_grab)
        except GeometryContractError:
            raise
        except Exception as e:
            raise DesktopError(f"PILImageGrabBackend failed: {e}", "BLOCKED_ENVIRONMENT") from e


class PyAutoGUIBackend(BaseScreenshotBackend):
    """Secondary/fallback screenshot backend powered by PyAutoGUI."""

    name: str = "pyautogui"

    def capture(self) -> Image.Image:
        import pyautogui

        def _grab():
            ensure_interactive_desktop()
            return pyautogui.screenshot()

        try:
            return run_on_default_desktop(_grab)
        except Exception as e:
            raise DesktopError(f"PyAutoGUIBackend failed: {e}", "BLOCKED_ENVIRONMENT") from e


class ScreenshotService:
    """Manages screenshot capturing, backend fallback, verification, and disk persistence."""

    def __init__(self, backends: Optional[List[BaseScreenshotBackend]] = None, *, all_screens=False,
                 geometry_contract=None):
        # A primary-only fallback must never masquerade as a virtual-desktop frame.
        self.backends = backends or ([PILImageGrabBackend(all_screens=True, geometry_contract=geometry_contract)] if all_screens
                                     else [PILImageGrabBackend(), PyAutoGUIBackend()])

    def capture(
        self,
        target_path: Optional[Union[str, Path]] = None,
        crop_bbox: Optional[Tuple[int, int, int, int]] = None,
        run_id: Optional[str] = None,
        step: Optional[int] = None,
        observation_id: Optional[str] = None,
    ) -> Tuple[Image.Image, ScreenshotMetadata]:
        """
        Captures a real screenshot using primary backend with fallback.
        Raises DesktopError(BLOCKED_ENVIRONMENT) if no backend can capture an interactive screen.
        """
        last_error: Optional[Exception] = None
        captured_image: Optional[Image.Image] = None
        used_backend: str = ""

        for backend in self.backends:
            try:
                img = backend.capture()
                if img is not None and img.width > 0 and img.height > 0:
                    captured_image = img
                    used_backend = backend.name
                    origin = backend.desktop_origin
                    break
            except GeometryContractError:
                # A broken coordinate contract cannot be repaired by a fallback.
                raise
            except Exception as e:
                last_error = e

        if captured_image is None:
            raise DesktopError(
                f"BLOCKED_ENVIRONMENT: All screenshot backends failed. Last error: {last_error}",
                "BLOCKED_ENVIRONMENT",
            )

        capture_bbox = (origin[0], origin[1], origin[0] + captured_image.width,
                        origin[1] + captured_image.height)
        bounds = crop_bbox if crop_bbox is not None else capture_bbox
        left, top, right, bottom = bounds
        if not (capture_bbox[0] <= left < right <= capture_bbox[2]
                and capture_bbox[1] <= top < bottom <= capture_bbox[3]):
            raise DesktopError(f"Screenshot crop {bounds} outside capture {capture_bbox}")
        if crop_bbox is not None:
            captured_image = captured_image.crop((left - origin[0], top - origin[1],
                                                  right - origin[0], bottom - origin[1]))

        # Integrity verification: reject dummy/empty images
        if captured_image.width == 0 or captured_image.height == 0:
            raise DesktopError("Captured screenshot has invalid 0 dimensions", "BLOCKED_ENVIRONMENT")

        path_str = ""
        if target_path:
            p = Path(target_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            captured_image.save(p)
            path_str = str(p)

        meta = ScreenshotMetadata(
            width=captured_image.width,
            height=captured_image.height,
            backend=used_backend,
            path=path_str,
            run_id=run_id,
            step=step,
            observation_id=observation_id,
            desktop_origin=(left, top),
            screenshot_bbox=tuple(bounds),
            capture_bbox=capture_bbox,
            **getattr(backend, "geometry_evidence", {}),
        )

        return captured_image, meta
