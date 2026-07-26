"""
sara.tools.system.folders
Open well-known Windows shell folders (Downloads, This PC, Explorer, ...).
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
# FOLDERS & SHELL
# ============================================================


def _open_user_folder(folder_name: str) -> str:
    _ensure_windows()
    path = os.path.join(os.path.expanduser("~"), folder_name)
    try:
        os.startfile(path)
        return f"Opening your {folder_name} folder."
    except Exception as e:
        logger.error(f"_open_user_folder failed for '{folder_name}': {e}")
        return f"Sorry, I couldn't open the {folder_name} folder right now."


def open_downloads() -> str:
    return _open_user_folder("Downloads")


def open_documents() -> str:
    return _open_user_folder("Documents")


def open_desktop_folder() -> str:
    return _open_user_folder("Desktop")


def open_pictures() -> str:
    return _open_user_folder("Pictures")


def open_music() -> str:
    return _open_user_folder("Music")


def open_videos() -> str:
    return _open_user_folder("Videos")


def open_this_pc() -> str:
    _ensure_windows()
    try:
        subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"])
        return "Opening This PC."
    except Exception as e:
        logger.error(f"open_this_pc failed: {e}")
        return "Sorry, I couldn't open This PC right now."


def open_recycle_bin() -> str:
    _ensure_windows()
    try:
        subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"])
        return "Opening the Recycle Bin."
    except Exception as e:
        logger.error(f"open_recycle_bin failed: {e}")
        return "Sorry, I couldn't open the Recycle Bin right now."


def open_file_explorer() -> str:
    _ensure_windows()
    try:
        subprocess.Popen("explorer.exe")
        return "Opening File Explorer."
    except Exception as e:
        logger.error(f"open_file_explorer failed: {e}")
        return "Sorry, I couldn't open File Explorer right now."


def open_control_panel() -> str:
    _ensure_windows()
    try:
        subprocess.Popen("control.exe")
        return "Opening Control Panel."
    except Exception as e:
        logger.error(f"open_control_panel failed: {e}")
        return "Sorry, I couldn't open Control Panel right now."


def open_task_manager() -> str:
    _ensure_windows()
    try:
        subprocess.Popen("taskmgr.exe")
        return "Opening Task Manager."
    except Exception as e:
        logger.error(f"open_task_manager failed: {e}")
        return "Sorry, I couldn't open Task Manager right now."
