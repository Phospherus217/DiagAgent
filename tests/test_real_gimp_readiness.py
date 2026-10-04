"""Startup state tests use recorded-shaped snapshots, never a real/mock GUI window."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from diagagent.config import DesktopConfig
from diagagent.environment.errors import DesktopError
from diagagent.environment import real_gimp
from diagagent.platform.windows import desktop_session


@pytest.fixture
def startup(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    def advance(seconds):
        clock.now += seconds
    monkeypatch.setattr(real_gimp, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=advance))
    backend = real_gimp.RealGIMPBackend("unused.exe", DesktopConfig(startup_timeout=4, gui_timeout=1, focus_timeout=2))
    process = SimpleNamespace(pid=10, returncode=None, poll=lambda: process.returncode, input_idle=lambda: True)
    backend.process = process
    main = SimpleNamespace(_hWnd=11, title="GNU Image Manipulation Program", width=1440,
                           box=(0, 0, 1440, 900))
    welcome = SimpleNamespace(_hWnd=12, title="Welcome to GIMP 3.2.4", width=626, right=729, bottom=776)
    visible = [main]
    states = {w._hWnd: dict(hwnd=w._hWnd, valid=True, visible=True, enabled=True,
                           input_queue=True, responsive=True, root=w._hWnd, foreground=w._hWnd)
              for w in (main, welcome)}
    requests, actions, events, calibrations = [], [], [], []
    monkeypatch.setattr(backend, "windows", lambda: list(visible))
    monkeypatch.setattr(desktop_session, "inspect_window_readiness", lambda hwnd, **kw: dict(states[hwnd]))
    def calibrate(size, **kwargs):
        calibrations.append(size)
        backend.window.box = (0, 0, *size)
        return list(backend.window.box)
    monkeypatch.setattr(backend, "fix_window", calibrate)
    monkeypatch.setattr(backend, "_request_focus", lambda window: requests.append(window._hWnd))
    backend.gui_execute = actions.append
    backend.audit = lambda event, **data: events.append((event, data))
    return SimpleNamespace(backend=backend, clock=clock, main=main, welcome=welcome, visible=visible,
                           states=states, requests=requests, actions=actions, events=events,
                           calibrations=calibrations)


def failure(s, match):
    with pytest.raises(DesktopError, match=match) as exc:
        s.backend._wait_startup_ready()
    assert exc.value.error_type == "Launch / Window Error"
    assert not s.backend._startup_ready
    assert s.events[-1][0] == "startup_failed"


def test_process_exists_welcome_only_is_not_main_readiness(startup):
    s = startup
    s.visible[:] = [s.welcome]
    s.backend.gui_execute = lambda action: (s.actions.append(action), s.visible.clear())
    failure(s, "startup readiness")
    assert len(s.actions) == 1
    assert s.backend.window is None
    assert not s.calibrations


@pytest.mark.parametrize("field", ["valid", "visible", "enabled", "input_queue", "responsive"])
def test_main_must_be_interactive_before_focus_or_success(startup, field):
    s = startup
    s.states[11][field] = False
    failure(s, "startup readiness")
    assert not s.requests and not s.actions and not s.calibrations


def test_process_not_input_idle_does_not_dispatch(startup):
    s = startup
    s.backend.process.input_idle = lambda: False
    failure(s, "startup readiness")
    assert not s.requests and not s.actions


def test_ready_main_is_required_for_success(startup):
    s = startup
    s.backend._wait_startup_ready()
    assert s.backend._startup_ready
    assert s.backend.window is s.main
    assert len(s.calibrations) == 1
    assert s.events[-1][0] == "startup_ready"
    assert s.events[-1][1]["readiness"]["target"]["foreground"] == 11


@pytest.mark.parametrize("late", [False, True])
def test_welcome_before_or_after_main_mapping_is_closed_once(startup, monkeypatch, late):
    s = startup
    if late:
        def calibrate(*args, **kwargs):
            s.calibrations.append(True)
            s.visible.append(s.welcome)
            return [0, 0, 1440, 900]
        monkeypatch.setattr(s.backend, "fix_window", calibrate)
    else:
        s.visible.append(s.welcome)
    def dismiss(action):
        s.actions.append(action)
        s.visible.remove(s.welcome)
    s.backend.gui_execute = dismiss
    s.backend._wait_startup_ready()
    assert s.backend.window is s.main and s.backend._startup_ready
    assert len(s.actions) == 1
    names = [name for name, _ in s.events]
    assert names.index("welcome_close_requested") < names.index("welcome_dismissed") < names.index("startup_ready")


def test_unresponsive_welcome_is_not_clicked(startup):
    s = startup
    s.visible.append(s.welcome)
    s.states[12]["responsive"] = False
    failure(s, "startup readiness")
    assert not s.actions and not s.requests


def test_welcome_dismissal_failure_has_no_repeat_click(startup):
    s = startup
    s.visible.append(s.welcome)
    failure(s, "welcome close")
    assert len(s.actions) == 1
    assert "welcome_dismissed" not in [name for name, _ in s.events]


def test_focus_failure_is_not_retried_or_ignored(startup):
    s = startup
    s.states[11]["foreground"] = 99
    failure(s, "focus on owned GIMP window")
    assert s.requests == [11]
    assert not s.actions
    assert s.events[-1][1]["readiness"]["expired_focus_hwnd"] == 11
    assert s.events[-1][1]["readiness"]["target"]["foreground"] == 99


def test_delayed_focus_success_requires_observed_foreground(startup, monkeypatch):
    s = startup
    s.states[11]["foreground"] = 99
    def acquire(window):
        s.requests.append(window._hWnd)
        s.states[11]["foreground"] = 11
    monkeypatch.setattr(s.backend, "_request_focus", acquire)
    s.backend._wait_startup_ready()
    assert s.backend._startup_ready and s.requests == [11]


def test_late_welcome_redirects_pending_main_focus(startup, monkeypatch):
    s = startup
    s.states[11]["foreground"] = 99
    def acquire(window):
        s.requests.append(window._hWnd)
        s.visible.append(s.welcome)
    def dismiss(action):
        s.actions.append(action)
        s.visible.remove(s.welcome)
        s.states[11]["foreground"] = 11
    monkeypatch.setattr(s.backend, "_request_focus", acquire)
    s.backend.gui_execute = dismiss
    s.backend._wait_startup_ready()
    assert s.backend._startup_ready and s.requests == [11] and len(s.actions) == 1


def test_configured_focus_budget_accepts_observed_delayed_focus_without_retry(startup, monkeypatch):
    s = startup
    s.backend.config.focus_timeout = 5
    s.backend.config.startup_timeout = 8
    s.states[11]["foreground"] = 99
    def inspect(hwnd, **kwargs):
        state = dict(s.states[hwnd])
        if s.clock.now >= 3:
            state["foreground"] = hwnd
        return state
    monkeypatch.setattr(desktop_session, "inspect_window_readiness", inspect)
    s.backend._wait_startup_ready()
    assert s.backend._startup_ready and s.requests == [11]
    assert 3 <= s.clock.now < 5


def test_late_welcome_does_not_erase_an_expired_focus_timeout(startup, monkeypatch):
    s = startup
    s.states[11]["foreground"] = 99
    def expire_then_welcome(window):
        s.requests.append(window._hWnd)
        s.clock.now += 2.01
        s.visible.append(s.welcome)
    monkeypatch.setattr(s.backend, "_request_focus", expire_then_welcome)
    failure(s, "focus on owned GIMP window")
    assert s.requests == [11] and not s.actions


def test_disappeared_focus_target_fails(startup, monkeypatch):
    s = startup
    s.states[11]["foreground"] = 99
    monkeypatch.setattr(s.backend, "_request_focus", lambda window: s.visible.clear())
    failure(s, "disappeared")


def test_process_exit_fails_before_dispatch(startup):
    s = startup
    s.backend.process.returncode = 1
    failure(s, "Owned GIMP exited: 1")
    assert not s.requests and not s.actions


def test_run_deadline_bounds_startup(startup):
    s = startup
    s.backend.deadline = 0.4
    s.states[11]["responsive"] = False
    failure(s, "startup readiness")
    assert s.clock.now <= 0.4


def test_unfinished_launch_cannot_be_reused(startup):
    with pytest.raises(DesktopError, match="has not completed startup"):
        startup.backend.launch()


def test_open_image_cannot_bypass_unfinished_startup(startup, tmp_path):
    asset = tmp_path / "input.png"
    asset.write_bytes(b"not used: readiness must fail first")
    with pytest.raises(DesktopError, match="has not completed startup"):
        startup.backend.open_image(asset)
    assert not startup.actions


def test_reappearing_welcome_is_not_dismissed_again(startup):
    s = startup
    s.visible.append(s.welcome)
    def dismiss(action):
        s.actions.append(action)
        s.visible.remove(s.welcome)
    def audit(event, **data):
        s.events.append((event, data))
        if event == "welcome_dismissed":
            s.visible.append(s.welcome)
    s.backend.gui_execute = dismiss
    s.backend.audit = audit
    failure(s, "reappeared")
    assert len(s.actions) == 1


def test_focus_request_keeps_activation_error_and_single_fallback(startup, monkeypatch):
    s = startup
    dispatches = []
    def desktop(fn):
        dispatches.append(True)
        return fn()
    def denied():
        raise OSError("foreground activation denied")
    snapshot = SimpleNamespace(_hWnd=11, title="GNU Image Manipulation Program", isMinimized=False,
                               left=0, top=0, right=1440, bottom=900, width=1440, activate=denied)
    monkeypatch.setattr(desktop_session, "run_on_default_desktop", desktop)
    monkeypatch.setattr(real_gimp, "ctypes", SimpleNamespace(windll=SimpleNamespace(
        user32=SimpleNamespace(GetForegroundWindow=lambda: 99))))
    real_gimp.RealGIMPBackend._request_focus(s.backend, snapshot)
    assert dispatches == [True]
    assert s.actions == [{"type": "click", "x": 200, "y": 12, "coordinate_space": "virtual_desktop"}]
    assert s.events[-1][0] == "focus_activation_error"
    assert "denied" in s.events[-1][1]["error_message"]


def test_startup_failure_preserves_environment_responsibility(tmp_path, monkeypatch):
    from diagagent.environment.gimp_env import GIMPEnvironment
    from diagagent.agent.runtime import AgentRuntime
    from diagagent.agent.rule_agent import RuleAgent
    from diagagent.tasks import load_task
    task = load_task(Path(__file__).resolve().parents[1] / "benchmark/smoke/resize_export.yaml")
    env = GIMPEnvironment(backend="real", gimp_executable="unused.exe", run_root=tmp_path)
    def fail():
        raise DesktopError("Startup focus acquisition failed", "Launch / Window Error")
    monkeypatch.setattr(env.backend, "launch", fail)
    monkeypatch.setattr(env.backend, "open_image", lambda *args: pytest.fail("task started before readiness"))
    report = AgentRuntime(env).run_task(task, RuleAgent())
    assert not report.success and report.responsibility.value == "Environment"
    result = json.loads((env.trace_recorder.run_dir / "result.json").read_text())
    assert result["first_failure_step"] == 0
    assert result["first_failure_type"] == "Launch / Window Error"
    assert result["termination_reason"] == "environment_error"
    assert result["model_call_count"] == 0 and result["executed_primitives"] == 0
    assert not (env.trace_recorder.run_dir / "artifacts/output.png").exists()


@pytest.mark.parametrize("valid_desktop", [True, False])
def test_run2_welcome_hwnd_through_real_boundary(startup, monkeypatch, tmp_path, valid_desktop):
    """Exercise startup -> parser -> bounds/ownership -> executor, offline."""
    import time
    from diagagent.environment.gimp_env import GIMPEnvironment
    from diagagent.executor import gui_executor
    from diagagent.diagnosis.taxonomy import get_responsibility
    s = startup
    # right/bottom reproduce the archived point; the rest is synthetic geometry.
    s.welcome.left, s.welcome.top = -2457, 200
    s.welcome.right, s.welcome.bottom = -1831, 810
    s.main.left, s.main.top, s.main.right, s.main.bottom = 0, 0, 1440, 900
    s.visible.append(s.welcome)
    env = GIMPEnvironment(backend="real", gimp_executable="unused.exe", run_root=tmp_path)
    env.backend = s.backend
    env.deadline = time.monotonic() + 100
    env._event = lambda name, **data: s.events.append((name, data))
    env.executor.step_delay = 0
    s.backend.gui_execute = env._execute_primitive
    monkeypatch.setattr(desktop_session, "get_virtual_screen_bbox",
                        lambda: (-2560, 0, 1920, 1080) if valid_desktop else (0, 0, 1920, 1080))
    monkeypatch.setattr(desktop_session, "ensure_interactive_desktop", lambda: True)
    monkeypatch.setattr(gui_executor, "HAS_PYAUTOGUI", True)
    def click(**kwargs):
        s.actions.append(kwargs)
        s.visible.remove(s.welcome)
    monkeypatch.setattr(gui_executor, "pyautogui", SimpleNamespace(click=click))
    if valid_desktop:
        s.backend._wait_startup_ready()
        assert s.backend._startup_ready
        assert s.actions == [{"x": -1891, "y": 775, "button": "left", "clicks": 1}]
        assert env.executed_primitives == 1
    else:
        with pytest.raises(DesktopError) as exc:
            s.backend._wait_startup_ready()
        assert get_responsibility(exc.value.error_type).value == "Execution"
        assert not s.actions and env.executed_primitives == 0
        assert s.events[-1][0] == "startup_failed"
    grounded = next(data for name, data in s.events if name == "startup_pointer_grounded")
    assert grounded["source"] == "welcome_hwnd"
    assert grounded["action"]["x"] == -1891


def test_stable_geometry_commits_only_after_independent_probes(startup):
    s = startup
    s.backend._wait_startup_ready()
    assert s.backend.lifecycle_state == "READY"
    assert s.backend.pending_calibration is None
    record = s.backend.calibration
    assert record["hwnd"] == 11 and record["bbox"] == [0, 0, 1440, 900]
    assert record["lifecycle_state"] == "READY"
    from datetime import datetime
    assert datetime.fromisoformat(record["timestamp"]).utcoffset().total_seconds() == 0
    names = [name for name, _ in s.events]
    probes = [data for name, data in s.events if name == "startup_geometry_observed"]
    assert [p["phase"] for p in probes] == ["before_calibration"] * 3 + ["after_calibration"] * 3
    assert names.index("calibration_committed") < names.index("startup_ready")
    assert len(s.calibrations) == 1


def test_same_hwnd_geometry_change_invalidates_candidate(startup):
    s = startup
    def audit(event, **data):
        s.events.append((event, data))
        if event == "startup_geometry_observed" and data["phase"] == "before_calibration" and data["bbox"][2] == 1440:
            s.main.box = (0, 0, 816, 639)
    s.backend.audit = audit
    s.backend._wait_startup_ready()
    invalid = [d for e, d in s.events if e == "calibration_invalidated"]
    assert invalid[0]["previous"]["hwnd"] == 11
    assert invalid[0]["actual_bbox"] == [0, 0, 816, 639]
    before = [d for e, d in s.events if e == "startup_geometry_observed" and d["phase"] == "before_calibration"]
    assert [d["samples"] for d in before] == [1, 1, 2, 3]
    assert len(s.calibrations) == 1
    assert s.backend.calibration["bbox"] == [0, 0, 1440, 900]


@pytest.mark.parametrize("phase", ["before_calibration", "after_calibration"])
def test_welcome_invalidates_pending_calibration(startup, phase):
    s = startup
    inserted = False
    def audit(event, **data):
        nonlocal inserted
        s.events.append((event, data))
        if event == "startup_geometry_observed" and data["phase"] == phase and not inserted:
            inserted = True
            s.visible.append(s.welcome)
        if event == "startup_lifecycle" and data["lifecycle_state"] == "WELCOME_PENDING":
            assert s.backend.pending_calibration is None
            assert s.backend.calibration is None
            assert not s.backend._startup_ready
    def dismiss(action):
        s.actions.append(action)
        s.visible.remove(s.welcome)
    s.backend.audit = audit
    s.backend.gui_execute = dismiss
    s.backend._wait_startup_ready()
    invalid = [d for e, d in s.events if e == "calibration_invalidated"]
    assert any(d["reason"] == "startup welcome observed" and d["previous"]["phase"] == phase for d in invalid)
    names = [e for e, _ in s.events]
    assert names.index("welcome_dismissed") < names.index("calibration_committed")
    assert len(s.calibrations) == len(s.actions) == 1


def test_interactive_main_with_unstable_geometry_never_ready(startup):
    s = startup
    def audit(event, **data):
        s.events.append((event, data))
        if event == "startup_geometry_observed":
            width = 816 if s.main.box[2] == 1440 else 1440
            s.main.box = (0, 0, width, 900)
    s.backend.audit = audit
    failure(s, "startup readiness")
    assert not s.calibrations and s.backend.calibration is None
    assert s.backend.pending_calibration is None
    assert all(e != "startup_ready" for e, _ in s.events)
    assert s.clock.now <= 4


def test_same_hwnd_changes_after_resize_fails_without_resize_retry(startup):
    s = startup
    def audit(event, **data):
        s.events.append((event, data))
        if event == "startup_geometry_observed" and data["phase"] == "after_calibration":
            s.main.box = (0, 0, 816, 639)
    s.backend.audit = audit
    failure(s, "Window changed during calibration")
    assert len(s.calibrations) == 1
    assert s.backend.calibration is None and s.backend.pending_calibration is None
    assert any(e == "calibration_invalidated" and d["actual_bbox"] == [0, 0, 816, 639] for e, d in s.events)


@pytest.mark.parametrize("entry", ["launch", "capture_screenshot", "fix_window"])
def test_committed_same_hwnd_movement_invalidates_and_fails(startup, monkeypatch, tmp_path, entry):
    s = startup
    s.backend._wait_startup_ready()
    s.main.box = (0, 0, 816, 639)
    monkeypatch.setattr(desktop_session, "ensure_interactive_desktop", lambda: True)
    monkeypatch.setattr(real_gimp, "ctypes", SimpleNamespace(windll=SimpleNamespace(
        user32=SimpleNamespace(GetForegroundWindow=lambda: 11))))
    output = tmp_path / "must_not_capture.png"
    with pytest.raises(DesktopError, match="Window moved since calibration"):
        if entry == "capture_screenshot":
            s.backend.capture_screenshot(output)
        elif entry == "fix_window":
            real_gimp.RealGIMPBackend.fix_window(s.backend, (1440, 900))
        else:
            s.backend.launch()
    assert s.backend.calibration is None and not s.backend._startup_ready
    assert s.backend.lifecycle_state == "FAILED"
    assert s.backend.window_bbox == [0, 0, 1440, 900]  # Never accept the new bbox.
    assert not output.exists() and len(s.calibrations) == 1


def test_main_identity_rebound_before_calibration(startup):
    s = startup
    replacement = SimpleNamespace(**vars(s.main))
    replacement._hWnd = 13
    s.states[13] = {**s.states[11], "hwnd": 13, "root": 13, "foreground": 13}
    def audit(event, **data):
        s.events.append((event, data))
        if event == "startup_geometry_observed" and data["hwnd"] == 11:
            s.visible[:] = [replacement]
    s.backend.audit = audit
    s.backend._wait_startup_ready()
    assert s.backend.window is replacement
    assert s.backend.calibration["hwnd"] == 13 and len(s.calibrations) == 1
    assert any(e == "calibration_invalidated" and d["previous"]["hwnd"] == 11 for e, d in s.events)


def test_secondary_startup_dialog_blocks_commit(startup):
    s = startup
    modal = SimpleNamespace(_hWnd=13, title="Startup dialog", width=500)
    def audit(event, **data):
        s.events.append((event, data))
        if event == "startup_geometry_observed" and data["phase"] == "after_calibration":
            s.visible.append(modal)
    s.backend.audit = audit
    failure(s, "startup readiness")
    assert len(s.calibrations) == 1 and s.backend.calibration is None
    assert any(e == "calibration_invalidated" and d["reason"] == "startup modal or secondary window present"
               for e, d in s.events)


def test_real_calibration_moves_once_and_does_not_commit(startup):
    s = startup
    operations = []
    s.main.isMaximized = s.main.isMinimized = False
    s.main.left = s.main.top = 0
    s.main.height = 900
    s.main.moveTo = lambda *args: operations.append(("move", args))
    s.main.resizeTo = lambda *args: operations.append(("resize", args))
    s.backend.window = s.main
    bbox = real_gimp.RealGIMPBackend.fix_window(s.backend, (1440, 900), startup_end=4)
    assert bbox == [0, 0, 1440, 900]
    assert operations == [("move", (0, 0)), ("resize", (1440, 900))]
    assert s.backend.calibration is None and not s.backend._startup_ready
    assert s.clock.now == 0  # A satisfied geometry condition needs no fixed sleep.


@pytest.mark.parametrize("change", ["bbox", "welcome", "disabled", "hwnd"])
def test_last_sample_cannot_bypass_commit_validation(startup, change):
    s = startup
    def audit(event, **data):
        s.events.append((event, data))
        if event == "startup_geometry_observed" and data["phase"] == "after_calibration" and data["samples"] == 3:
            if change == "bbox":
                s.main.box = (0, 0, 816, 639)
            elif change == "welcome":
                s.visible.append(s.welcome)
            elif change == "disabled":
                s.states[11]["enabled"] = False
            else:
                s.visible.clear()
    s.backend.audit = audit
    failure(s, "Window changed during calibration" if change == "bbox" else "welcome close" if change == "welcome" else "startup readiness")
    assert s.backend.calibration is None and s.backend.pending_calibration is None
    assert len(s.calibrations) == 1
    assert not any(e == "calibration_committed" for e, _ in s.events)
