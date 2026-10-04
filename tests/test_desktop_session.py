"""Unit tests for Windows desktop session inspection and screenshot service."""

import os
from pathlib import Path
import pytest
from PIL import Image

from diagagent.platform.windows.desktop_session import (
    DesktopInspection,
    get_foreground_window,
    get_session_id,
    get_thread_desktop,
    get_window_station,
    inspect_desktop,
    is_interactive,
    run_on_default_desktop,
)
from diagagent.observation.screenshot import (
    PILImageGrabBackend,
    PyAutoGUIBackend,
    ScreenshotMetadata,
    ScreenshotService,
)


def test_desktop_session_info_contract():
    info = inspect_desktop()
    assert isinstance(info, DesktopInspection)
    assert isinstance(info.session_id, int)
    assert isinstance(info.window_station, str)
    assert isinstance(info.thread_desktop, str)
    assert isinstance(info.is_winsta0, bool)
    assert isinstance(info.is_default_desktop, bool)


def test_is_interactive_boolean():
    res = is_interactive()
    assert isinstance(res, bool)


def test_run_on_default_desktop_worker():
    def _probe():
        return {"pid": os.getpid(), "val": 42}

    res = run_on_default_desktop(_probe)
    assert res["val"] == 42
    assert res["pid"] == os.getpid()


def test_get_foreground_window_structure():
    fg = get_foreground_window()
    assert isinstance(fg, dict)
    assert "hwnd" in fg
    assert "title" in fg
    assert "bbox" in fg
    assert isinstance(fg["hwnd"], int)
    assert isinstance(fg["bbox"], (list, tuple))
    assert len(fg["bbox"]) == 4


def test_screenshot_service_capture(tmp_path: Path):
    target = tmp_path / "test_shot.png"
    svc = ScreenshotService()
    img, meta = svc.capture(target_path=target)

    assert isinstance(img, Image.Image)
    assert isinstance(meta, ScreenshotMetadata)
    assert meta.width > 0
    assert meta.height > 0
    assert meta.backend in ("pil_imagegrab", "pyautogui")
    assert target.is_file()
    assert target.stat().st_size > 0


def test_screenshot_service_with_crop(tmp_path: Path):
    target = tmp_path / "test_crop.png"
    svc = ScreenshotService()
    # Crop a 100x100 box
    img, meta = svc.capture(target_path=target, crop_bbox=(10, 10, 110, 110))

    assert img.width == 100
    assert img.height == 100
    assert meta.width == 100
    assert meta.height == 100
    assert target.is_file()


@pytest.mark.parametrize("result,expected", [(0, True), (258, False)])
def test_process_input_idle_is_nonblocking(monkeypatch, result, expected):
    from types import SimpleNamespace
    from diagagent.platform.windows import desktop_session as ds
    calls = []
    def idle(handle, timeout):
        calls.append((handle, timeout))
        return result
    monkeypatch.setattr(ds, "_readiness_user32", lambda: SimpleNamespace(WaitForInputIdle=idle))
    process = ds.DesktopProcess(0x123456789, 0, 10, 11)
    assert process.input_idle() is expected
    assert calls == [(0x123456789, 0)]


def test_process_input_idle_api_failure_is_not_ready(monkeypatch):
    from types import SimpleNamespace
    from diagagent.platform.windows import desktop_session as ds
    from diagagent.environment.errors import DesktopError
    monkeypatch.setattr(ds, "_readiness_user32", lambda: SimpleNamespace(WaitForInputIdle=lambda *args: 0xFFFFFFFF))
    with pytest.raises(DesktopError, match="WaitForInputIdle failed") as exc:
        ds.DesktopProcess(1, 0, 10, 11).input_idle()
    assert exc.value.error_type == "Launch / Window Error"


@pytest.mark.parametrize("valid,queue,response", [(False, True, True), (True, False, True),
                                                  (True, True, False), (True, True, True)])
def test_readiness_probes_target_queue_and_response(monkeypatch, valid, queue, response):
    from types import SimpleNamespace
    from diagagent.platform.windows import desktop_session as ds
    import ctypes
    calls = []
    target = 0x123456789
    def thread(hwnd, pid):
        assert hwnd == target
        pid._obj.value = 10
        return 20
    def gui(tid, info):
        assert tid == 20 and info._obj.cbSize == ctypes.sizeof(ds._GUIThreadInfo)
        return queue
    def send(hwnd, msg, wp, lp, flags, timeout, result):
        calls.append((hwnd, msg, flags, timeout))
        return response
    api = SimpleNamespace(IsWindow=lambda h: valid, IsWindowVisible=lambda h: True,
                          IsWindowEnabled=lambda h: True, GetForegroundWindow=lambda: target,
                          GetAncestor=lambda h, kind: target, GetWindowThreadProcessId=thread,
                          GetGUIThreadInfo=gui, SendMessageTimeoutW=send)
    dispatched = []
    def desktop(fn):
        dispatched.append(True)
        return fn()
    monkeypatch.setattr(ds, "run_on_default_desktop", desktop)
    monkeypatch.setattr(ds, "ensure_interactive_desktop", lambda: True)
    monkeypatch.setattr(ds, "_readiness_user32", lambda: api)
    state = ds.inspect_window_readiness(target, timeout_ms=37)
    assert dispatched == [True]
    assert state["valid"] is valid
    assert state["input_queue"] is (valid and queue)
    assert state["responsive"] is (valid and queue and response)
    assert state["foreground"] == target
    assert calls == ([(target, 0, 0x23, 37)] if valid and queue else [])


def test_native_readiness_rejects_null_window():
    from diagagent.platform.windows.desktop_session import inspect_window_readiness
    state = inspect_window_readiness(0)
    assert not state["valid"] and not state["input_queue"] and not state["responsive"]
