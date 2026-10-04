"""Deterministic geometry failures, no GIMP launch or physical input."""
import ctypes
from types import SimpleNamespace

import pytest
from PIL import Image, ImageGrab

from diagagent.actions.coordinates import desktop_action
from diagagent.actions.schema import ClickAction
from diagagent.environment.errors import DesktopError
from diagagent.environment.real_gimp import RealGIMPBackend
from diagagent.executor.gui_executor import GUIExecutor
from diagagent.observation import screenshot
from diagagent.platform.windows import desktop_session as ds
from diagagent.platform.windows.desktop_geometry import (
    DesktopGeometry, DesktopGeometryContract, GeometryContractError,
)


@pytest.fixture
def capture(monkeypatch):
    monkeypatch.setattr(screenshot, "run_on_default_desktop", lambda fn: fn())
    monkeypatch.setattr(screenshot, "ensure_interactive_desktop", lambda: True)
    calls, events = [], []
    geometry = DesktopGeometry(-200, -100, 400, 300)
    state = SimpleNamespace(current=geometry, size=(400, 300), during=None)
    def grab(**kwargs):
        calls.append(kwargs)
        image = Image.new("RGB", state.size, "white")
        image.putpixel((50, 60), (255, 0, 0))
        if state.during:
            state.current = state.during
        return image
    monkeypatch.setattr(ImageGrab, "grab", grab)
    contract = DesktopGeometryContract(geometry, reader=lambda: state.current,
        audit=lambda name, **data: events.append((name, data)))
    service = screenshot.ScreenshotService(all_screens=True, geometry_contract=contract)
    return SimpleNamespace(contract=contract, service=service, calls=calls, events=events, state=state)


def test_same_geometry_passes_with_before_after_evidence(capture, tmp_path):
    c = capture
    path = tmp_path / "shot.png"
    image, meta = c.service.capture(path)
    assert image.size == (400, 300) and path.is_file()
    assert meta.capture_bbox == c.contract.calibrated.bbox
    assert meta.geometry_before == c.contract.calibrated.to_dict()
    assert meta.geometry_after == meta.geometry_before
    assert "PER_MONITOR_AWARE_V2" in meta.geometry_source
    assert c.calls == [{"all_screens": True}]


def test_geometry_changed_before_capture_is_controlled_and_not_retried(capture, tmp_path):
    c = capture
    old = c.contract.calibrated
    c.state.current = DesktopGeometry(-200, -100, 401, 300)
    path = tmp_path / "invalid.png"
    with pytest.raises(GeometryContractError) as exc:
        c.service.capture(path)
    assert exc.value.error_type == "BLOCKED_ENVIRONMENT"
    assert not c.calls and not path.exists()
    assert c.contract.current == c.state.current and c.contract.calibrated == old
    assert not c.contract.valid
    assert c.events[-1][1]["resynchronized"]
    assert exc.value.evidence["geometry_before"]["width"] == 400
    assert exc.value.evidence["geometry_after"]["width"] == 401
    # Returning to the old layout cannot revive stale window/screenshot state.
    c.state.current = old
    with pytest.raises(GeometryContractError, match="calibration invalidated"):
        c.service.capture(path)
    assert not c.calls


def test_geometry_changed_during_capture_rejects_frame(capture, tmp_path):
    c = capture
    c.state.during = DesktopGeometry(-201, -100, 400, 300)
    path = tmp_path / "invalid.png"
    with pytest.raises(GeometryContractError):
        c.service.capture(path)
    assert len(c.calls) == 1 and not path.exists()
    assert c.events[-1][1]["source"] == "capture_after"


def test_stable_desktop_wrong_frame_size_is_distinct_failure(capture, tmp_path):
    c = capture
    c.state.size = (400, 200)
    fallback_calls = []
    c.service.backends.append(SimpleNamespace(capture=lambda: fallback_calls.append(True)))
    path = tmp_path / "invalid.png"
    with pytest.raises(GeometryContractError, match="capture extent mismatch") as exc:
        c.service.capture(path)
    assert exc.value.evidence["observed_image_size"] == [400, 200]
    assert not fallback_calls and not path.exists()
    assert not c.contract.valid and len(c.calls) == 1


def test_negative_origin_crop_pixel_and_input_round_trip(capture):
    c = capture
    image, meta = c.service.capture(crop_bbox=(-150, -40, 100, 100))
    assert image.getpixel((0, 0)) == (255, 0, 0)
    action = desktop_action(ClickAction(x=0, y=0, coordinate_space="screenshot"), meta.screenshot_bbox)
    assert (action.x, action.y) == (-150, -40)
    assert meta.pixel_scale == 1.0


