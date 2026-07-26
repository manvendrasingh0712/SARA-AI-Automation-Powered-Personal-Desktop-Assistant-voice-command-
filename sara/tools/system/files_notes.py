"""
sara.tools.system.files_notes
File search, recycle bin, and the plain-text quick-notes feature.
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
# FILE OPERATIONS
# ============================================================


def find_file(name: str) -> str:
    """
    Searches common user directories for a file with the given name.
    Searches Downloads, Documents, Desktop, Pictures, Music, Videos,
    and the user's home folder. All directories are scanned recursively
    via os.walk(), guarded by a 3-second timeout — if the search takes
    longer than 3 seconds, it stops early and returns whatever matches
    have been found so far (or a "no results within the time limit"
    message if none have been found yet).

    Args:
        name: Filename or partial filename to search for.

    Returns:
        Path to the file if found, or a helpful not-found message.
    """
    if not name or not name.strip():
        return "Please tell me the name of the file to find."

    name = name.strip().lower()
    home = os.path.expanduser("~")
    search_dirs = [
        os.path.join(home, "Downloads"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Pictures"),
        os.path.join(home, "Music"),
        os.path.join(home, "Videos"),
        home,
    ]

    found = []
    start_time = time.monotonic()
    timed_out = False
    try:
        for base in search_dirs:
            if not os.path.isdir(base):
                continue
            for root, dirs, files in os.walk(base):
                # Don't recurse into hidden/system dirs
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if name in f.lower():
                        found.append(os.path.join(root, f))
                    if time.monotonic() - start_time > 3.0:
                        timed_out = True
                        break
                if len(found) >= 5 or timed_out:
                    break
                if time.monotonic() - start_time > 3.0:
                    timed_out = True
                    break
            if len(found) >= 5 or timed_out:
                break

        if not found:
            if timed_out:
                return "No results found within the time limit."
            return f"Could not find any file matching '{name}' in your common folders."

        if len(found) == 1:
            return f"Found it: {found[0]}"

        results = "; ".join(found[:5])
        return f"Found {len(found)} file(s) matching '{name}': {results}"

    except Exception as e:
        logger.error(f"find_file failed: {e}")
        return "Sorry, I couldn't complete the file search right now."


def empty_recycle_bin() -> str:
    """Empties the Windows Recycle Bin silently (no confirmation dialog)."""
    _ensure_windows()
    try:
        # SHERB_NOCONFIRMATION=0x1, SHERB_NOPROGRESSUI=0x2, SHERB_NOSOUND=0x4
        flags = 0x1 | 0x2 | 0x4
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        if result == 0 or result == -2147418113:  # S_OK or already empty
            return "Recycle Bin has been emptied."
        return f"Recycle Bin emptied (code {result})."
    except Exception as e:
        logger.error(f"empty_recycle_bin failed: {e}")
        return "Sorry, I couldn't empty the Recycle Bin right now."


# ============================================================
# NOTES
# ============================================================


def take_note(text: str, return_id: bool = False):
    """
    Appends a timestamped note to sara_notes.txt.

    Args:
        text: note content.
        return_id: when False (default — unchanged from before), returns
            the plain confirmation string exactly like before, so every
            EXISTING caller of take_note() (voice commands, gui_main.py,
            etc.) keeps working with zero changes. When True, returns a
            dict {"message": str, "id": int|None} instead, where `id` is
            a stable 0-indexed line number that the desktop UI's Quick
            Notes backend-sync uses to avoid re-importing the same note
            twice (see gui/app.py: Api.save_note).
    """
    if not text or not text.strip():
        msg = "Nothing to note down."
        return {"message": msg, "id": None} if return_id else msg
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(_NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {text.strip()}\n")
        message = f"Got it. I've noted: {text.strip()}"
        if not return_id:
            return message
        # id = 0-indexed line number of the note we just appended. Safe
        # to compute by re-reading the file (notes are only ever
        # appended, never edited in place, so this is cheap and rare —
        # note-taking isn't a high-frequency action).
        try:
            with open(_NOTES_FILE, "r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f if _.strip())
            note_id = line_count - 1
        except Exception:
            note_id = None
        return {"message": message, "id": note_id}
    except Exception as e:
        logger.error(f"take_note failed: {e}")
        err = "Sorry, I couldn't save that note right now."
        return {"message": err, "id": None} if return_id else err


def read_notes() -> str:
    """Reads all saved notes from sara_notes.txt."""
    try:
        if not os.path.exists(_NOTES_FILE):
            return "You have no saved notes."
        with open(_NOTES_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return "Your notes are empty."
        lines = content.split("\n")
        if len(lines) == 1:
            return f"You have one note: {lines[0]}"
        return f"You have {len(lines)} notes: " + ". ".join(lines)
    except Exception as e:
        logger.error(f"read_notes failed: {e}")
        return "Sorry, I couldn't read your notes right now."


def clear_notes() -> str:
    """Deletes all saved notes."""
    try:
        if not os.path.exists(_NOTES_FILE):
            return "You have no notes to clear."
        os.remove(_NOTES_FILE)
        return "All your notes have been cleared."
    except Exception as e:
        logger.error(f"clear_notes failed: {e}")
        return "Sorry, I couldn't clear your notes right now."


def get_notes() -> list:
    """
    Returns all saved notes as a list of {"id","text","timestamp"} dicts
    (oldest first, same order as the file), for the desktop UI's Quick
    Notes backend sync (see gui/app.py: Api.get_notes).

    `id` is the 0-indexed line number within sara_notes.txt. This is
    stable as long as notes are only ever appended and never
    individually edited/reordered/deleted — which matches the current
    behavior exactly (the only bulk operation is clear_notes(), which
    wipes the whole file, and the frontend already re-syncs cleanly
    from an empty list in that case).
    """
    try:
        if not os.path.exists(_NOTES_FILE):
            return []
        with open(_NOTES_FILE, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]
        result = []
        for idx, line in enumerate(lines):
            m = _NOTE_LINE_RE.match(line)
            if m:
                result.append(
                    {
                        "id": idx,
                        "text": m.group("text"),
                        "timestamp": m.group("ts"),
                    }
                )
            else:
                # Malformed/legacy line with no "[timestamp]" prefix —
                # still surface it rather than silently dropping a saved
                # note the user actually wrote.
                result.append({"id": idx, "text": line, "timestamp": ""})
        return result
    except Exception as e:
        logger.error(f"get_notes failed: {e}")
        return []
