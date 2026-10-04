"""Physical desktop snapshot shared by observation, window and input boundaries."""
from dataclasses import asdict, dataclass, field
import time

from diagagent.environment.errors import DesktopError
from diagagent.platform.windows import desktop_session


@dataclass(frozen=True)
class DesktopGeometry:
    origin_x: int
    origin_y: int
    width: int
    height: int
    # Physical screenshot pixels per desktop coordinate unit, NOT UI zoom/DPI.
    scale: float = 1.0
    timestamp: float = field(default_factory=time.time)
    source: str = "Win32.GetSystemMetrics / WinSta0.Default / PER_MONITOR_AWARE_V2"

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0 or self.scale != 1.0:
            raise DesktopError("Invalid physical desktop geometry", "BLOCKED_ENVIRONMENT")

    @property
    def bbox(self):
        return (self.origin_x, self.origin_y,
                self.origin_x + self.width, self.origin_y + self.height)

    def matches(self, other):
        return self.bbox == other.bbox and self.scale == other.scale

    def to_dict(self):
        return asdict(self)


def read_desktop_geometry():
    left, top, right, bottom = desktop_session.get_virtual_screen_bbox()
    return DesktopGeometry(left, top, right - left, bottom - top)


class GeometryContractError(DesktopError):
    def __init__(self, reason, evidence):
        super().__init__(f"Desktop geometry contract: {reason}; {evidence}", "BLOCKED_ENVIRONMENT")
        self.evidence = evidence


class DesktopGeometryContract:
    """A changed desktop is observed, but cannot silently replace calibration.

    Resynchronization updates current state and latches invalidation. A fresh
    preflight/calibration is required before any further capture or dispatch.
    """
    def __init__(self, calibrated=None, *, reader=None, audit=None, invalidate=None):
        self.reader = reader or read_desktop_geometry
        self.audit = audit or (lambda *args, **kwargs: None)
        self.invalidate = invalidate or (lambda reason: None)
        self.calibrated = calibrated or self.reader()
        self.current = self.calibrated
        self.valid = True

    def check(self, source):
        before = self.current
        after = self.reader()
        self.current = after
        evidence = dict(source=source, geometry_before=before.to_dict(),
                        geometry_after=after.to_dict(), calibrated_geometry=self.calibrated.to_dict())
        changed = not self.calibrated.matches(after)
        if changed or not self.valid:
            self.valid = False
            self.audit("desktop_geometry_invalidated", **evidence,
                       resynchronized=True, requires_recalibration=True)
            self.invalidate("desktop geometry changed; explicit recalibration required")
            raise GeometryContractError("changed" if changed else "calibration invalidated", evidence)
        self.audit("desktop_geometry_validated", **evidence)
        return after

    def reject_capture(self, before, after, size):
        self.valid = False
        evidence = dict(source="PIL.ImageGrab", geometry_before=before.to_dict(),
                        geometry_after=after.to_dict(), observed_image_size=list(size))
        self.audit("capture_geometry_mismatch", **evidence, requires_recalibration=True)
        self.invalidate("capture dimensions disagree with physical desktop")
        raise GeometryContractError("capture extent mismatch", evidence)
