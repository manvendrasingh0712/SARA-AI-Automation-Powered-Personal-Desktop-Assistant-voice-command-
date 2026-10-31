"""
sara.tools.system.timers
Voice-triggered countdown timers.
"""

import logging
import re
import platform
import threading

from typing import Callable, Optional


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
# TIMER
# ============================================================

# Module-level timer reference so it can be cancelled
_active_timer: Optional[threading.Timer] = None
_active_timer_lock = threading.Lock()


def set_timer(
    seconds: int, label: str = "timer", callback: Optional[Callable[[str], None]] = None
) -> str:
    """
    Starts a countdown timer. When it fires, calls callback(message)
    if provided, otherwise prints to console.

    Args:
        seconds: Duration in seconds.
        label: Human-readable label (e.g. "5 minute timer").
        callback: Optional function to call when timer fires. Receives
                  the reminder message string. In gui_main.py this
                  should be wired to _speak() / ui_update().

    Returns:
        Confirmation message.
    """
    global _active_timer

    if seconds <= 0:
        return "Timer duration must be greater than zero."

    def _fire():
        msg = f"Your {label} is done!"
        if callback:
            try:
                callback(msg)
            except Exception as e:
                print(f"[Timer] Callback error: {e}")
        else:
            print(f"\n[Timer] {msg}")

    with _active_timer_lock:
        if _active_timer and _active_timer.is_alive():
            _active_timer.cancel()
        _active_timer = threading.Timer(seconds, _fire)
        _active_timer.daemon = True
        _active_timer.start()

    # Format the confirmation message (an alarm hands in float seconds: "29.0 hours" read badly)
    seconds = int(round(seconds))
    if seconds < 60:
        duration_str = f"{seconds} second{'s' if seconds != 1 else ''}"
    elif seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        duration_str = f"{mins} minute{'s' if mins != 1 else ''}"
        if secs:
            duration_str += f" and {secs} second{'s' if secs != 1 else ''}"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        duration_str = f"{hours} hour{'s' if hours != 1 else ''}"
        if mins:
            duration_str += f" and {mins} minute{'s' if mins != 1 else ''}"

    return f"Timer set for {duration_str}. I'll let you know when it's done."


def cancel_timer() -> str:
    """Cancels the currently active timer, if any."""
    global _active_timer
    with _active_timer_lock:
        if _active_timer and _active_timer.is_alive():
            _active_timer.cancel()
            _active_timer = None
            return "Timer cancelled."
    return "No active timer to cancel."

# ── Stopwatch ──────────────────────────────────────────────────────────────
# E2E audit 2026-10-30: "start the stopwatch" / "lap" / "stop the stopwatch"
# are recognised by the router and call system_tools.start/lap/stop_stopwatch,
# which did not exist: every voice stopwatch command answered "Sorry, I ran
# into a problem". The Clock Card on screen times itself in JavaScript, so the
# voice side needs its own clock. time.monotonic() => immune to clock changes.
import time as _time

_sw_lock = threading.Lock()
_sw: dict = {"started_at": None, "elapsed": 0.0, "laps": [], "last_lap": 0.0}


def _spoken_duration(seconds: float) -> str:
    """12.4 -> '12 seconds', 75 -> '1 minute 15 seconds', 3725 -> '1 hour 2 minutes 5 seconds'."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if s or not parts:
        parts.append(f"{s} second{'s' if s != 1 else ''}")
    return " ".join(parts)


def _sw_elapsed_locked() -> float:
    if _sw["started_at"] is None:
        return _sw["elapsed"]
    return _sw["elapsed"] + (_time.monotonic() - _sw["started_at"])


def start_stopwatch() -> str:
    with _sw_lock:
        if _sw["started_at"] is not None:
            return f"The stopwatch is already running, at {_spoken_duration(_sw_elapsed_locked())}."
        _sw.update(started_at=_time.monotonic(), elapsed=0.0, laps=[], last_lap=0.0)
    return "Stopwatch started."


def lap_stopwatch() -> str:
    with _sw_lock:
        if _sw["started_at"] is None:
            return "The stopwatch isn't running. Say 'start the stopwatch' first."
        now = _sw_elapsed_locked()
        lap = now - _sw["last_lap"]
        _sw["laps"].append(lap)
        _sw["last_lap"] = now
        n = len(_sw["laps"])
    return f"Lap {n}: {_spoken_duration(lap)}. Total {_spoken_duration(now)}."


def stop_stopwatch() -> str:
    with _sw_lock:
        if _sw["started_at"] is None:
            if _sw["elapsed"] > 0:
                return f"The stopwatch is already stopped at {_spoken_duration(_sw['elapsed'])}."
            return "The stopwatch isn't running."
        _sw["elapsed"] = _sw_elapsed_locked()
        _sw["started_at"] = None
        total, laps = _sw["elapsed"], len(_sw["laps"])
    tail = f" with {laps} lap{'s' if laps != 1 else ''}" if laps else ""
    return f"Stopwatch stopped at {_spoken_duration(total)}{tail}."
