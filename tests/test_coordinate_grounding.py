"""Offline coordinate regressions: no model, process launch, or physical input."""
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from diagagent.actions.coordinates import desktop_action
from diagagent.actions.parser import ActionParser, ActionParseError
from diagagent.actions.schema import ClickAction, DragAction
from diagagent.agent.context import ContextBuilder
from diagagent.environment.errors import DesktopError
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.executor import gui_executor
from diagagent.observation.models import AgentObservation, Observation
from diagagent.observation.screenshot import BaseScreenshotBackend, PILImageGrabBackend, ScreenshotService
from diagagent.platform.windows import desktop_session


@pytest.fixture
def execution(monkeypatch, tmp_path):
    # Build a real boundary with only Win32/PyAutoGUI effects replaced.
    env = GIMPEnvironment(backend="real", gimp_executable="unused.exe", run_root=tmp_path)
    env.deadline = time.monotonic() + 100
    owned = [SimpleNamespace(_hWnd=12, left=-2400, top=-900, right=0, bottom=1080),
             SimpleNamespace(_hWnd=11, left=0, top=0, right=1440, bottom=900)]
    monkeypatch.setattr(env.backend, "windows", lambda: owned)
    monkeypatch.setattr(desktop_session, "get_virtual_screen_bbox", lambda: (-2560, -1080, 1920, 1080))
    monkeypatch.setattr(desktop_session, "ensure_interactive_desktop", lambda: True)
    calls, events = [], []
    monkeypatch.setattr(gui_executor, "HAS_PYAUTOGUI", True)
    monkeypatch.setattr(gui_executor, "pyautogui", SimpleNamespace(
        click=lambda **kw: calls.append(("click", kw)),
        moveTo=lambda x, y: calls.append(("move", (x, y))),
        dragTo=lambda x, y, **kw: calls.append(("drag", (x, y)))))
    env.executor.step_delay = 0
    env._event = lambda name, **data: events.append((name, data))
    return SimpleNamespace(env=env, calls=calls, events=events, owned=owned)


@pytest.mark.parametrize("point", [(-1891, 775), (-400, -300), (100, 200)])
def test_desktop_coordinates_reach_pyautogui_unchanged(execution, point):
    s = execution
    s.env._execute_primitive({"type": "click", "x": point[0], "y": point[1]})
    assert s.calls == [("click", {"x": point[0], "y": point[1], "button": "left", "clicks": 1})]
    event = next(data for name, data in s.events if name == "coordinate_validation")
    assert event["coordinate_space"] == "virtual_desktop"
    assert event["desktop_bbox"] == [-2560, -1080, 1920, 1080]


def test_screenshot_origin_applied_once_to_click_and_drag(execution):
    s = execution
    s.env.backend.screenshot_bbox = [-2400, -900, 0, 1080]
    s.env._execute_primitive({"type": "click", "x": 509, "y": 1675, "coordinate_space": "screenshot"})
    assert s.calls[0][1]["x"] == -1891 and s.calls[0][1]["y"] == 775
    action = DragAction(start=(10, 20), end=(200, 300), coordinate_space="screenshot")
    resolved = desktop_action(action, s.env.backend.screenshot_bbox)
    assert desktop_action(resolved, s.env.backend.screenshot_bbox) == resolved
    s.env._execute_primitive(action)
    assert s.calls[1:] == [("move", (-2390, -880)), ("drag", (-2200, -600))]


@pytest.mark.parametrize("action", [
    {"type": "click", "x": -2561, "y": 20},
    {"type": "click", "x": 1920, "y": 20},
    {"type": "click", "x": 1500, "y": 20},  # Desktop, but not owned.
    {"type": "click", "x": 1439.8, "y": 20},  # Rounds out of owned HWND.
    {"type": "click", "x": -2400.1, "y": 20},  # Rounds into owned HWND.
    {"type": "drag", "start": [-1891, 775], "end": [1500, 200]},
    {"type": "click", "x": 0, "y": -1081},
    {"type": "click", "x": 0, "y": 1080},
])
def test_invalid_pointer_is_rejected_without_dispatch(execution, action):
    with pytest.raises(DesktopError) as exc:
        execution.env._execute_primitive(action)
    assert exc.value.error_type == "UI Grounding Error"
    assert not execution.calls and execution.env.executed_primitives == 0


