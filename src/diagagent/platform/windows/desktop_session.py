"""Centralized Windows interactive desktop session management and capability guards."""

import ctypes
import os
import threading
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from diagagent.environment.errors import DesktopError

# Win32 Constants
UOI_NAME = 2
GENERIC_ALL = 0x10000000
DESKTOP_READOBJECTS = 0x0001
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_SWITCHDESKTOP = 0x0100


def get_virtual_screen_bbox():
    """Return signed physical desktop bounds, not primary-monitor size."""
    def query():
        ensure_interactive_desktop()
        api = ctypes.windll.user32
        left, top = api.GetSystemMetrics(76), api.GetSystemMetrics(77)
        width, height = api.GetSystemMetrics(78), api.GetSystemMetrics(79)
        if width <= 0 or height <= 0:
            raise DesktopError("Invalid virtual desktop geometry", "BLOCKED_ENVIRONMENT")
        return (left, top, left + width, top + height)
    return run_on_default_desktop(query)


class DesktopInspection(BaseModel):
    """Detailed diagnostic snapshot of current Windows process desktop attachment."""

    session_id: int = 0
    window_station: str = ""
    thread_desktop: str = ""
    is_interactive: bool = False
    is_default_desktop: bool = False
    is_winsta0: bool = False
    foreground_hwnd: int = 0
    foreground_title: str = ""
    foreground_bbox: List[int] = Field(default_factory=list)
    error_code: int = 0
    error_message: str = ""


def enable_dpi_awareness():
    """Require physical pixels on this thread, regardless of import order.

    PyAutoGUI can lock the process to SYSTEM_AWARE before we are imported.
    A process-wide setter then returns E_ACCESSDENIED (not a Python exception).
    Use and verify a thread override instead; never continue in virtualized units.
    """
    if os.name != "nt":
        return
    try:
        api = _dpi_user32()
    except (AttributeError, OSError) as error:
        raise DesktopError("Physical desktop DPI API unavailable", "BLOCKED_ENVIRONMENT") from error
    if not api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)):
        raise DesktopError("Cannot establish physical desktop DPI context", "BLOCKED_ENVIRONMENT")
    context = api.GetThreadDpiAwarenessContext()
    if not api.AreDpiAwarenessContextsEqual(context, ctypes.c_void_p(-4)):
        raise DesktopError("Physical desktop DPI context verification failed", "BLOCKED_ENVIRONMENT")


def _dpi_user32():
    api = ctypes.WinDLL("user32", use_last_error=True)
    for name, args, result in [
        ("SetThreadDpiAwarenessContext", [ctypes.c_void_p], ctypes.c_void_p),
        ("GetThreadDpiAwarenessContext", [], ctypes.c_void_p),
        ("AreDpiAwarenessContextsEqual", [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
    ]:
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def get_session_id() -> int:
    """Returns the Windows Session ID of the current process."""
    if os.name != "nt":
        return 0
    sess_id = ctypes.c_uint()
    if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sess_id)):
        return int(sess_id.value)
    return 0


def get_window_station() -> str:
    """Returns the name of the current process WindowStation."""
    if os.name != "nt":
        return ""
    winsta = ctypes.windll.user32.GetProcessWindowStation()
    if not winsta:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.c_uint()
    if ctypes.windll.user32.GetUserObjectInformationW(winsta, UOI_NAME, buf, 256, ctypes.byref(needed)):
        return buf.value
    return ""


def get_thread_desktop() -> str:
    """Returns the name of the Desktop associated with the current thread."""
    if os.name != "nt":
        return ""
    tid = ctypes.windll.kernel32.GetCurrentThreadId()
    desk = ctypes.windll.user32.GetThreadDesktop(tid)
    if not desk:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.c_uint()
    if ctypes.windll.user32.GetUserObjectInformationW(desk, UOI_NAME, buf, 256, ctypes.byref(needed)):
        return buf.value
    return ""


def is_interactive() -> bool:
    """Checks whether the current process is in an interactive WindowStation (WinSta0)."""
    return get_window_station().lower() == "winsta0"


