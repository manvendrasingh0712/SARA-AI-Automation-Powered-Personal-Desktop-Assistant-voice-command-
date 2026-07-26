"""
sara.tools.system.timers
Voice-triggered countdown timers.
"""

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
# TIMER
# ============================================================

# Module-level timer reference so it can be cancelled
_active_timer: Optional[threading.Timer] = None
_active_timer_lock = threading.Lock()


def set_timer(
    seconds: int, label: str = "timer", callback: Optional[Callable[[str], None]] = None
) -> str:
    """
    Starts a countdown timer. When it fires, calls callback(message)
    if provided, otherwise prints to console.

    Args:
        seconds: Duration in seconds.
        label: Human-readable label (e.g. "5 minute timer").
        callback: Optional function to call when timer fires. Receives
                  the reminder message string. In gui_main.py this
                  should be wired to _speak() / ui_update().

    Returns:
        Confirmation message.
    """
    global _active_timer

    if seconds <= 0:
        return "Timer duration must be greater than zero."

    def _fire():
        msg = f"Your {label} is done!"
        if callback:
            try:
                callback(msg)
            except Exception as e:
                print(f"[Timer] Callback error: {e}")
        else:
            print(f"\n[Timer] {msg}")

    with _active_timer_lock:
        if _active_timer and _active_timer.is_alive():
            _active_timer.cancel()
        _active_timer = threading.Timer(seconds, _fire)
        _active_timer.daemon = True
        _active_timer.start()

    # Format the confirmation message
    if seconds < 60:
        duration_str = f"{seconds} second{'s' if seconds != 1 else ''}"
    elif seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        duration_str = f"{mins} minute{'s' if mins != 1 else ''}"
        if secs:
            duration_str += f" and {secs} second{'s' if secs != 1 else ''}"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        duration_str = f"{hours} hour{'s' if hours != 1 else ''}"
        if mins:
            duration_str += f" and {mins} minute{'s' if mins != 1 else ''}"

    return f"Timer set for {duration_str}. I'll let you know when it's done."


def cancel_timer() -> str:
    """Cancels the currently active timer, if any."""
    global _active_timer
    with _active_timer_lock:
        if _active_timer and _active_timer.is_alive():
            _active_timer.cancel()
            _active_timer = None
            return "Timer cancelled."
    return "No active timer to cancel."