@pytest.mark.parametrize("action", [
    {"type": "click", "x": float("nan"), "y": 0},
    {"type": "drag", "start": [0, 0], "end": [float("inf"), 1]},
    {"type": "click", "x": 1, "y": 2, "coordinate_space": "screen_local"},
    {"type": "click", "x": 1, "y": 2, "coordinate_space": "normalized"},
])
def test_schema_rejects_nonfinite_or_ambiguous_space(action):
    with pytest.raises(ActionParseError):
        ActionParser.parse(action)


@pytest.mark.parametrize("point", [(-1, 0), (100, 0), (99.8, 0), (0, 100)])
def test_screenshot_bounds_not_clipped(point):
    with pytest.raises(ValueError, match="outside screenshot"):
        desktop_action(ClickAction(x=point[0], y=point[1], coordinate_space="screenshot"), (-200, -100, -100, 0))


def test_screenshot_space_requires_metadata_and_cannot_reach_executor(execution):
    action = ClickAction(x=20, y=20, coordinate_space="screenshot")
    with pytest.raises(DesktopError, match="screenshot_bbox"):
        execution.env._execute_primitive(action)
    result = execution.env.executor.execute(action)
    assert not result.success and not execution.calls


class Frame(BaseScreenshotBackend):
    name = "synthetic_virtual_desktop"
    desktop_origin = (-200, -100)

    def capture(self):
        image = Image.new("RGB", (400, 300), "white")
        image.putpixel((50, 60), (255, 0, 0))
        return image


def test_capture_crop_and_action_round_trip(tmp_path):
    path = tmp_path / "frame.png"
    image, meta = ScreenshotService([Frame()]).capture(path, crop_bbox=(-150, -40, 50, 100))
    assert image.size == (200, 140) and image.getpixel((0, 0)) == (255, 0, 0)
    assert meta.desktop_origin == (-150, -40) and meta.pixel_scale == 1
    assert meta.capture_bbox == (-200, -100, 200, 200)
    action = desktop_action(ClickAction(x=0, y=0, coordinate_space="screenshot"), meta.screenshot_bbox)
    assert (action.x, action.y) == (-150, -40)
    with Image.open(path) as saved:
        assert saved.getpixel((0, 0)) == (255, 0, 0)


def test_capture_rejects_padding_outside_source(tmp_path):
    path = tmp_path / "invalid.png"
    with pytest.raises(DesktopError, match="outside capture"):
        ScreenshotService([Frame()]).capture(path, crop_bbox=(-201, -100, 0, 0))
    assert not path.exists()


def test_virtual_capture_uses_all_screens_and_records_origin(monkeypatch):
    from PIL import ImageGrab
    from diagagent.observation import screenshot
    monkeypatch.setattr(screenshot, "run_on_default_desktop", lambda fn: fn())
    monkeypatch.setattr(screenshot, "ensure_interactive_desktop", lambda: True)
    monkeypatch.setattr(desktop_session, "get_virtual_screen_bbox", lambda: (-200, -100, 200, 200))
    calls = []
    def grab(**kw):
        calls.append(kw)
        return Frame().capture()
    monkeypatch.setattr(ImageGrab, "grab", grab)
    _, meta = ScreenshotService(all_screens=True).capture(crop_bbox=(-150, -40, 50, 100))
    assert calls == [{"all_screens": True}]
    assert meta.desktop_origin == (-150, -40)
    assert all(isinstance(b, PILImageGrabBackend) for b in ScreenshotService(all_screens=True).backends)