def get_foreground_window() -> Dict[str, Any]:
    """Retrieves current foreground window details."""
    if os.name != "nt":
        return {"hwnd": 0, "title": "", "bbox": [0, 0, 0, 0], "is_visible": False}
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "title": "", "bbox": [0, 0, 0, 0], "is_visible": False}

    title_buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, title_buf, 512)
    rect = (ctypes.c_long * 4)()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    is_visible = bool(ctypes.windll.user32.IsWindowVisible(hwnd))

    return {
        "hwnd": int(hwnd),
        "title": title_buf.value,
        "bbox": [int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])],
        "is_visible": is_visible,
    }


def ensure_interactive_desktop() -> bool:
    """
    Ensures the current thread is attached to the interactive Windows 'Default' desktop.
    Fails safely with BLOCKED_ENVIRONMENT and logs Win32 error code if unable.
    """
    if os.name != "nt":
        raise DesktopError("Platform is not Windows NT", "BLOCKED_ENVIRONMENT")

    enable_dpi_awareness()

    station = get_window_station()
    if station.lower() != "winsta0":
        raise DesktopError(
            f"BLOCKED_ENVIRONMENT: Process WindowStation is not interactive (found '{station}'). "
            "DiagAgent must be run from an interactive Windows desktop session.",
            "BLOCKED_ENVIRONMENT",
        )

    cur_desktop = get_thread_desktop()
    if cur_desktop.lower() == "default":
        return True

    # Try attaching to Default desktop
    hdesk = ctypes.windll.user32.OpenDesktopW("Default", 0, False, GENERIC_ALL)
    if not hdesk:
        win_err = ctypes.GetLastError()
        raise DesktopError(
            f"BLOCKED_ENVIRONMENT: OpenDesktopW('Default') failed with Win32 error {win_err}. "
            "Ensure the session is unlocked and has interactive desktop access.",
            "BLOCKED_ENVIRONMENT",
        )

    ret = ctypes.windll.user32.SetThreadDesktop(hdesk)
    if not ret:
        win_err = ctypes.GetLastError()
        raise DesktopError(
            f"BLOCKED_ENVIRONMENT: SetThreadDesktop to 'Default' failed with Win32 error {win_err}. "
            "The calling thread may have already created desktop-bound UI resources.",
            "BLOCKED_ENVIRONMENT",
        )

    new_desktop = get_thread_desktop()
    if new_desktop.lower() != "default":
        raise DesktopError(
            f"BLOCKED_ENVIRONMENT: Verification failed: thread desktop is '{new_desktop}', expected 'Default'.",
            "BLOCKED_ENVIRONMENT",
        )

    return True


def run_on_default_desktop(func: Callable[..., Any], *args, **kwargs) -> Any:
    """
    Executes a callable guaranteed to be running on the interactive 'Default' desktop.
    If current thread cannot switch, dispatches to a clean dedicated worker thread.
    """
    if os.name != "nt":
        return func(*args, **kwargs)

    cur_desktop = get_thread_desktop()
    if cur_desktop.lower() == "default":
        ensure_interactive_desktop()
        return func(*args, **kwargs)

    # Try switching in current thread first
    try:
        ensure_interactive_desktop()
    except DesktopError:
        # If thread-switch failed on this thread, execute in a fresh clean worker thread
        result_holder: List[Any] = []
        exception_holder: List[Exception] = []

        def worker():
            try:
                ensure_interactive_desktop()
                res = func(*args, **kwargs)
                result_holder.append(res)
            except Exception as e:
                exception_holder.append(e)

        thread = threading.Thread(target=worker, name="DiagAgentDesktopWorker")
        thread.start()
        thread.join()

        if exception_holder:
            raise exception_holder[0]
        return result_holder[0] if result_holder else None
    # Operation errors must never trigger a second capture or input dispatch.
    return func(*args, **kwargs)


