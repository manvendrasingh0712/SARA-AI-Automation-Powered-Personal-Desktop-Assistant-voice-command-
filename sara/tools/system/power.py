"""
sara.tools.system.power
Power-state control: lock, sleep, hibernate, log off, shutdown, restart.
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
# POWER & SESSION
# ============================================================


def lock_pc() -> str:
    _ensure_windows()
    try:
        ctypes.windll.user32.LockWorkStation()
        return "Locking your PC now."
    except Exception as e:
        logger.error(f"lock_pc failed: {e}")
        return "Sorry, I couldn't lock the PC right now."


def sleep_system() -> str:
    _ensure_windows()
    try:
        ctypes.windll.powrprof.SetSuspendState(False, True, False)
        return "Putting your system to sleep."
    except Exception as e:
        logger.error(f"sleep_system failed: {e}")
        return "Sorry, I couldn't put the system to sleep right now."


def hibernate_system() -> str:
    _ensure_windows()
    try:
        ctypes.windll.powrprof.SetSuspendState(True, True, False)
        return "Hibernating your system."
    except Exception as e:
        logger.error(f"hibernate_system failed: {e}")
        return "Sorry, I couldn't hibernate the system right now."


def log_off() -> str:
    _ensure_windows()
    try:
        subprocess.run(["shutdown", "/l"], check=True, timeout=5)
        return "Logging off now."
    except subprocess.CalledProcessError as e:
        # FINAL PRODUCTION POLISH: previously `return f"...Error: {e}"`,
        # leaking raw subprocess exception text straight into what TTS
        # speaks aloud. Brought in line with the logger.error()-first
        # pattern used everywhere else in this file.
        logger.error(f"log_off failed: {e}")
        return "Sorry, I couldn't log off right now."
    except subprocess.TimeoutExpired:
        return "Sorry, that command took too long to respond."


def shutdown_system(delay_seconds: int = 10) -> str:
    _ensure_windows()
    try:
        subprocess.run(
            ["shutdown", "/s", "/t", str(delay_seconds)], check=True, timeout=5
        )
        return f"System will shut down in {delay_seconds} seconds. Say 'cancel shutdown' to abort."
    except subprocess.CalledProcessError as e:
        # FINAL PRODUCTION POLISH: same raw-exception-leak fix as log_off().
        logger.error(f"shutdown_system failed: {e}")
        return "Sorry, I couldn't schedule the shutdown right now."
    except subprocess.TimeoutExpired:
        return "Sorry, that command took too long to respond."


def restart_system(delay_seconds: int = 10) -> str:
    _ensure_windows()
    try:
        subprocess.run(
            ["shutdown", "/r", "/t", str(delay_seconds)], check=True, timeout=5
        )
        return f"System will restart in {delay_seconds} seconds. Say 'cancel shutdown' to abort."
    except subprocess.CalledProcessError as e:
        # FINAL PRODUCTION POLISH: same raw-exception-leak fix as log_off().
        logger.error(f"restart_system failed: {e}")
        return "Sorry, I couldn't schedule the restart right now."
    except subprocess.TimeoutExpired:
        return "Sorry, that command took too long to respond."


def cancel_shutdown() -> str:
    _ensure_windows()
    try:
        subprocess.run(["shutdown", "/a"], check=True, timeout=5)
        return "Scheduled shutdown/restart has been cancelled."
    except subprocess.CalledProcessError:
        return "No scheduled shutdown or restart was found to cancel."
    except subprocess.TimeoutExpired:
        return "Sorry, that command took too long to respond."
