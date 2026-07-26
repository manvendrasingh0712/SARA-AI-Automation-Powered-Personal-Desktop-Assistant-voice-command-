"""
sara.tools.system.audio_display
System volume and screen-brightness control.
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
# VOLUME
# ============================================================


def set_volume(level: int) -> str:
    _ensure_windows()

    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        level = max(0, min(100, int(level)))

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume set to {level}%."
    except ImportError:
        return "Volume control requires 'pycaw' and 'comtypes'. Run: pip install pycaw comtypes"
    except Exception as e:
        logger.error(f"set_volume failed: {e}")
        return "Sorry, I couldn't set the volume right now."


def adjust_volume(delta: int) -> str:
    _ensure_windows()

    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        # BUG FIX: GetMasterVolumeLevelScalar can return None on some drivers
        current = volume.GetMasterVolumeLevelScalar()
        if current is None:
            current = 0.0

        new_level = max(0.0, min(1.0, current + (delta / 100.0)))
        volume.SetMasterVolumeLevelScalar(new_level, None)

        return f"Volume adjusted to {round(new_level * 100)}%."
    except ImportError:
        return "Volume control requires 'pycaw' and 'comtypes'. Run: pip install pycaw comtypes"
    except Exception as e:
        logger.error(f"adjust_volume failed: {e}")
        return "Sorry, I couldn't adjust the volume right now."


# ============================================================
# BRIGHTNESS
# ============================================================


def get_brightness_status() -> str:
    try:
        import screen_brightness_control as sbc

        levels = sbc.get_brightness()
        level = levels[0] if isinstance(levels, list) else levels
        return f"Screen brightness is currently at {level}%."
    except ImportError:
        return "Brightness control requires 'screen-brightness-control'. Run: pip install screen-brightness-control"
    except Exception as e:
        logger.error(f"get_brightness_status failed: {e}")
        return "Sorry, I couldn't retrieve the brightness right now."


def set_brightness(level: int) -> str:
    try:
        import screen_brightness_control as sbc

        level = max(0, min(100, int(level)))
        sbc.set_brightness(level)
        return f"Brightness set to {level}%."
    except ImportError:
        return "Brightness control requires 'screen-brightness-control'. Run: pip install screen-brightness-control"
    except Exception as e:
        logger.error(f"set_brightness failed: {e}")
        return "Sorry, I couldn't set the brightness right now."


def adjust_brightness(delta: int) -> str:
    try:
        import screen_brightness_control as sbc

        current = sbc.get_brightness()
        current = current[0] if isinstance(current, list) else current
        new_level = max(0, min(100, current + delta))
        sbc.set_brightness(new_level)
        return f"Brightness adjusted to {new_level}%."
    except ImportError:
        return "Brightness control requires 'screen-brightness-control'. Run: pip install screen-brightness-control"
    except Exception as e:
        logger.error(f"adjust_brightness failed: {e}")
        return "Sorry, I couldn't adjust the brightness right now."
