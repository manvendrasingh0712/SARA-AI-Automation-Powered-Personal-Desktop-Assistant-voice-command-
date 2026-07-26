"""
sara.tools.system.window_mgmt
Active-window / desktop management (minimize, maximize, snap, switch).
"""
from ._shared import _ensure_windows, _send_keys

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
# WINDOW MANAGEMENT
# ============================================================


def show_desktop() -> str:
    error = _send_keys("windows+d")
    return error or "Toggled the desktop view."


def minimize_all_windows() -> str:
    error = _send_keys("windows+m")
    return error or "Minimized all windows."


def restore_windows() -> str:
    error = _send_keys("windows+shift+m")
    return error or "Restored your minimized windows."


def maximize_active_window() -> str:
    error = _send_keys("windows+up")
    return error or "Maximized the active window."


def minimize_active_window() -> str:
    error = _send_keys("windows+down")
    return error or "Minimized the active window."


def close_active_window() -> str:
    error = _send_keys("alt+f4")
    return error or "Closed the active window."


def snap_window_left() -> str:
    error = _send_keys("windows+left")
    return error or "Snapped the window to the left."


def snap_window_right() -> str:
    error = _send_keys("windows+right")
    return error or "Snapped the window to the right."


def switch_window() -> str:
    error = _send_keys("alt+tab")
    return error or "Switched to the next window."
