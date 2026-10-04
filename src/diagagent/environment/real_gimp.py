"""Owned Windows GIMP 3 session. Edits and exports go through the GUI."""
import ctypes
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import time
from PIL import Image
from diagagent.config import DesktopConfig
from diagagent.environment.errors import DesktopError


class RealGIMPBackend:
    name = "real_gimp"
    # Consecutive successful probes, not a startup sleep or repeated resize.
    _geometry_samples = 3

    def __init__(self, gimp_executable=None, config=None):
        self.config = config or DesktopConfig()
        self.gimp_executable = gimp_executable or self.config.gimp_executable or self._find_gimp_executable()
        self.process = None
        self.window = None
        self.run_dir = None
        self.window_bbox = [0, 0, *self.config.window_size]
        self.window_size = self.config.window_size
        self.active_tool = None
        self.active_dialog = None
        self.dialog_confirmed = None
        self.image_loaded = False
        self.input_file = None
        self.gui_execute = self._default_gui_execute
        self.audit = lambda *args, **kwargs: None
        self.deadline = None
        self._log = None
        self._startup_ready = False
        self.lifecycle_state = "STARTING"
        self.pending_calibration = None
        self.calibration = None
        self.desktop_geometry = None
        self._action_before_size = None
        self._action_before_tool = None

    @staticmethod
    def _window_rect(window):
        # box reads one Win32 rectangle through PyGetWindow.
        from diagagent.platform.windows.desktop_session import run_on_default_desktop
        left, top, width, height = run_on_default_desktop(lambda: tuple(window.box))
        return [left, top, left + width, top + height]

    def _invalidate_desktop_geometry(self, reason):
        self.screenshot_bbox = None
        self.screenshot_metadata = None
        self._invalidate_calibration(reason)
        self._set_lifecycle("FAILED")

    def _set_lifecycle(self, state):
        if self.lifecycle_state != state:
            previous = self.lifecycle_state
            self.lifecycle_state = state
            self.audit("startup_lifecycle", previous=previous, lifecycle_state=state)

    def _invalidate_calibration(self, reason, actual=None):
        previous = self.calibration or self.pending_calibration
        self.pending_calibration = None
        self.calibration = None
        self._startup_ready = False
        if previous is not None:
            self.audit("calibration_invalidated", reason=reason, previous=previous,
                       actual_bbox=actual, lifecycle_state=self.lifecycle_state)

    def _assert_calibration(self):
        if self.desktop_geometry is not None:
            self.desktop_geometry.check("window_calibration")
        actual = self._window_rect(self.window) if self.window is not None else None
        if (self.calibration is None or self.window is None
                or self.window._hWnd != self.calibration["hwnd"]):
            self._invalidate_calibration("main HWND identity changed", actual)
            self._set_lifecycle("FAILED")
            raise DesktopError("Main HWND has no valid committed calibration")
        if actual != self.window_bbox or actual != self.calibration["bbox"]:
            self._invalidate_calibration("window moved since calibration", actual)
            self._set_lifecycle("FAILED")
            raise DesktopError(f"Window moved since calibration: {actual}")

    def _default_gui_execute(self, primitive):
        import pyautogui
        from diagagent.platform.windows.desktop_session import ensure_interactive_desktop, run_on_default_desktop
        ensure_interactive_desktop()
        act_type = primitive.get("type") if isinstance(primitive, dict) else getattr(primitive, "type", None)
        if act_type == "click":
            x = primitive.get("x") if isinstance(primitive, dict) else primitive.x
            y = primitive.get("y") if isinstance(primitive, dict) else primitive.y
            run_on_default_desktop(lambda: pyautogui.click(x=x, y=y))
        elif act_type == "hotkey":
            keys = primitive.get("keys") if isinstance(primitive, dict) else primitive.keys
            if isinstance(keys, str):
                keys = [keys]
            run_on_default_desktop(lambda: pyautogui.hotkey(*keys))
        elif act_type == "type":
            text = primitive.get("text") if isinstance(primitive, dict) else primitive.text
            run_on_default_desktop(lambda: pyautogui.typewrite(str(text)))
        elif act_type == "wait":
            seconds = primitive.get("seconds", 0.5) if isinstance(primitive, dict) else getattr(primitive, "seconds", 0.5)
            time.sleep(seconds)

    @staticmethod
    def _find_gimp_executable():
        for path in [r"C:\Program Files\GIMP 3\bin\gimp-3.exe", r"C:\Program Files\GIMP 3\bin\gimp-3.2.exe", r"C:\Program Files\GIMP 3\bin\gimp.exe"]:
            if Path(path).is_file():
                return path
        raise DesktopError("Configure gimp_executable: this profile supports GIMP 3.2")

    def preflight(self):
        from diagagent.platform.windows.desktop_session import ensure_interactive_desktop, enable_dpi_awareness
        from diagagent.platform.windows.desktop_geometry import DesktopGeometryContract
        from diagagent.observation.screenshot import ScreenshotService
        enable_dpi_awareness()
        ensure_interactive_desktop()
        import pyautogui
        import pyperclip
        if not Path(self.gimp_executable).is_file():
            raise DesktopError(f"GIMP executable missing: {self.gimp_executable}")
        console_bin = Path(self.gimp_executable).with_name("gimp-console.exe")
        probe_bin = str(console_bin) if console_bin.is_file() else self.gimp_executable
        proc = subprocess.run([probe_bin, "--version"], capture_output=True, timeout=10, text=True)
        version = (proc.stdout or proc.stderr).strip()
        if "3.2." not in version:
            raise DesktopError(f"Unsupported GIMP version for calibrated profile: {version}")
        screen = list(pyautogui.size())
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi != self.config.expected_dpi:
            raise DesktopError(f"DPI mismatch: actual={dpi}, expected={self.config.expected_dpi}")
        if self.config.expected_screen_size and screen != list(self.config.expected_screen_size):
            raise DesktopError(f"Screen size mismatch: {screen}")
        if any(a > b for a, b in zip(self.window_size, screen)):
            raise DesktopError(f"Configured window {self.window_size} exceeds screen {screen}")
        self.desktop_geometry = DesktopGeometryContract(audit=self.audit,
            invalidate=self._invalidate_desktop_geometry)
        _, meta = ScreenshotService(all_screens=True,
            geometry_contract=self.desktop_geometry).capture()
        desktop_bbox = list(self.desktop_geometry.calibrated.bbox)
        self.audit("desktop_geometry", coordinate_space="virtual_desktop", desktop_bbox=desktop_bbox,
                   primary_screen_size=screen, dpi=dpi)
        return {"status": "PASS", "gimp_version": version, "screen_size": screen,
                "coordinate_space": "virtual_desktop", "virtual_desktop_bbox": desktop_bbox,
                "desktop_geometry": self.desktop_geometry.calibrated.to_dict(),
                "capture_geometry": meta.model_dump(),
                "dpi": dpi, "profile_id": self.config.profile_id, "capture": meta.backend,
                "language": self.config.language, "window_size": list(self.window_size)}

    def windows(self):
        from diagagent.platform.windows.desktop_session import ensure_interactive_desktop, run_on_default_desktop
        import pygetwindow as gw
        import psutil
        if self.process is None:
            return []

        def _get_wins():
            ensure_interactive_desktop()
            try:
                owned_pids = {self.process.pid, *(child.pid for child in psutil.Process(self.process.pid).children(recursive=True))}
            except psutil.NoSuchProcess:
                return []
            result = []
            for window in gw.getAllWindows():
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(window._hWnd, ctypes.byref(pid))
                if (pid.value in owned_pids and window.title and window.width > 0
                        and ctypes.windll.user32.IsWindowVisible(window._hWnd)):
                    result.append(window)
            return result

        return run_on_default_desktop(_get_wins) or []

    def wait_for(self, predicate, description, timeout=None, *, end=None,
                 error_type="Dialog Operation Error"):
        end = min(end if end is not None else float("inf"),
                  time.monotonic() + (timeout if timeout is not None else self.config.gui_timeout))
        if self.deadline:
            end = min(end, self.deadline)
        while time.monotonic() < end:
            if self.process and self.process.poll() is not None:
                raise DesktopError(f"Owned GIMP exited: {self.process.returncode}",
                                   "Launch / Window Error" if error_type == "Launch / Window Error" else "Environment Error")
            value = predicate()
            if value and time.monotonic() < end:
                return value
            time.sleep(min(0.15, max(0, end - time.monotonic())))
        raise DesktopError(f"Timed out waiting for {description}", error_type)

    def launch(self):
        if self.process:
            if self._startup_ready and self.process.poll() is None:
                self._assert_calibration()
                return
            raise DesktopError("Owned GIMP has not completed startup", "Launch / Window Error")
        if self.run_dir is None:
            raise DesktopError("Set run directory before launching GIMP")
        self.preflight_result = self.preflight()
        # Persist successful preflight even if opening the image later fails.
        import json
        (self.run_dir / "desktop_preflight.json").write_text(
            json.dumps(self.preflight_result, indent=2), encoding="utf-8")
        profile = self.run_dir / "gimp_profile"
        profile.mkdir(parents=True, exist_ok=False)
        (profile / "gimprc").write_text('(show-welcome-dialog no)\n(image-title-format "%D*%f [%wx%h] - GIMP")\n', encoding="utf-8")
        (profile / "shortcutsrc").write_text('# DiagAgent owned profile\n(file-version 1)\n(action "image-scale" "<Primary><Alt>s")\n', encoding="utf-8")
        env = os.environ.copy()
        env["GIMP3_DIRECTORY"] = str(profile)
        env["LANGUAGE"] = self.config.language
        env["LANG"] = self.config.language + ".UTF-8"
        from diagagent.platform.windows.desktop_session import launch_process_on_desktop
        self.process = launch_process_on_desktop(
            [self.gimp_executable, "--new-instance", "--no-splash"],
            env=env,
        )
        self._wait_startup_ready()

    @staticmethod
    def _is_welcome(window):
        return window.title.startswith("Welcome") or "欢迎" in window.title

    def _wait_startup_ready(self):
        from diagagent.platform.windows.desktop_session import inspect_window_readiness
        end = min(time.monotonic() + self.config.startup_timeout, self.deadline or float("inf"))
        requested = {}
        dismissed = False
        closing = None
        calibration_attempt = None
        last_state = {}
        self._invalidate_calibration("startup begins")
        self._set_lifecycle("STARTING")

        def invalidate(reason, actual=None):
            self._invalidate_calibration(reason, actual)

        def observe_geometry(target, bbox, phase):
            previous = self.pending_calibration
            if previous and (previous["hwnd"] != target._hWnd or previous["bbox"] != bbox
                             or previous["phase"] != phase):
                invalidate("startup identity or geometry changed", bbox)
            if self.pending_calibration is None:
                self.pending_calibration = {"hwnd": target._hWnd, "bbox": list(bbox),
                                            "phase": phase, "samples": 0}
            self.pending_calibration["samples"] += 1
            self.audit("startup_geometry_observed", **self.pending_calibration,
                       lifecycle_state=self.lifecycle_state)
            return self.pending_calibration["samples"] >= self._geometry_samples

        def ready():
            nonlocal closing, calibration_attempt, dismissed
            owned = self.windows()
            idle = self.process.input_idle()
            last_state.update(process_input_idle=idle,
                              windows=[{"hwnd": w._hWnd, "title": w.title} for w in owned])
            for hwnd, focus_end in requested.items():
                if time.monotonic() >= focus_end:
                    last_state["expired_focus_hwnd"] = hwnd
                    raise DesktopError(f"Timed out waiting for focus on owned GIMP window: HWND={hwnd}",
                                       "Launch / Window Error")
            if closing:
                hwnd, title, close_end = closing
                if any(w._hWnd == hwnd for w in owned):
                    if time.monotonic() >= close_end:
                        raise DesktopError("Timed out waiting for startup welcome close", "Launch / Window Error")
                    return None
                self.audit("welcome_dismissed", hwnd=hwnd, title=title)
                dismissed = True
                closing = None
            welcome = next((w for w in owned if self._is_welcome(w)), None)
            main = next((w for w in owned if not self._is_welcome(w) and w.width > 700
                         and (w.title in {"GNU Image Manipulation Program", "GNU 图像处理程序", "GIMP"}
                              or re.search(r"\[\d+x\d+\] - GIMP$", w.title))), None)
            target = welcome or main
            if welcome:
                invalidate("startup welcome observed")
                self._set_lifecycle("WELCOME_PENDING")
            if not idle:
                invalidate("process not input idle")
                return None
            if any(hwnd not in {w._hWnd for w in owned} for hwnd in requested):
                raise DesktopError("GIMP focus target disappeared during startup", "Launch / Window Error")
            if target is None:
                invalidate("main window unavailable")
                return None
            if not welcome and self.pending_calibration is None:
                self._set_lifecycle("MAIN_CANDIDATE")
            if welcome and main and main._hWnd in requested:
                # The new modal redirects main-window activation; it is a different
                # startup state, not a repeated request against the blocked main.
                requested.pop(main._hWnd)
                self.audit("startup_focus_redirected", hwnd=welcome._hWnd)
            remaining_ms = max(1, min(100, int((end - time.monotonic()) * 1000)))
            state = inspect_window_readiness(target._hWnd, timeout_ms=remaining_ms)
            last_state.update(target=state, role="welcome" if welcome else "main",
                              probe_monotonic=time.monotonic())
            focus_end = requested.get(target._hWnd)
            if focus_end is not None and time.monotonic() >= focus_end:
                raise DesktopError(f"Timed out waiting for focus on owned GIMP window: {target.title}",
                                   "Launch / Window Error")
            if not all(state[key] for key in ("valid", "visible", "enabled", "input_queue", "responsive")):
                invalidate("window not interactive")
                return None
            if state["hwnd"] != target._hWnd or state["root"] != target._hWnd or time.monotonic() >= end:
                invalidate("window identity or startup deadline changed")
                return None
            if state["foreground"] != target._hWnd:
                invalidate("window not foreground")
                if focus_end is None:
                    requested[target._hWnd] = min(end, time.monotonic() + self.config.focus_timeout)
                    self._request_focus(target)
                return None
            requested.pop(target._hWnd, None)
            if welcome:
                if dismissed:
                    raise DesktopError("Startup welcome reappeared after dismissal", "Launch / Window Error")
                if closing is None:
                    click = {"type": "click", "x": target.right - 60, "y": target.bottom - 35,
                             "coordinate_space": "virtual_desktop"}
                    self.audit("startup_pointer_grounded", hwnd=target._hWnd, source="welcome_hwnd",
                               right=target.right, bottom=target.bottom, offset=[-60, -35], action=click)
                    self.gui_execute(click)
                    closing = (target._hWnd, target.title, min(end, time.monotonic() + self.config.gui_timeout))
                    self.audit("welcome_close_requested", hwnd=target._hWnd, title=target.title)
                return None
            # A fresh ownership/dialog check closes the enumeration-to-probe race.
            current = self.windows()
            if any(self._is_welcome(w) for w in current):
                invalidate("startup welcome observed")
                self._set_lifecycle("WELCOME_PENDING")
                return None
            if not any(w._hWnd == target._hWnd for w in current):
                invalidate("main HWND disappeared")
                raise DesktopError("Main GIMP window disappeared during readiness", "Launch / Window Error")
            # A fresh foreground/enabled probe plus no other owned startup window
            # prevents committing through a newly mapped modal dialog.
            if any(w._hWnd != target._hWnd for w in current):
                invalidate("startup modal or secondary window present")
                return None
            state = inspect_window_readiness(target._hWnd, timeout_ms=remaining_ms)
            last_state["target"] = state
            if (not all(state[key] for key in ("valid", "visible", "enabled", "input_queue", "responsive"))
                    or any(state[key] != target._hWnd for key in ("hwnd", "root", "foreground"))):
                invalidate("main readiness changed during validation")
                return None
            bbox = self._window_rect(target)
            if calibration_attempt is None:
                self._set_lifecycle("MAIN_INTERACTIVE")
                if not observe_geometry(target, bbox, "before_calibration"):
                    return None
                self.window = target
                self._set_lifecycle("CALIBRATING")
                calibrated_bbox = self.fix_window(self.window_size, startup_end=end)
                calibration_attempt = {"hwnd": target._hWnd, "bbox": calibrated_bbox}
                self.pending_calibration = {**calibration_attempt, "phase": "after_calibration", "samples": 0}
                # Never commit in the same iteration as move/resize.
                return None
            if target._hWnd != calibration_attempt["hwnd"] or bbox != calibration_attempt["bbox"]:
                invalidate("identity or geometry changed after calibration request", bbox)
                raise DesktopError(f"Window changed during calibration: expected={calibration_attempt}, actual={bbox}",
                                   "Launch / Window Error")
            self._set_lifecycle("CALIBRATING")
            if not observe_geometry(target, bbox, "after_calibration"):
                return None
            # Commit barrier: sampling/audit callbacks do not grant a permanent
            # lease on this HWND. Revalidate immediately before publishing READY.
            final_windows = self.windows()
            if any(self._is_welcome(w) for w in final_windows):
                invalidate("startup welcome observed")
                self._set_lifecycle("WELCOME_PENDING")
                return None
            if {w._hWnd for w in final_windows} != {target._hWnd}:
                invalidate("startup window set changed before commit")
                return None
            final_state = inspect_window_readiness(target._hWnd, timeout_ms=remaining_ms)
            last_state["target"] = final_state
            if (not all(final_state[key] for key in ("valid", "visible", "enabled", "input_queue", "responsive"))
                    or any(final_state[key] != target._hWnd for key in ("hwnd", "root", "foreground"))):
                invalidate("main readiness changed before commit")
                return None
            actual = self._window_rect(target)
            if actual != bbox:
                invalidate("geometry changed before commit", actual)
                raise DesktopError(f"Window changed during calibration: expected={bbox}, actual={actual}",
                                   "Launch / Window Error")
            if time.monotonic() >= end:
                return None
            return target

        try:
            self.window = self.wait_for(ready, "owned GIMP startup readiness", self.config.startup_timeout,
                                        end=end, error_type="Launch / Window Error")
        except DesktopError as error:
            invalidate("startup failed")
            self._set_lifecycle("FAILED")
            self.audit("startup_failed", error_type=error.error_type, error_message=str(error), readiness=last_state)
            raise
        self.window_bbox = list(self.pending_calibration["bbox"])
        if self.desktop_geometry is not None:
            self.desktop_geometry.check("window_calibration_commit")
        self.calibration = {"hwnd": self.window._hWnd, "bbox": list(self.window_bbox),
                            "timestamp": datetime.now(timezone.utc).isoformat(), "lifecycle_state": "READY"}
        self.pending_calibration = None
        if self.desktop_geometry is not None:
            self.calibration["desktop_geometry"] = self.desktop_geometry.calibrated.to_dict()
        self._set_lifecycle("READY")
        self._startup_ready = True
        self.audit("calibration_committed", **self.calibration)
        self.audit("startup_ready", readiness=last_state)

    def _request_focus(self, window):
        from diagagent.platform.windows.desktop_session import run_on_default_desktop
        def request():
            if window.isMinimized:
                window.restore()
            self.audit("focus_requested", title=window.title,
                       bbox=[window.left, window.top, window.right, window.bottom])
            if ctypes.windll.user32.GetForegroundWindow() == window._hWnd:
                return
            try:
                window.activate()
            except Exception as error:
                self.audit("focus_activation_error", hwnd=window._hWnd, error_message=str(error))
            if ctypes.windll.user32.GetForegroundWindow() != window._hWnd:
                self.gui_execute({"type": "click", "x": window.left + min(200, window.width // 2), "y": window.top + 12,
                                  "coordinate_space": "virtual_desktop"})
        run_on_default_desktop(request)

    def activate(self, window):
        from diagagent.platform.windows.desktop_session import run_on_default_desktop
        self._request_focus(window)
        self.wait_for(lambda: run_on_default_desktop(lambda: ctypes.windll.user32.GetForegroundWindow() == window._hWnd),
                      f"focus on owned GIMP window: {window.title}", timeout=self.config.focus_timeout)

    def fix_window(self, window_size, *, startup_end=None):
        self.window_size = tuple(window_size)
        if self.window is None:
            return
        if startup_end is None:
            # Post-startup geometry must never be silently repaired/rebaselined.
            self._assert_calibration()
            return
        if self.window.isMaximized or self.window.isMinimized:
            self.window.restore()
        self.audit("calibration_requested", hwnd=self.window._hWnd,
                   bbox=[0, 0, *self.window_size], lifecycle_state=self.lifecycle_state)
        self.window.moveTo(0, 0)
        self.window.resizeTo(*self.window_size)
        self.wait_for(lambda: self.window.left == 0 and self.window.top == 0
                      and abs(self.window.width - self.window_size[0]) <= 16
                      and abs(self.window.height - self.window_size[1]) <= 16,
                      "main window calibration", end=startup_end, error_type="Launch / Window Error")
        actual = self._window_rect(self.window)
        if (actual[:2] != [0, 0] or abs(actual[2] - self.window_size[0]) > 16
                or abs(actual[3] - self.window_size[1]) > 16):
            raise DesktopError(f"Actual window size does not match profile: {actual}")
        return actual

    def find_dialog(self, name):
        labels = {"Scale Image", "Export Image", "Open Image", "Export Image as PNG"}
        if name not in labels:
            raise DesktopError(f"Unsupported dialog: {name}", "UI Grounding Error")
        def find():
            for w in self.windows():
                if name.lower() in w.title.lower():
                    if name == "Export Image" and " as " in w.title:
                        continue
                    return w
        dialog = self.wait_for(find, name)
        self.activate(dialog)
        self.active_dialog = name
        self.audit("dialog_observed", title=dialog.title, bbox=[dialog.left, dialog.top, dialog.right, dialog.bottom])
        checkpoint = self.run_dir / "screenshots" / f"dialog_{time.time_ns()}.png"
        self.capture_screenshot(checkpoint)
        self.audit("dialog_screenshot", path=str(checkpoint.relative_to(self.run_dir)), title=dialog.title)
        return dialog

    def dialog_is_open(self, name=None):
        """Non-blocking dialog probe used only for downstream verification."""
        for window in self.windows():
            title = (window.title or "").lower()
            if name is None:
                if window._hWnd != getattr(self.window, "_hWnd", None):
                    return True
            elif name.lower() in title:
                return True
        return False

    def current_image_size(self):
        if self.window is None:
            return None
        match = re.search(r"\[(\d+)x(\d+)\]", self.window.title)
        return [int(x) for x in match.groups()] if match else None

    def open_image(self, input_file):
        path = Path(input_file).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        self.launch()
        self.activate(self.window)
        self.gui_execute({"type": "hotkey", "keys": ["ctrl", "o"]})
        self.find_dialog("Open Image")
        self.gui_execute({"type": "hotkey", "keys": ["ctrl", "l"]})
        self.gui_execute({"type": "type", "text": str(path), "clear_first": True})
        self.gui_execute({"type": "hotkey", "keys": "enter"})
        self.wait_for(lambda: path.stem in self.window.title and self.current_image_size(), "loaded input image")
        self.input_file = str(path)
        self.image_loaded = True
        self.active_dialog = None
        self.fix_window(self.window_size)
        self.audit("image_loaded", title=self.window.title, observed_size=self.current_image_size(), source="window_title")

    def capture_screenshot(self, path):
        from diagagent.platform.windows.desktop_session import ensure_interactive_desktop
        from diagagent.observation.screenshot import ScreenshotService
        if self.window is None or not self.windows():
            raise DesktopError("Owned GIMP window missing during screenshot")
        ensure_interactive_desktop()
        foreground = ctypes.windll.user32.GetForegroundWindow()
        if foreground not in [w._hWnd for w in self.windows()]:
            raise DesktopError("Foreground window is not owned by this run")
        self._assert_calibration()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        bounds = list(self.window_bbox)
        desktop_bbox = None
        if self.desktop_geometry is not None:
            desktop_bbox = tuple(self.desktop_geometry.calibrated.bbox)
        for window in self.windows():
            # GIMP can retain hidden auxiliary windows at stale negative
            # coordinates.  They must not expand the screenshot crop outside
            # the calibrated virtual desktop.  Visible dialogs that intersect
            # the desktop remain included.
            if desktop_bbox is not None and (
                window.right <= desktop_bbox[0] or window.left >= desktop_bbox[2]
                or window.bottom <= desktop_bbox[1] or window.top >= desktop_bbox[3]
            ):
                self.audit("screenshot_window_skipped", hwnd=window._hWnd,
                           title=window.title,
                           bbox=[window.left, window.top, window.right, window.bottom],
                           reason="outside_calibrated_desktop")
                continue
            bounds = [min(bounds[0], window.left), min(bounds[1], window.top),
                      max(bounds[2], window.right), max(bounds[3], window.bottom)]
        service = ScreenshotService(all_screens=True, geometry_contract=self.desktop_geometry)
        image, metadata = service.capture(crop_bbox=tuple(bounds))
        # Also reject movement during capture, before publishing any frame.
        self._assert_calibration()
        image.save(target)
        metadata.path = str(target.resolve())
        self.screenshot_bbox = list(metadata.screenshot_bbox)
        self.screenshot_metadata = metadata.model_dump()
        self.audit("screenshot_captured", metadata=self.screenshot_metadata)

    def verify_action(self, action):
        if action.type == "resize_image":
            expected = [action.width, action.height]
            actual = self.wait_for(lambda: self.current_image_size() == expected and expected, "resized image dimensions")
            try:
                dialog_closed = self.wait_for(lambda: not self.dialog_is_open(), "scale dialog close", timeout=self.config.gui_timeout)
            except DesktopError:
                dialog_closed = False
            return {"status": "PASS" if dialog_closed else "UNKNOWN", "source": "window_title",
                    "actual_size": actual, "title": self.window.title,
                    "dialog_closed": dialog_closed, "downstream_state_changed": True}
        if action.type == "export_file":
            path = Path(action.path)
            def decoded():
                try:
                    with Image.open(path) as img:
                        img.load()
                        return {"status": "PASS", "source": "exported_file_decode", "size": list(img.size), "format": img.format}
                except (OSError, ValueError):
                    return None
            result = self.wait_for(decoded, "GUI exported decodable artifact", error_type="File I/O Error")
            try:
                dialog_closed = self.wait_for(lambda: not self.dialog_is_open(), "export dialog close", timeout=self.config.gui_timeout)
            except DesktopError:
                dialog_closed = False
            result.update({"status": "PASS" if dialog_closed else "UNKNOWN",
                           "dialog_closed": dialog_closed, "downstream_state_changed": True})
            return result
        if action.type == "select_tool":
            # GIMP 3 does not expose a stable machine-readable active-tool
            # probe in this profile.  Keep this explicit instead of treating
            # keyboard dispatch as proof of tool selection.
            return {"status": "UNKNOWN", "source": "proxy_keyboard_dispatch",
                    "signal_kind": "proxy", "active_tool": self.active_tool,
                    "target_tool": action.tool_name, "state_changed": None,
                    "verification_note": "active tool is not machine-verifiable in the calibrated profile"}
        if action.type == "crop_canvas":
            before = self._action_before_size
            actual = self.current_image_size()
            if not before or not actual:
                return {"status": "UNKNOWN", "source": "window_title", "target_hit": None,
                        "state_changed": None, "layout_scope": "fixed"}
            region = getattr(action, "region", "center")
            expected = [round(before[0] * 0.5), round(before[1] * 0.5)] if region == "center" else None
            changed = actual != before
            dimension_match = expected is not None and actual == expected
            return {"status": "PASS" if dimension_match else "UNKNOWN", "source": "window_title",
                    "actual_size": actual, "expected_size": expected, "state_changed": changed,
                    "target_hit": None, "spatial_signal": "dimension_proxy",
                    "layout_scope": "fixed",
                    "verification_note": "crop location is not machine-verifiable; dimensions are a proxy"}
        return {"status": "UNKNOWN", "source": "no_semantic_effect_probe"}

    def close(self):
        self._invalidate_calibration("session closed")
        self._set_lifecycle("CLOSED")
        if self.process:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=5)
            finally:
                if hasattr(self.process, "close"):
                    self.process.close()
                self.process = None
        if self._log:
            try:
                self._log.close()
            except Exception:
                pass
            self._log = None
