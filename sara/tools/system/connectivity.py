"""
sara.tools.system.connectivity
WiFi / Bluetooth toggles and dark/light theme switching.
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
# NETWORK — WiFi & Bluetooth
# ============================================================


def wifi_on() -> str:
    """Enables WiFi using netsh."""
    _ensure_windows()
    try:
        result = subprocess.run(
            ["netsh", "interface", "set", "interface", "Wi-Fi", "enabled"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "WiFi has been turned on."
        # Some systems use different interface names; try opening Settings
        os.startfile("ms-settings:network-wifi")
        return "Opened WiFi settings. Please enable WiFi from there."
    except subprocess.TimeoutExpired:
        return "Sorry, that command took too long to respond."
    except Exception as e:
        logger.error(f"wifi_on failed: {e}")
        return "Sorry, I couldn't turn WiFi on right now."


def wifi_off() -> str:
    """Disables WiFi using netsh."""
    _ensure_windows()
    try:
        result = subprocess.run(
            ["netsh", "interface", "set", "interface", "Wi-Fi", "disabled"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "WiFi has been turned off."
        os.startfile("ms-settings:network-wifi")
        return "Opened WiFi settings. Please disable WiFi from there."
    except subprocess.TimeoutExpired:
        return "Sorry, that command took too long to respond."
    except Exception as e:
        logger.error(f"wifi_off failed: {e}")
        return "Sorry, I couldn't turn WiFi off right now."


def bluetooth_on() -> str:
    """Opens Bluetooth settings for the user to enable it."""
    _ensure_windows()
    try:
        # Windows has no reliable CLI for Bluetooth toggle without third-party tools.
        # Open the Settings page — most reliable cross-version approach.
        os.startfile("ms-settings:bluetooth")
        return "Opened Bluetooth settings. Please turn Bluetooth on from there."
    except Exception as e:
        logger.error(f"bluetooth_on failed: {e}")
        return "Sorry, I couldn't open Bluetooth settings right now."


def bluetooth_off() -> str:
    """Opens Bluetooth settings for the user to disable it."""
    _ensure_windows()
    try:
        os.startfile("ms-settings:bluetooth")
        return "Opened Bluetooth settings. Please turn Bluetooth off from there."
    except Exception as e:
        logger.error(f"bluetooth_off failed: {e}")
        return "Sorry, I couldn't open Bluetooth settings right now."


# ============================================================
# DISPLAY — Dark Mode / Light Mode
# ============================================================


def dark_mode() -> str:
    """Switches Windows to dark mode by writing to the registry."""
    _ensure_windows()
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 0)
        # Broadcast the change so apps pick it up
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 5000, None
        )
        return "Dark mode has been enabled."
    except Exception as e:
        logger.error(f"dark_mode failed: {e}")
        return "Sorry, I couldn't enable dark mode right now."


def light_mode() -> str:
    """Switches Windows to light mode by writing to the registry."""
    _ensure_windows()
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1)
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 5000, None
        )
        return "Light mode has been enabled."
    except Exception as e:
        logger.error(f"light_mode failed: {e}")
        return "Sorry, I couldn't enable light mode right now."
