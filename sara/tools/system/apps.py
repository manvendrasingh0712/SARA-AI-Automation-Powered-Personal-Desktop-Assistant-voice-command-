"""
sara.tools.system.apps
Launch / close applications by name.
"""
from ._shared import _ensure_windows

import logging
import os
import re
import time
import subprocess
import platform

from typing import Dict

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
# APP NAME ALIASES
# ============================================================
_APP_ALIASES: Dict[str, str] = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "browser": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "notepad": "notepad",
    "word": "winword",
    "ms word": "winword",
    "microsoft word": "winword",
    "excel": "excel",
    "ms excel": "excel",
    "microsoft excel": "excel",
    "powerpoint": "powerpnt",
    "ms powerpoint": "powerpnt",
    "microsoft powerpoint": "powerpnt",
    "outlook": "outlook",
    "calculator": "calc",
    "calc": "calc",
    "paint": "mspaint",
    "spotify": "spotify",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "cmd": "cmd",
    "command prompt": "cmd",
    "terminal": "cmd",
    "powershell": "powershell",
    "task manager": "taskmgr",
    "control panel": "control",
    "settings": "ms-settings:",
    "file explorer": "explorer",
    "explorer": "explorer",
    "files": "explorer",
    "photos": "ms-photos:",
    "camera": "microsoft.windows.camera:",
    "snipping tool": "SnippingTool",
    "wordpad": "write",
    "vlc": "vlc",
    "discord": "discord",
    "telegram": "telegram",
    "whatsapp": "whatsapp:",
    "zoom": "zoom",
    "teams": "msteams",
    "microsoft teams": "msteams",
    "steam": "steam",
    "obs": "obs64",
}


# ============================================================
# KEY NAME ALIASES
# ============================================================
# FINAL PRODUCTION POLISH: hoisted out of press_key() (see below) to


# ============================================================
# SYSTEM ACTIONS — apps
# ============================================================


def open_application(app_name: str) -> str:
    _ensure_windows()

    if not app_name or not app_name.strip():
        return "No application name was provided."

    raw_name = app_name.strip()
    target = _APP_ALIASES.get(raw_name.lower(), raw_name)

    try:
        os.startfile(target)
        if Config.DEBUG_MODE:
            print(f"[Debug] Launched application: {target} (spoken: '{raw_name}')")
        return f"Opened {raw_name}."
    except FileNotFoundError:
        try:
            subprocess.Popen(target, shell=True)
            return f"Opened {raw_name}."
        except Exception as e:
            logger.error(f"open_application fallback failed for '{raw_name}': {e}")
            return f"Sorry, I couldn't find or open '{raw_name}'."
    except Exception as e:
        logger.error(f"open_application failed for '{raw_name}': {e}")
        return f"Sorry, I couldn't open '{raw_name}' right now."

def restart_application(app_name: str) -> str:
    """
    Terminates all running instances of app_name, then relaunches it from
    the SAME executable path (captured before termination via psutil's
    proc.exe()) rather than guessing via os.startfile(), which only
    resolves names Windows already knows about (PATH / App Paths registry)
    and can silently launch a different install than the one that was
    actually running.
    """
    _ensure_windows()

    if not app_name or not app_name.strip():
        return "No application name was provided."

    raw_name = app_name.strip()
    target = _APP_ALIASES.get(raw_name.lower(), raw_name)

    if ":" in target:
        return (
            f"'{raw_name}' isn't a running application I can restart "
            f"(it opens a system page, not a process)."
        )

    process_exe = target if target.lower().endswith(".exe") else target + ".exe"

    exe_path = None
    terminated_pids = []
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == process_exe.lower():
                    if not exe_path:
                        exe_path = proc.info.get("exe")
                    proc.terminate()
                    terminated_pids.append(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        logger.error(f"restart_application enumeration failed for '{raw_name}': {e}")
        return f"Sorry, I couldn't restart '{raw_name}' right now."

    if not terminated_pids:
        return open_application(raw_name)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and any(psutil.pid_exists(p) for p in terminated_pids):
        time.sleep(0.1)

    try:
        if exe_path:
            subprocess.Popen(exe_path)
        else:
            os.startfile(target)
        return f"Restarted {raw_name}."
    except Exception as e:
        logger.error(f"restart_application relaunch failed for '{raw_name}': {e}")
        return f"Closed {raw_name} but couldn't relaunch it automatically."

def close_application(process_name: str) -> str:
    _ensure_windows()

    if not process_name or not process_name.strip():
        return "No process name was provided."

    raw_name = process_name.strip()
    target = _APP_ALIASES.get(raw_name.lower(), raw_name)

    # FINAL PRODUCTION POLISH: some _APP_ALIASES values are URI-scheme
    # handlers intended for open_application()'s os.startfile() (e.g.
    # "ms-settings:", "whatsapp:", "microsoft.windows.camera:"), not
    # real running-process names. Blindly appending ".exe" below would
    # have produced an unmatchable target like "ms-settings:.exe" — now
    # caught early with a clear message instead of silently closing
    # nothing.
    if ":" in target:
        return f"'{raw_name}' isn't a running application I can close (it opens a system page, not a process)."

    if not target.lower().endswith(".exe"):
        target += ".exe"

    closed_count = 0
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == target.lower():
                    proc.terminate()
                    closed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if closed_count > 0:
            return f"Closed {closed_count} instance(s) of {target}."
        return f"No running process found matching '{target}'."
    except Exception as e:
        logger.error(f"close_application failed for '{raw_name}': {e}")
        return f"Sorry, I couldn't close '{raw_name}' right now."