@pytest.mark.parametrize("bounds", [(-2560, 0, 1920, 1660), (-1920, -1080, 3840, 2160)])
def test_multi_monitor_virtual_extent_and_transform(capture, bounds):
    c = capture
    left, top, right, bottom = bounds
    geometry = DesktopGeometry(left, top, right - left, bottom - top)
    c.state.current = c.contract.calibrated = c.contract.current = geometry
    c.state.size = (geometry.width, geometry.height)
    _, meta = c.service.capture(crop_bbox=(left, top, left + 100, top + 100))
    assert meta.capture_bbox == bounds
    action = desktop_action(ClickAction(x=50, y=60, coordinate_space="screenshot"), meta.screenshot_bbox)
    assert (action.x, action.y) == (left + 50, top + 60)


def test_timestamp_is_not_a_layout_change(capture):
    capture.state.current = DesktopGeometry(-200, -100, 400, 300, timestamp=999)
    capture.service.capture()
    assert capture.contract.valid


@pytest.mark.parametrize("inherited", [0, 1, 2])
def test_dpi_context_is_explicit_regardless_of_process_import_order(monkeypatch, inherited):
    calls = []
    state = {"context": inherited}
    def set_context(context):
        calls.append(context.value)
        old = state["context"]
        state["context"] = context.value
        return old or 10
    api = SimpleNamespace(SetThreadDpiAwarenessContext=set_context,
        GetThreadDpiAwarenessContext=lambda: state["context"],
        AreDpiAwarenessContextsEqual=lambda actual, expected: actual == expected.value)
    monkeypatch.setattr(ds, "_dpi_user32", lambda: api)
    ds.enable_dpi_awareness()
    assert calls == [ctypes.c_void_p(-4).value]


@pytest.mark.parametrize("set_ok,verified", [(False, True), (True, False)])
def test_dpi_failure_is_never_ignored(monkeypatch, set_ok, verified):
    api = SimpleNamespace(SetThreadDpiAwarenessContext=lambda ctx: set_ok,
        GetThreadDpiAwarenessContext=lambda: 1,
        AreDpiAwarenessContextsEqual=lambda *args: verified)
    monkeypatch.setattr(ds, "_dpi_user32", lambda: api)
    with pytest.raises(DesktopError) as exc:
        ds.enable_dpi_awareness()
    assert exc.value.error_type == "BLOCKED_ENVIRONMENT"


@pytest.mark.parametrize("desktop", ["Default", "isolated"])
def test_operation_failure_never_causes_worker_retry(monkeypatch, desktop):
    calls = []
    monkeypatch.setattr(ds, "get_thread_desktop", lambda: desktop)
    monkeypatch.setattr(ds, "ensure_interactive_desktop", lambda: calls.append("ensure"))
    def operation():
        calls.append("operation")
        raise DesktopError("geometry failure", "BLOCKED_ENVIRONMENT")
    with pytest.raises(DesktopError, match="geometry failure"):
        ds.run_on_default_desktop(operation)
    assert calls == ["ensure", "operation"]


def test_real_backend_invalidates_window_and_observation_on_desktop_change():
    backend = RealGIMPBackend("unused.exe")
    backend.calibration = {"hwnd": 1, "bbox": [0, 0, 100, 100]}
    backend.screenshot_bbox = [0, 0, 100, 100]
    backend._startup_ready = True
    old, new = DesktopGeometry(0, 0, 100, 100), DesktopGeometry(-100, 0, 200, 100)
    backend.desktop_geometry = DesktopGeometryContract(old, reader=lambda: new,
        invalidate=backend._invalidate_desktop_geometry)
    with pytest.raises(GeometryContractError):
        backend._assert_calibration()
    assert backend.calibration is None and backend.screenshot_bbox is None
    assert backend.lifecycle_state == "FAILED" and not backend._startup_ready


def test_input_rechecks_shared_snapshot_immediately_before_dispatch(monkeypatch):
    from diagagent.executor import gui_executor
    calls = []
    monkeypatch.setattr(ds, "run_on_default_desktop", lambda fn, *args: fn(*args))
    monkeypatch.setattr(ds, "ensure_interactive_desktop", lambda: True)
    monkeypatch.setattr(gui_executor, "HAS_PYAUTOGUI", True)
    monkeypatch.setattr(gui_executor, "pyautogui", SimpleNamespace(click=lambda **kw: calls.append(kw)))
    old = DesktopGeometry(-200, -100, 400, 300)
    state = SimpleNamespace(current=old)
    contract = DesktopGeometryContract(old, reader=lambda: state.current)
    executor = GUIExecutor(step_delay=0)
    executor.geometry_check = lambda: contract.check("input_dispatch")
    action = ClickAction(x=-150, y=-40)
    assert executor.execute(action).success
    state.current = DesktopGeometry(-200, -100, 401, 300)
    result = executor.execute(action)
    assert not result.success and not result.dispatched
    assert result.error_type == "BLOCKED_ENVIRONMENT" and len(calls) == 1


@pytest.mark.parametrize("module", ["diagagent.platform.windows.desktop_session",
                                  "diagagent.platform.windows.desktop_geometry",
                                  "diagagent.observation.screenshot"])
def test_geometry_modules_import_in_fresh_process(module):
    # Test collection order must not hide platform/environment import cycles.
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