def inspect_desktop() -> DesktopInspection:
    """Performs a comprehensive inspection of the current desktop session."""
    if os.name != "nt":
        return DesktopInspection(
            is_interactive=False,
            error_message="Non-Windows operating system",
        )

    sess_id = get_session_id()
    station = get_window_station()
    desktop = get_thread_desktop()
    is_winsta = station.lower() == "winsta0"
    is_def = desktop.lower() == "default"

    fg = get_foreground_window()
    return DesktopInspection(
        session_id=sess_id,
        window_station=station,
        thread_desktop=desktop,
        is_interactive=is_winsta and is_def,
        is_default_desktop=is_def,
        is_winsta0=is_winsta,
        foreground_hwnd=fg["hwnd"],
        foreground_title=fg["title"],
        foreground_bbox=fg["bbox"],
    )


class _GUIThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                *[(name, ctypes.c_void_p) for name in
                  ("hwndActive", "hwndFocus", "hwndCapture", "hwndMenuOwner",
                   "hwndMoveSize", "hwndCaret")],
                ("rcCaret", ctypes.c_long * 4)]


def _readiness_user32():
    # Private bindings keep pointer-sized HWND/HANDLE values intact on 64-bit Windows.
    api = ctypes.WinDLL("user32", use_last_error=True)
    signatures = {
        "IsWindow": ([ctypes.c_void_p], ctypes.c_int),
        "IsWindowVisible": ([ctypes.c_void_p], ctypes.c_int),
        "IsWindowEnabled": ([ctypes.c_void_p], ctypes.c_int),
        "GetForegroundWindow": ([], ctypes.c_void_p),
        "GetAncestor": ([ctypes.c_void_p, ctypes.c_uint], ctypes.c_void_p),
        "GetWindowThreadProcessId": ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)], ctypes.c_ulong),
        "GetGUIThreadInfo": ([ctypes.c_ulong, ctypes.POINTER(_GUIThreadInfo)], ctypes.c_int),
        "WaitForInputIdle": ([ctypes.c_void_p, ctypes.c_ulong], ctypes.c_ulong),
        "SendMessageTimeoutW": ([ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                  ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_uint,
                                  ctypes.POINTER(ctypes.c_size_t)], ctypes.c_ssize_t),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def inspect_window_readiness(hwnd: int, timeout_ms: int = 100) -> Dict[str, Any]:
    """Probe the actual target queue; process input-idle alone is not readiness."""
    def probe():
        ensure_interactive_desktop()
        api = _readiness_user32()
        state = {"hwnd": hwnd, "valid": bool(api.IsWindow(hwnd)),
                 "visible": False, "enabled": False, "input_queue": False,
                 "responsive": False, "foreground": int(api.GetForegroundWindow() or 0),
                 "root": 0, "pid": 0, "error_code": 0}
        if not state["valid"]:
            return state
        state.update(visible=bool(api.IsWindowVisible(hwnd)),
                     enabled=bool(api.IsWindowEnabled(hwnd)),
                     root=int(api.GetAncestor(hwnd, 2) or 0))  # GA_ROOT
        pid = ctypes.c_ulong()
        tid = api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        state["pid"] = int(pid.value)
        info = _GUIThreadInfo()
        info.cbSize = ctypes.sizeof(info)
        state["input_queue"] = bool(tid and api.GetGUIThreadInfo(tid, ctypes.byref(info)))
        if state["input_queue"] and state["visible"] and state["enabled"]:
            result = ctypes.c_size_t()
            ctypes.set_last_error(0)
            # WM_NULL; bounded, abort on hang/exit, no synthetic user input.
            state["responsive"] = bool(api.SendMessageTimeoutW(
                hwnd, 0, 0, 0, 0x0001 | 0x0002 | 0x0020, max(1, timeout_ms), ctypes.byref(result)))
            state["error_code"] = ctypes.get_last_error()
        state["foreground"] = int(api.GetForegroundWindow() or 0)
        return state
    return run_on_default_desktop(probe)


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_ulong),
        ("dwY", ctypes.c_ulong),
        ("dwXSize", ctypes.c_ulong),
        ("dwYSize", ctypes.c_ulong),
        ("dwXCountChars", ctypes.c_ulong),
        ("dwYCountChars", ctypes.c_ulong),
        ("dwFillAttribute", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("wShowWindow", ctypes.c_ushort),
        ("cbReserved2", ctypes.c_ushort),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_ulong),
        ("dwThreadId", ctypes.c_ulong),
    ]


