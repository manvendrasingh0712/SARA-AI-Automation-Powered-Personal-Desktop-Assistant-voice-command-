"""
sara.tools.system.settings_pages
Deep-link into specific Windows Settings pages (ms-settings: URIs).
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
# WINDOWS SETTINGS PAGES
# ============================================================

_SETTINGS_PAGES: Dict[str, str] = {
    "display": "display",
    "sound": "sound",
    "bluetooth": "bluetooth",
    "network": "network",
    "update": "windowsupdate",
    "apps": "appsfeatures",
    "personalization": "personalization",
    "privacy": "privacy",
    "storage": "storagesense",
    "power": "powersleep",
    "about": "about",
}


def _open_settings_page(page_key: str, spoken_label: str) -> str:
    _ensure_windows()
    uri_suffix = _SETTINGS_PAGES.get(page_key, "")
    try:
        os.startfile(f"ms-settings:{uri_suffix}")
        return f"Opening {spoken_label} settings."
    except Exception as e:
        logger.error(f"_open_settings_page failed for '{spoken_label}': {e}")
        return f"Sorry, I couldn't open {spoken_label} settings right now."


def open_display_settings() -> str:
    return _open_settings_page("display", "display")


def open_sound_settings() -> str:
    return _open_settings_page("sound", "sound")


def open_bluetooth_settings() -> str:
    return _open_settings_page("bluetooth", "Bluetooth")


def open_network_settings() -> str:
    return _open_settings_page("network", "network")


def open_update_settings() -> str:
    return _open_settings_page("update", "Windows Update")


def open_apps_settings() -> str:
    return _open_settings_page("apps", "apps and features")


def open_personalization_settings() -> str:
    return (
        _open_settings_page("personalization", "personalization")
        + " You can change your wallpaper or theme from here."
    )


def open_privacy_settings() -> str:
    return _open_settings_page("privacy", "privacy")


def open_storage_settings() -> str:
    return _open_settings_page("storage", "storage")


def open_power_settings() -> str:
    return _open_settings_page("power", "power and sleep")


def open_about_settings() -> str:
    return _open_settings_page("about", "about")