def test_virtual_metrics_are_signed_origin_plus_extent(monkeypatch):
    metrics = {76: -2560, 77: -1080, 78: 4480, 79: 2160}
    monkeypatch.setattr(desktop_session, "ctypes", SimpleNamespace(windll=SimpleNamespace(
        user32=SimpleNamespace(GetSystemMetrics=metrics.__getitem__))))
    monkeypatch.setattr(desktop_session, "run_on_default_desktop", lambda fn: fn())
    monkeypatch.setattr(desktop_session, "ensure_interactive_desktop", lambda: True)
    assert desktop_session.get_virtual_screen_bbox() == (-2560, -1080, 1920, 1080)


def test_public_context_explains_frame_without_private_state():
    private = Observation(screenshot_path="frame.png", screenshot_bbox=[-200, -100, 200, 200])
    private.ui_state.image_size = (777, 555)
    observation = AgentObservation.from_private(private, ["screenshot", "manual_ui_bbox"])
    text = ContextBuilder().build_context({"instruction": "resize"}, observation)[-1].content
    assert '"coordinate_space": "virtual_desktop"' in text
    assert '"screenshot_bbox"' in text and "left/top once" in text
    assert "777" not in text and "ui_state" not in text


@pytest.mark.parametrize("action,points", [
    ({"type": "set_dialog_field", "dialog": "Scale Image", "field": "Width", "value": 512},
     [(-2035, -664), (-2160, -680)]),
    ({"type": "set_dialog_field", "dialog": "Scale Image", "field": "Height", "value": 512},
     [(-2160, -645)]),
    ({"type": "click_dialog_button", "dialog": "Scale Image", "button": "Scale"},
     [(-1899, -322)]),
])
def test_structural_lowering_retains_hwnd_desktop_space(execution, monkeypatch, action, points):
    from diagagent.lowering.structural_lowering import StructuralLowerer
    dialog = SimpleNamespace(left=-2300, top=-800, width=626, bottom=-300)
    monkeypatch.setattr(execution.env.backend, "find_dialog", lambda name: dialog)
    lowered = StructuralLowerer(execution.env.backend).lower(ActionParser.parse(action))
    for pointer in (p for p in lowered if p.type == "click"):
        assert pointer.coordinate_space == "virtual_desktop"
        execution.env._execute_primitive(pointer)
    assert [(data["x"], data["y"]) for kind, data in execution.calls] == points


def test_real_observation_persists_capture_geometry(execution, monkeypatch, tmp_path):
    from diagagent.environment import real_gimp
    from diagagent.observation import screenshot
    from diagagent.observation.collector import ObservationCollector
    s = execution
    main = SimpleNamespace(_hWnd=11, left=0, top=0, right=100, bottom=100,
                           box=(0, 0, 100, 100), title="GIMP")
    dialog = SimpleNamespace(_hWnd=12, left=-150, top=-40, right=50, bottom=100)
    s.owned[:] = [main, dialog]
    s.env.backend.window = main
    s.env.backend.window_bbox = [0, 0, 100, 100]
    # This observation-only fixture starts after startup calibration committed.
    s.env.backend.calibration = {"hwnd": 11, "bbox": [0, 0, 100, 100],
                                 "timestamp": "2026-09-27T00:00:00+00:00", "lifecycle_state": "READY"}
    s.env.backend.lifecycle_state = "READY"
    s.env.backend._startup_ready = True
    monkeypatch.setattr(real_gimp, "ctypes", SimpleNamespace(windll=SimpleNamespace(
        user32=SimpleNamespace(GetForegroundWindow=lambda: 11))))
    def service(**kwargs):
        assert kwargs == {"all_screens": True, "geometry_contract": s.env.backend.desktop_geometry}
        return ScreenshotService([Frame()])
    monkeypatch.setattr(screenshot, "ScreenshotService", service)
    s.env.backend.audit = s.env._event
    observation = ObservationCollector(tmp_path).collect(0, s.env.backend, screenshot_name="obs.png")
    assert observation.screenshot_bbox == [-150, -40, 100, 100]
    assert observation.screenshot_metadata["desktop_origin"] == (-150, -40)
    public = AgentObservation.from_private(observation, ["screenshot"])
    assert public.screenshot_bbox == [-150, -40, 100, 100]
    assert public.ui_primitives is None
    assert s.events[-1][0] == "screenshot_captured"
