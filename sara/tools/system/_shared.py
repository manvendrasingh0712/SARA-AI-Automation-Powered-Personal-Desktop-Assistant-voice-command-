"""
sara.tools.system._shared
Low-level shared helpers (platform guard, key-combo sender) used by
almost every other function in this package.
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


def _ensure_windows() -> None:
    """Raises a clear error if a Windows-only function is called on another OS."""
    if not _IS_WINDOWS:
        raise RuntimeError(
            "This action is only supported on Windows. "
            f"Detected OS: {platform.system()}"
        )


def _send_keys(combo: str) -> Optional[str]:
    """
    Sends a key combination (or media key name) via the 'keyboard'
    package.

    Returns:
        None on success, or a human-readable error message on failure.
    """
    try:
        import keyboard

        keyboard.send(combo)
        return None
    except ImportError:
        return "Window/media control requires the 'keyboard' package. Run: pip install keyboard"
    except Exception as e:
        logger.error(f"_send_keys failed for combo '{combo}': {e}")
        return "Sorry, I couldn't send that key command right now."
