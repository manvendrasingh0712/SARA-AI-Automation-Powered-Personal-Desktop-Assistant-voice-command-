"""
sara.tools.system.media_keys
OS-level media-key shortcuts (play/pause/next/prev/stop).
"""
from ._shared import _send_keys

import logging
import re
import platform



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



# ------------------------------------------------------------
# State-aware pause / resume
#
# "pause" and "play" used to share ONE play/pause key press. Saying "pause music" while the
# music was already paused therefore STARTED it, and "play music" was sent to YouTube as a search
# for the word "music". The Windows media session (the same one the Apps page reads) says whether
# something is playing, so we ask it first and only act when the action makes sense.
# ------------------------------------------------------------

_PAUSE_WORDS = re.compile(
    r"\b(?:pause|paws|roko|rok|ruk|stop playing|hold)\b|रोक|पॉज",
    re.IGNORECASE,
)
_RESUME_WORDS = re.compile(
    r"\b(?:play|resume|unpause|continue|chalu|chalao|chala|shuru|start)\b|चला|शुरू|प्ले",
    re.IGNORECASE,
)


def _session_control(action: str):
    """
    Pause or play the current Windows media session. Returns a message, or None when there is no
    session / winsdk is missing (the caller then falls back to the media key).
    """
    if not _IS_WINDOWS:
        return None

    def _run():
        import asyncio

        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )

        async def _go():
            mgr = await MediaManager.request_async()
            session = mgr.get_current_session()
            if session is None:
                return None
            status = int(session.get_playback_info().playback_status)  # 4 playing, 5 paused
            if action == "pause":
                if status != 4:
                    return "It's already paused." if status == 5 else None
                ok = await session.try_pause_async()
                return "Paused the music." if ok else None
            if status == 4:
                return "It's already playing."
            if status != 5:
                return None
            ok = await session.try_play_async()
            return "Resumed the music." if ok else None

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_go())
        finally:
            loop.close()

    try:
        from concurrent.futures import ThreadPoolExecutor

        # Own thread: this may be called from a thread that already runs an event loop.
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run).result(timeout=3.0)
    except Exception as e:  # winsdk missing, no session, timeout ...
        logger.debug(f"media session control unavailable: {e}")
        return None


def pause_media() -> str:
    """Pause only if something is playing (never starts paused music)."""
    done = _session_control("pause")
    if done:
        return done
    return play_pause_media()


def resume_media() -> str:
    """Resume paused music (never pauses music that is already playing)."""
    done = _session_control("play")
    if done:
        return done
    return play_pause_media()


def media_action_for(text: str):
    """
    Pick pause / resume / plain toggle for a play_pause_media request from the words the user said:
    "pause music" -> pause_media, "play music" / "resume" -> resume_media, "play/pause" -> toggle.
    """
    t = (text or "").lower()
    if re.search(r"play\s*(?:/|or|and)?\s*pause|pause\s*(?:/|or|and)\s*play|toggle", t):
        return play_pause_media
    pause, resume = bool(_PAUSE_WORDS.search(t)), bool(_RESUME_WORDS.search(t))
    if pause and not resume:
        return pause_media
    if resume and not pause:
        return resume_media
    return play_pause_media
