"""Real desktop GUI executor powered by PyAutoGUI."""

import time
from typing import Optional

from diagagent.actions.schema import (
    BaseAction,
    ClickAction,
    DragAction,
    HotkeyAction,
    TypeAction,
    WaitAction,
)
from diagagent.executor.atomic import normalize_coordinates, wait_seconds
from diagagent.executor.base import BaseExecutor
from diagagent.executor.models import ExecutionResult

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
    HAS_PYAUTOGUI = True
except ImportError:
    pyautogui = None
    HAS_PYAUTOGUI = False


class GUIExecutor(BaseExecutor):
    """Executes atomic actions on real desktop windows."""

    def __init__(self, step_delay: float = 0.2):
        self.step_delay = step_delay
        self.geometry_check = None
        if not HAS_PYAUTOGUI:
            # We don't crash on init so that imports work anywhere, but execute will notify
            pass

    def execute(self, action: BaseAction) -> ExecutionResult:
        from diagagent.platform.windows.desktop_session import run_on_default_desktop
        from diagagent.environment.errors import DesktopError
        try:
            return run_on_default_desktop(self._execute_on_desktop, action)
        except DesktopError as error:
            return ExecutionResult(success=False, state_changed=False, action_executable=False,
                                   error_type=error.error_type, error_message=str(error))

    def _execute_on_desktop(self, action: BaseAction) -> ExecutionResult:
        if not HAS_PYAUTOGUI:
            return ExecutionResult(
                success=False,
                state_changed=False,
                action_executable=False,
                error_type="Environment Error",
                error_message="pyautogui is not installed or GUI display is not available.",
            )

        import os
        if os.name == "nt":
            from diagagent.platform.windows.desktop_session import ensure_interactive_desktop
            ensure_interactive_desktop()
        if self.geometry_check is not None:
            self.geometry_check()

        start_time = time.time()
        try:
            if action.type in {"click", "drag"} and getattr(action, "coordinate_space", None) != "virtual_desktop":
                return ExecutionResult(success=False, state_changed=False, action_executable=False,
                    error_type="UI Grounding Error", error_message="GUIExecutor requires virtual_desktop coordinates")
            if isinstance(action, ClickAction) or action.type == "click":
                x = getattr(action, "x", None) or (action.to_dict().get("x") if hasattr(action, "to_dict") else 0)
                y = getattr(action, "y", None) or (action.to_dict().get("y") if hasattr(action, "to_dict") else 0)
                button = getattr(action, "button", "left")
                clicks = getattr(action, "clicks", 1)
                ix, iy = normalize_coordinates(x, y)
                pyautogui.click(x=ix, y=iy, button=button, clicks=clicks)

            elif isinstance(action, DragAction) or action.type == "drag":
                start = getattr(action, "start", (0, 0))
                end = getattr(action, "end", (0, 0))
                duration = getattr(action, "duration", 0.5)
                sx, sy = normalize_coordinates(start[0], start[1])
                ex, ey = normalize_coordinates(end[0], end[1])
                pyautogui.moveTo(sx, sy)
                pyautogui.dragTo(ex, ey, duration=duration, button="left")

            elif isinstance(action, TypeAction) or action.type == "type":
                text = getattr(action, "text", "")
                import pyperclip
                previous = pyperclip.paste()
                try:
                    pyperclip.copy(str(text))
                    if getattr(action, "clear_first", False):
                        pyautogui.hotkey("ctrl", "a")
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.2)
                finally:
                    pyperclip.copy(previous)

            elif isinstance(action, HotkeyAction) or action.type == "hotkey":
                keys = getattr(action, "keys", "")
                if isinstance(keys, str):
                    pyautogui.press(keys)
                elif isinstance(keys, list):
                    pyautogui.hotkey(*keys)

            elif isinstance(action, WaitAction) or action.type == "wait":
                dur = getattr(action, "duration", 0.5)
                wait_seconds(dur)
                duration_ms = (time.time() - start_time) * 1000.0
                return ExecutionResult(
                    success=True,
                    state_changed=False,
                    action_executable=True,
                    duration_ms=duration_ms,
                )

            elif action.type == "stop":
                return ExecutionResult(
                    success=True,
                    state_changed=False,
                    action_executable=True,
                    duration_ms=0.0,
                )

            else:
                # Unhandled atomic action
                return ExecutionResult(
                    success=False,
                    state_changed=False,
                    action_executable=False,
                    error_type="UI Grounding Error",
                    error_message=f"Action '{action.type}' cannot be executed directly by GUIExecutor without lowering.",
                )

            if self.step_delay > 0:
                wait_seconds(self.step_delay)

            duration_ms = (time.time() - start_time) * 1000.0
            return ExecutionResult(
                success=True,
                state_changed=None,
                dispatched=True,
                action_executable=True,
                duration_ms=duration_ms,
            )

        except Exception as err:
            duration_ms = (time.time() - start_time) * 1000.0
            return ExecutionResult(
                success=False,
                state_changed=False,
                action_executable=False,
                error_type="Execution Error",
                error_message=f"Desktop GUI execution failed: {err}",
                duration_ms=duration_ms,
            )
