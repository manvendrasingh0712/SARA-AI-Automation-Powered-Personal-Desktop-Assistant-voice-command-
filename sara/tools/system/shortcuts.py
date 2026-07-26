"""
sara.tools.system.shortcuts
Generic keyboard shortcuts: typing, clipboard, browser/tab, zoom, scroll.
"""
from typing import Dict

from ._shared import _ensure_windows, _send_keys

# module scope, matching the _APP_ALIASES pattern above — this table is
# static and was previously being rebuilt on every single press_key()
# call for no reason.
_KEY_ALIASES: Dict[str, str] = {
    "enter": "enter",
    "return": "enter",
    "escape": "esc",
    "esc": "esc",
    "space": "space",
    "spacebar": "space",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "tab": "tab",
    "up": "up",
    "up arrow": "up",
    "down": "down",
    "down arrow": "down",
    "left": "left",
    "left arrow": "left",
    "right": "right",
    "right arrow": "right",
    "home": "home",
    "end": "end",
    "page up": "page up",
    "page down": "page down",
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
    "print screen": "print screen",
    "caps lock": "caps lock",
    "num lock": "num lock",
    "scroll lock": "scroll lock",
    "windows": "windows",
    "win": "windows",
}

import ctypes
import logging
import os
import re
import socket
import subprocess
import platform
import threading
import time

from datetime import datetime
from typing import Callable, Dict, Optional

import psutil

from config import Config

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# PRODUCTION-AUDIT FIX: previously computed as os.path.join(os.getcwd(),
# "sara_notes.txt"), which meant launching the app from a different
# working directory could silently point at a different physical file
# than the one database.py/reminders.py use. Now resolved from a single,
# CWD-independent, project-root-based path defined once in config.py.
_NOTES_FILE = Config.NOTES_FILE_PATH

# FINAL PRODUCTION POLISH: single canonical definition, used by
# get_notes() below to parse each "[timestamp] text" line back out of
# sara_notes.txt. Previously this regex existed in two places — one
# unreachable/dead copy mis-indented inside clear_notes(), and one real
# copy declared AFTER get_notes() (which only worked because Python
# resolves names inside a function body at call time, not definition
# time — fragile and confusing to read). Consolidated here, declared
# before anything references it.
_NOTE_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s?(?P<text>.*)$")




# ============================================================
# KEYBOARD & TYPING
# ============================================================


def type_text(text: str) -> str:
    """Types the given text at the current cursor position."""
    if not text or not text.strip():
        return "Nothing to type."
    try:
        import keyboard

        keyboard.write(text, delay=0.02)
        return f"Typed: {text}"
    except ImportError:
        return "Typing requires the 'keyboard' package. Run: pip install keyboard"
    except Exception as e:
        logger.error(f"type_text failed: {e}")
        return "Sorry, I couldn't type that text right now."


def press_key(key: str) -> str:
    """Presses a single key or key combination."""
    if not key or not key.strip():
        return "No key specified."

    # FINAL PRODUCTION POLISH: _KEY_ALIASES is now the module-level
    # constant defined near _APP_ALIASES above, instead of being
    # rebuilt from scratch on every call.
    normalized = _KEY_ALIASES.get(key.strip().lower(), key.strip().lower())
    error = _send_keys(normalized)
    return error or f"Pressed {key}."


def copy_selection() -> str:
    error = _send_keys("ctrl+c")
    return error or "Copied selection to clipboard."


def paste_clipboard() -> str:
    error = _send_keys("ctrl+v")
    return error or "Pasted from clipboard."


def select_all() -> str:
    error = _send_keys("ctrl+a")
    return error or "Selected all."


def undo() -> str:
    error = _send_keys("ctrl+z")
    return error or "Undone."


def redo() -> str:
    error = _send_keys("ctrl+y")
    return error or "Redone."


# ============================================================
# BROWSER TAB CONTROLS
# ============================================================


def new_tab() -> str:
    error = _send_keys("ctrl+t")
    return error or "Opened a new tab."


def close_tab() -> str:
    error = _send_keys("ctrl+w")
    return error or "Closed the current tab."


def next_tab() -> str:
    error = _send_keys("ctrl+tab")
    return error or "Switched to the next tab."


def prev_tab() -> str:
    error = _send_keys("ctrl+shift+tab")
    return error or "Switched to the previous tab."


def reload_page() -> str:
    error = _send_keys("f5")
    return error or "Page refreshed."


def zoom_in() -> str:
    error = _send_keys("ctrl+=")
    return error or "Zoomed in."


def zoom_out() -> str:
    error = _send_keys("ctrl+-")
    return error or "Zoomed out."


def zoom_reset() -> str:
    error = _send_keys("ctrl+0")
    return error or "Zoom reset to default."


def scroll_up() -> str:
    error = _send_keys("page up")
    return error or "Scrolled up."


def scroll_down() -> str:
    error = _send_keys("page down")
    return error or "Scrolled down."


def scroll_top() -> str:
    error = _send_keys("ctrl+home")
    return error or "Jumped to the top."


def scroll_bottom() -> str:
    error = _send_keys("ctrl+end")
    return error or "Jumped to the bottom."