class DesktopProcess:
    """Wrapper around a Win32 process created explicitly on an interactive desktop."""

    def __init__(self, h_process: int, h_thread: int, pid: int, tid: int):
        self._h_process = h_process
        self._h_thread = h_thread
        self.pid = pid
        self.tid = tid
        self.returncode: Optional[int] = None

    def input_idle(self) -> bool:
        """Nonblocking startup signal, always combined with a target-window probe."""
        result = _readiness_user32().WaitForInputIdle(self._h_process, 0)
        if result == 0xFFFFFFFF:
            raise DesktopError(f"WaitForInputIdle failed: Win32 error {ctypes.get_last_error()}",
                               "Launch / Window Error")
        return result == 0

    def poll(self) -> Optional[int]:
        if self.returncode is not None:
            return self.returncode
        if not self._h_process:
            return 0
        code = ctypes.c_ulong()
        STILL_ACTIVE = 259
        if ctypes.windll.kernel32.GetExitCodeProcess(self._h_process, ctypes.byref(code)):
            if code.value != STILL_ACTIVE:
                self.returncode = int(code.value)
                return self.returncode
        return None

    def wait(self, timeout: Optional[float] = None) -> int:
        if self.returncode is not None:
            return self.returncode
        ms = int(timeout * 1000) if timeout is not None else 0xFFFFFFFF
        ctypes.windll.kernel32.WaitForSingleObject(self._h_process, ms)
        self.poll()
        return self.returncode or 0

    def terminate(self):
        if self._h_process and self.poll() is None:
            ctypes.windll.kernel32.TerminateProcess(self._h_process, 0)
            self.poll()

    def close(self):
        if self._h_thread:
            ctypes.windll.kernel32.CloseHandle(self._h_thread)
            self._h_thread = None
        if self._h_process:
            ctypes.windll.kernel32.CloseHandle(self._h_process)
            self._h_process = None


def launch_process_on_desktop(
    cmd: Any,
    desktop_name: str = r"WinSta0\Default",
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> DesktopProcess:
    """Spawns a process explicitly assigned to the designated interactive desktop."""
    if os.name != "nt":
        raise DesktopError("launch_process_on_desktop is only supported on Windows")

    if isinstance(cmd, list):
        formatted_args = []
        for arg in cmd:
            s_arg = str(arg)
            if " " in s_arg and not (s_arg.startswith('"') and s_arg.endswith('"')):
                formatted_args.append(f'"{s_arg}"')
            else:
                formatted_args.append(s_arg)
        cmd_str = " ".join(formatted_args)
    else:
        cmd_str = str(cmd)

    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(_STARTUPINFOW)
    si.lpDesktop = desktop_name

    pi = _PROCESS_INFORMATION()

    creation_flags = 0
    env_buf = None
    if env is not None:
        creation_flags |= 0x00000400  # CREATE_UNICODE_ENVIRONMENT
        env_str = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0"
        env_buf = ctypes.create_unicode_buffer(env_str, len(env_str))

    success = ctypes.windll.kernel32.CreateProcessW(
        None,
        cmd_str,
        None,
        None,
        False,
        creation_flags,
        env_buf,
        str(cwd) if cwd else None,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not success:
        err = ctypes.GetLastError()
        raise DesktopError(f"Failed to launch process on desktop '{desktop_name}': Win32 error {err}")

    return DesktopProcess(
        h_process=pi.hProcess,
        h_thread=pi.hThread,
        pid=int(pi.dwProcessId),
        tid=int(pi.dwThreadId),
    )
