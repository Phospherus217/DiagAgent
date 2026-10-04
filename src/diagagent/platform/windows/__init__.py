"""Windows platform support modules."""

from diagagent.platform.windows.desktop_session import (
    DesktopInspection,
    ensure_interactive_desktop,
    get_foreground_window,
    get_session_id,
    get_thread_desktop,
    get_window_station,
    inspect_desktop,
    is_interactive,
    run_on_default_desktop,
)

__all__ = [
    "DesktopInspection",
    "ensure_interactive_desktop",
    "get_foreground_window",
    "get_session_id",
    "get_thread_desktop",
    "get_window_station",
    "inspect_desktop",
    "is_interactive",
    "run_on_default_desktop",
]
