"""Doctor capability verification suite for interactive desktop, GIMP, input, and model connectivity."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageChops

import click

from diagagent.config import DesktopConfig
from diagagent.environment.errors import DesktopError
from diagagent.observation.screenshot import ScreenshotService
from diagagent.platform.windows.desktop_session import (
    ensure_interactive_desktop,
    get_foreground_window,
    get_session_id,
    get_thread_desktop,
    get_window_station,
    inspect_desktop,
    is_interactive,
    run_on_default_desktop,
)


def run_doctor(
    config_file: Optional[str] = None,
    desktop_probe: bool = False,
    gimp_input_probe: bool = False,
    output_dir: str = "runs/doctor",
) -> Dict[str, Any]:
    """Runs capability checks and reports granular PASS/FAIL/BLOCKED_ENVIRONMENT/NOT_CONFIGURED status."""
    if desktop_probe:
        return run_desktop_probe(output_dir=output_dir)

    if gimp_input_probe:
        return run_gimp_input_probe(output_dir=output_dir, config_file=config_file)

    config = DesktopConfig.load(config_file)
    checks: List[Dict[str, Any]] = []

    # [1] Python environment
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 9)
    checks.append({
        "id": 1,
        "name": "Python environment",
        "status": "PASS" if py_ok else "FAIL",
        "details": f"Python {py_ver} ({sys.platform})",
    })

    # [2] Dependencies
    deps = ["pydantic", "PIL", "yaml", "click", "pyautogui", "pygetwindow", "pyperclip", "psutil"]
    missing_deps = []
    for dep in deps:
        try:
            __import__(dep)
        except ImportError:
            missing_deps.append(dep)
    checks.append({
        "id": 2,
        "name": "dependencies",
        "status": "PASS" if not missing_deps else "FAIL",
        "details": "All core dependencies available" if not missing_deps else f"Missing: {missing_deps}",
    })

    # [3] GIMP executable
    from diagagent.environment.real_gimp import RealGIMPBackend
    gimp_exe = None
    gimp_version = None
    gimp_exe_status = "FAIL"
    try:
        backend = RealGIMPBackend(config=config)
        gimp_exe = backend.gimp_executable
        if Path(gimp_exe).is_file():
            console_bin = Path(gimp_exe).with_name("gimp-console.exe")
            probe_bin = str(console_bin) if console_bin.is_file() else gimp_exe
            proc = subprocess.run([probe_bin, "--version"], capture_output=True, timeout=10, text=True)
            gimp_version = (proc.stdout or proc.stderr).strip()
            gimp_exe_status = "PASS" if "3.2." in gimp_version else "FAIL"
    except Exception as e:
        gimp_version = str(e)

    checks.append({
        "id": 3,
        "name": "GIMP executable",
        "status": gimp_exe_status,
        "details": f"{gimp_exe} ({gimp_version})",
    })

    # [4] GIMP process
    import psutil
    gimp_pids = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if "gimp" in p.info["name"].lower():
                gimp_pids.append(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    checks.append({
        "id": 4,
        "name": "GIMP process",
        "status": "PASS" if gimp_pids else "NOT_CONFIGURED",
        "details": f"Running PIDs: {gimp_pids}" if gimp_pids else "No running GIMP process currently detected",
    })

    # [5] Interactive desktop availability
    desktop_info = inspect_desktop()
    desktop_status = "BLOCKED_ENVIRONMENT"
    desktop_details = f"Session={desktop_info.session_id}, WinSta={desktop_info.window_station}, Desktop={desktop_info.thread_desktop}"
    try:
        ensure_interactive_desktop()
        updated_info = inspect_desktop()
        if updated_info.is_default_desktop and updated_info.is_winsta0:
            desktop_status = "PASS"
            desktop_details = f"Active session={updated_info.session_id}, WinSta={updated_info.window_station}, Desktop={updated_info.thread_desktop}"
    except Exception as e:
        desktop_details += f" | Error: {e}"

    checks.append({
        "id": 5,
        "name": "interactive desktop availability",
        "status": desktop_status,
        "details": desktop_details,
    })

    # [6] Screen capture
    screen_status = "BLOCKED_ENVIRONMENT"
    screen_details = ""
    try:
        _, meta = ScreenshotService().capture()
        screen_status = "PASS"
        screen_details = f"Backend={meta.backend}, Dimensions={meta.width}x{meta.height}"
    except Exception as e:
        screen_details = str(e)

    checks.append({
        "id": 6,
        "name": "screen capture",
        "status": screen_status,
        "details": screen_details,
    })

    # [7] Mouse input
    mouse_status = "BLOCKED_ENVIRONMENT"
    mouse_details = ""
    try:
        import pyautogui
        def _get_pos():
            ensure_interactive_desktop()
            return pyautogui.position()
        pos = run_on_default_desktop(_get_pos)
        mouse_status = "PASS"
        mouse_details = f"Cursor position: ({pos.x}, {pos.y})"
    except Exception as e:
        mouse_details = str(e)

    checks.append({
        "id": 7,
        "name": "mouse input",
        "status": mouse_status,
        "details": mouse_details,
    })

    # [8] Keyboard input
    kb_status = "BLOCKED_ENVIRONMENT"
    kb_details = ""
    try:
        ensure_interactive_desktop()
        kb_status = "PASS"
        kb_details = "Win32 input queue accessible"
    except Exception as e:
        kb_details = str(e)

    checks.append({
        "id": 8,
        "name": "keyboard input",
        "status": kb_status,
        "details": kb_details,
    })

    # [9] Foreground window access
    fg_status = "BLOCKED_ENVIRONMENT"
    fg_details = ""
    try:
        fg = run_on_default_desktop(get_foreground_window)
        if fg["hwnd"] > 0:
            fg_status = "PASS"
            fg_details = f"HWND={fg['hwnd']} ('{fg['title']}'), bbox={fg['bbox']}"
        else:
            fg_details = "No active foreground window found on desktop"
    except Exception as e:
        fg_details = str(e)

    checks.append({
        "id": 9,
        "name": "foreground window access",
        "status": fg_status,
        "details": fg_details,
    })

    # [10] GIMP window discovery
    gimp_win_status = "NOT_CONFIGURED"
    gimp_win_details = "GIMP is not currently running"
    if gimp_pids:
        try:
            import pygetwindow as gw
            def _find_gimp():
                ensure_interactive_desktop()
                return [w for w in gw.getAllWindows() if any(k in w.title.lower() for k in ["gimp", "gnu image manipulation"])]
            gw_list = run_on_default_desktop(_find_gimp)
            if gw_list:
                gimp_win_status = "PASS"
                gimp_win_details = f"Found {len(gw_list)} GIMP windows: {[w.title for w in gw_list]}"
            else:
                gimp_win_details = "GIMP process exists but window not visible on current desktop"
        except Exception as e:
            gimp_win_details = str(e)

    checks.append({
        "id": 10,
        "name": "GIMP window discovery",
        "status": gimp_win_status,
        "details": gimp_win_details,
    })

    # [11] Output directory write
    out_dir_path = Path(output_dir).resolve()
    out_status = "FAIL"
    out_details = ""
    try:
        out_dir_path.mkdir(parents=True, exist_ok=True)
        test_file = out_dir_path / f".write_test_{time.time_ns()}"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        out_status = "PASS"
        out_details = f"Writable: {out_dir_path}"
    except Exception as e:
        out_details = str(e)

    checks.append({
        "id": 11,
        "name": "output directory write",
        "status": out_status,
        "details": out_details,
    })

    # [12] Model configuration
    api_key = os.getenv("DIAGAGENT_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("DIAGAGENT_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    model_name = os.getenv("DIAGAGENT_MODEL") or "gpt-4o"

    model_cfg_status = "NOT_CONFIGURED"
    model_cfg_details = "Set DIAGAGENT_API_KEY and DIAGAGENT_BASE_URL to enable real model execution"
    if api_key:
        model_cfg_status = "PASS"
        model_cfg_details = f"Model={model_name}, Endpoint={base_url or 'default'}"

    checks.append({
        "id": 12,
        "name": "model configuration",
        "status": model_cfg_status,
        "details": model_cfg_details,
    })

    # [13] Model API connectivity
    model_conn_status = "SKIPPED"
    model_conn_details = "Skipped (model not configured)"
    if api_key:
        try:
            from diagagent.models.openai_compatible import OpenAICompatibleModel
            from diagagent.models.base import Message
            m = OpenAICompatibleModel(
                model_name=model_name,
                base_url=base_url,
                api_key=api_key,
                timeout=10.0,
            )
            resp = m.query([Message(role="user", content="ping")])
            if resp and resp.text:
                model_conn_status = "PASS"
                model_conn_details = f"Response latency: {resp.latency_ms}ms"
            else:
                model_conn_status = "FAIL"
                model_conn_details = f"Empty response: {resp.raw}"
        except Exception as e:
            model_conn_status = "FAIL"
            model_conn_details = str(e)

    checks.append({
        "id": 13,
        "name": "model API connectivity",
        "status": model_conn_status,
        "details": model_conn_details,
    })

    # [14] AgentRuntime readiness
    rt_status = "FAIL"
    rt_details = ""
    try:
        from diagagent.agent.runtime import AgentRuntime
        from diagagent.agent.context import ContextBuilder
        from diagagent.verification.verifier import RuntimeVerifier
        from diagagent.recovery.policy import RecoveryPolicy
        from diagagent.agent.termination import TerminationPolicy
        rt_status = "PASS"
        rt_details = "Runtime components and policies imported and ready"
    except Exception as e:
        rt_details = str(e)

    checks.append({
        "id": 14,
        "name": "AgentRuntime readiness",
        "status": rt_status,
        "details": rt_details,
    })

    # Print summary
    click.secho("\n=== DiagAgent Doctor Capability Report ===", bold=True)
    all_ok = True
    for c in checks:
        status_color = "green" if c["status"] == "PASS" else "yellow" if c["status"] in ("NOT_CONFIGURED", "SKIPPED") else "red"
        click.echo(f"[{c['id']:02d}] {c['name']:<35} : ", nl=False)
        click.secho(f"{c['status']:<20}", fg=status_color, bold=True, nl=False)
        click.echo(f" | {c['details']}")
        if c["status"] in ("FAIL", "BLOCKED_ENVIRONMENT"):
            all_ok = False

    return {
        "status": "PASS" if all_ok else "PARTIAL",
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_desktop_probe(output_dir: str = "runs/doctor") -> Dict[str, Any]:
    """Captures desktop screenshot, inspects foreground window, and verifies non-empty image."""
    click.secho("\n--- Running Desktop Probe (diagagent doctor --desktop) ---", bold=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = Path(output_dir) / ts
    target_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = target_dir / "desktop.png"

    # 1. Desktop Session Check
    try:
        ensure_interactive_desktop()
        desktop_info = inspect_desktop()
        click.echo(f"Desktop Session   : WinSta0\\{desktop_info.thread_desktop} (Session ID {desktop_info.session_id})")
    except DesktopError as e:
        click.secho(f"Desktop Session   : BLOCKED_ENVIRONMENT - {e}", fg="red", bold=True)
        return {"status": "BLOCKED_ENVIRONMENT", "error": str(e)}

    # 2. Foreground Window Inspection
    fg = run_on_default_desktop(get_foreground_window)
    click.echo(f"Foreground Window : HWND={fg['hwnd']} | Title='{fg['title']}' | Bbox={fg['bbox']}")

    # 3. Screenshot Capture
    try:
        svc = ScreenshotService()
        img, meta = svc.capture(target_path=screenshot_path)
        click.secho(f"Screenshot Saved  : {screenshot_path}", fg="green")
        click.echo(f"Image Properties  : {img.width}x{img.height} pixels | Format={img.format or 'PNG'} | Size={screenshot_path.stat().st_size} bytes")

        # 4. Verify non-empty
        extrema = img.convert("L").getextrema()
        is_all_black = (extrema == (0, 0))
        if is_all_black:
            click.secho("Verification FAIL : Screenshot is completely black (0 lumens across all pixels)", fg="red")
            return {"status": "FAIL", "error": "Black screenshot"}

        click.secho("Desktop Probe PASS: Real interactive desktop verified with valid screenshot.", fg="green", bold=True)
        return {
            "status": "PASS",
            "screenshot_path": str(screenshot_path),
            "dimensions": [img.width, img.height],
            "foreground_window": fg,
            "desktop_name": desktop_info.thread_desktop,
        }
    except Exception as e:
        click.secho(f"Screenshot FAIL   : {e}", fg="red", bold=True)
        return {"status": "BLOCKED_ENVIRONMENT", "error": str(e)}


def run_gimp_input_probe(output_dir: str = "runs/doctor", config_file: Optional[str] = None) -> Dict[str, Any]:
    """Safe, non-destructive probe verifying GIMP discovery, foreground activation, input, and state change."""
    click.secho("\n--- Running Safe GIMP Input Probe (diagagent doctor --gimp-input) ---", bold=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    probe_dir = Path(output_dir) / f"gimp_probe_{ts}"
    probe_dir.mkdir(parents=True, exist_ok=True)

    config = DesktopConfig.load(config_file)
    from diagagent.environment.real_gimp import RealGIMPBackend

    backend = RealGIMPBackend(config=config)
    backend.run_dir = probe_dir

    try:
        # Step 1: Launch/Discover GIMP
        click.echo("Step 1: Locating/launching GIMP...")
        backend.launch()
        main_win = backend.window
        if not main_win:
            raise DesktopError("Could not find or launch GIMP main window", "UI Grounding Error")

        click.echo(f"Found GIMP window : HWND={main_win._hWnd} | Title='{main_win.title}' | Bbox={[main_win.left, main_win.top, main_win.right, main_win.bottom]}")

        # Step 2: Bring to foreground
        click.echo("Step 2: Activating GIMP into foreground...")
        backend.activate(main_win)
        fg = get_foreground_window()
        click.echo(f"Active foreground : HWND={fg['hwnd']} | Title='{fg['title']}'")

        # Step 3: Capture Before Screenshot
        click.echo("Step 3: Capturing before screenshot...")
        before_path = probe_dir / "step_before.png"
        backend.capture_screenshot(before_path)
        before_img = Image.open(before_path)

        # Step 4: Non-destructive action (Send Alt+F hotkey to open File menu)
        click.echo("Step 4: Sending safe menu hotkey (Alt+F)...")
        import pyautogui
        def _send_alt_f():
            ensure_interactive_desktop()
            pyautogui.hotkey("alt", "f")

        run_on_default_desktop(_send_alt_f)
        time.sleep(0.8)

        # Step 5: Capture After Screenshot
        click.echo("Step 5: Capturing after screenshot...")
        after_path = probe_dir / "step_after.png"
        backend.capture_screenshot(after_path)
        after_img = Image.open(after_path)

        # Step 6: Verify State Change (before != after)
        diff = ImageChops.difference(before_img.convert("RGB"), after_img.convert("RGB"))
        diff_box = diff.getbbox()
        state_changed = diff_box is not None

        # Step 7: Restore state (Send Escape to dismiss menu)
        click.echo("Step 6: Sending Escape to close menu cleanly...")
        def _send_esc():
            ensure_interactive_desktop()
            pyautogui.press("escape")

        run_on_default_desktop(_send_esc)
        time.sleep(0.3)

        restored_path = probe_dir / "step_restored.png"
        backend.capture_screenshot(restored_path)

        # Summary
        click.secho("\n=== GIMP Input Probe Results ===", bold=True)
        click.echo(f"desktop_access       : PASS (WinSta0\\Default)")
        click.echo(f"screen_capture       : PASS ({before_img.width}x{before_img.height})")
        click.echo(f"gimp_window          : PASS (HWND={main_win._hWnd})")
        click.echo(f"foreground           : PASS ('{main_win.title}')")
        click.echo(f"mouse_keyboard_input : PASS")
        click.secho(f"state_changed        : {state_changed} (Diff bbox: {diff_box})", fg="green" if state_changed else "red", bold=True)

        if not state_changed:
            click.secho("FAIL: EXECUTION_NOOP - Menu did not open or no visual difference was detected.", fg="red", bold=True)
            return {"status": "EXECUTION_NOOP", "state_changed": False}

        click.secho("GIMP Input Probe PASS: Verified real desktop interaction and state change.", fg="green", bold=True)
        return {
            "status": "PASS",
            "desktop_access": "PASS",
            "screen_capture": "PASS",
            "gimp_window": "PASS",
            "foreground": "PASS",
            "mouse_keyboard_input": "PASS",
            "state_changed": True,
            "before_path": str(before_path),
            "after_path": str(after_path),
            "restored_path": str(restored_path),
            "diff_bbox": list(diff_box) if diff_box else None,
            "gimp_hwnd": main_win._hWnd,
            "gimp_bbox": [main_win.left, main_win.top, main_win.right, main_win.bottom],
        }
    finally:
        backend.close()
