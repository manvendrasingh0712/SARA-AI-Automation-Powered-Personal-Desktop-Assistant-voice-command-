"""
sara.tools.system.window_control
Handle-based window control: find a window by app name and switch to it,
move/resize it to a preset screen position, toggle always-on-top, or
toggle fullscreen.

This is deliberately a separate module from window_mgmt.py: window_mgmt.py
only ever acts on whatever window currently has focus, via keyboard-shortcut
simulation (windows+up, alt+tab, etc.) — it never needs to know which
window that is. Everything here targets a *named* application instead, which
needs real window-handle lookup (pywin32), a fundamentally different
technique. Keeping them apart keeps window_mgmt.py dependency-free for
anyone who doesn't need pywin32.
"""
from ._shared import _ensure_windows, _send_keys

import logging
import platform
import subprocess
import time

import psutil

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

_PYWIN32_HINT = "Window control needs the 'pywin32' package. Run: pip install pywin32"

_POSITION_PRESETS = {
    "left half": (0.0, 0.0, 0.5, 1.0),
    "right half": (0.5, 0.0, 0.5, 1.0),
    "top half": (0.0, 0.0, 1.0, 0.5),
    "bottom half": (0.0, 0.5, 1.0, 0.5),
    "top left": (0.0, 0.0, 0.5, 0.5),
    "top right": (0.5, 0.0, 0.5, 0.5),
    "bottom left": (0.0, 0.5, 0.5, 0.5),
    "bottom right": (0.5, 0.5, 0.5, 0.5),
    "center": (0.25, 0.25, 0.5, 0.5),
    "full screen": (0.0, 0.0, 1.0, 1.0),
}


def _enum_visible_windows():
    import win32gui

    windows = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            windows.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return windows


def _find_window(app_name: str):
    import win32gui
    import win32process

    from .apps import _APP_ALIASES

    name_lower = app_name.strip().lower()
    target_process = _APP_ALIASES.get(name_lower, name_lower)
    target_exe = (
        target_process.lower()
        if target_process.lower().endswith(".exe")
        else target_process.lower() + ".exe"
    )

    title_matches = []
    process_matches = []
    for hwnd in _enum_visible_windows():
        title = win32gui.GetWindowText(hwnd)
        if name_lower in title.lower():
            title_matches.append(hwnd)
            continue
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            if proc.name().lower() == target_exe:
                process_matches.append(hwnd)
        except Exception:
            continue

    if title_matches:
        return title_matches[0]
    if process_matches:
        return process_matches[0]
    return None


def switch_to_application(app_name: str) -> str:
    _ensure_windows()
    if not app_name or not app_name.strip():
        return "No application name was provided."

    try:
        import win32gui
        import win32con
    except ImportError:
        return _PYWIN32_HINT

    hwnd = _find_window(app_name)
    if not hwnd:
        return f"I couldn't find an open window for '{app_name}'."

    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return f"Switched to {app_name}."
    except Exception as e:
        logger.error(f"switch_to_application failed for '{app_name}': {e}")
        return (
            f"I found {app_name}'s window but couldn't bring it to the "
            f"front — Windows sometimes blocks this if Sara isn't the "
            f"currently focused app."
        )


def move_window(app_name: str, position: str) -> str:
    _ensure_windows()
    if not app_name or not app_name.strip():
        return "No application name was provided."

    position_key = (position or "").strip().lower()
    preset = _POSITION_PRESETS.get(position_key)
    if not preset:
        valid = ", ".join(_POSITION_PRESETS.keys())
        return f"I don't know the position '{position}'. Try one of: {valid}."

    try:
        import win32gui
        import win32con
        import win32api
    except ImportError:
        return _PYWIN32_HINT

    hwnd = _find_window(app_name)
    if not hwnd:
        return f"I couldn't find an open window for '{app_name}'."

    try:
        screen_w = win32api.GetSystemMetrics(0)
        screen_h = win32api.GetSystemMetrics(1)
        fx, fy, fw, fh = preset
        x, y = int(fx * screen_w), int(fy * screen_h)
        w, h = int(fw * screen_w), int(fh * screen_h)

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        win32gui.SetForegroundWindow(hwnd)
        return f"Moved {app_name} to {position_key}."
    except Exception as e:
        logger.error(f"move_window failed for '{app_name}' -> '{position}': {e}")
        return f"Sorry, I couldn't move '{app_name}' right now."


def toggle_always_on_top(app_name: str) -> str:
    _ensure_windows()
    if not app_name or not app_name.strip():
        return "No application name was provided."

    try:
        import win32gui
        import win32con
    except ImportError:
        return _PYWIN32_HINT

    hwnd = _find_window(app_name)
    if not hwnd:
        return f"I couldn't find an open window for '{app_name}'."

    try:
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        is_topmost = bool(ex_style & win32con.WS_EX_TOPMOST)
        flag = win32con.HWND_NOTOPMOST if is_topmost else win32con.HWND_TOPMOST
        win32gui.SetWindowPos(
            hwnd, flag, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
        )
        return f"{'Disabled' if is_topmost else 'Enabled'} always-on-top for {app_name}."
    except Exception as e:
        logger.error(f"toggle_always_on_top failed for '{app_name}': {e}")
        return f"Sorry, I couldn't change always-on-top for '{app_name}' right now."


def toggle_fullscreen(app_name: str = "") -> str:
    _ensure_windows()

    app_name = (app_name or "").strip()
    if app_name:
        switch_result = switch_to_application(app_name)
        if switch_result.startswith("I couldn't find") or switch_result.startswith(
            (_PYWIN32_HINT, "Sorry")
        ):
            return switch_result
        time.sleep(0.15)

    error = _send_keys("f11")
    if error:
        return error
    return f"Toggled fullscreen{f' for {app_name}' if app_name else ''}."