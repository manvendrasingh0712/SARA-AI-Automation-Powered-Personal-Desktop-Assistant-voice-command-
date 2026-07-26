"""
sara.tools.system.media_keys
OS-level media-key shortcuts (play/pause/next/prev/stop).
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
# MEDIA CONTROLS
# ============================================================


def play_pause_media() -> str:
    error = _send_keys("play/pause media")
    return error or "Toggled play and pause."


def next_track() -> str:
    error = _send_keys("next track")
    return error or "Skipped to the next track."


def previous_track() -> str:
    error = _send_keys("previous track")
    return error or "Went back to the previous track."


def stop_media() -> str:
    error = _send_keys("stop media")
    return error or "Stopped media playback."
