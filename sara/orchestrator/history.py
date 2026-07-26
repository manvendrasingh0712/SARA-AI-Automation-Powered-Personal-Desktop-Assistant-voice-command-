"""
sara.orchestrator.history
Restoring conversation history and saved preferences into a fresh
SaraLLM/session at startup.
"""
from .lazy import _debug_log

import re
import sys
import time
import queue
import logging
import threading
import subprocess
import urllib.request
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from logging_config import setup_logging
from health_check import run_startup_diagnostics

from config import Config
from sara.core.llm import SaraLLM

from sara.core.intent import detect_intent
from sara.audio.tts import TextToSpeech
from sara.audio.stt import SpeechToText
from sara.core.memory import PreferencesDB
from sara.tools.reminders import ReminderManager, play_alarm_beep
from sara.tools.clipboard import read_clipboard, write_clipboard
from sara.tools.vision import VisionAssistant
from sara.tools import system as system_tools
from sara.tools import web as web_tools

# PRODUCTION-AUDIT ADDITION (Phase 2): long-term memory (RAG) and the
# LLM tool-calling fallback are both optional, additive features — if
# either module fails to import for any reason (e.g. numpy missing),
# the whole app must still start exactly as before, just without that
# one feature. Both are re-checked as None/False below wherever used.
try:
    from sara.core.rag import LongTermMemory

    _HAS_RAG = True
except Exception as _rag_import_err:  # noqa: BLE001
    LongTermMemory = None
    _HAS_RAG = False
    print(
        f"[Core] sara.core.rag unavailable, long-term memory disabled: {_rag_import_err}"
    )

try:
    from sara.core.tool_router import (
        resolve_tool_call,
        build_fake_match,
        TOOL_NAME_TO_INTENT,
    )

    _HAS_TOOL_ROUTER = True
except Exception as _tool_router_import_err:  # noqa: BLE001
    resolve_tool_call = None
    build_fake_match = None
    TOOL_NAME_TO_INTENT = {}
    _HAS_TOOL_ROUTER = False
    print(
        f"[Core] sara.core.tool_router unavailable, LLM tool-calling fallback "
        f"disabled: {_tool_router_import_err}"
    )

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_EXIT_WORDS = {
    "exit",
    "quit",
    "stop",
    "goodbye",
    "bye",
    "shutdown",
    "band karo",
    "band kar",
    "alvida",
    "phir milenge",
    "bye bye",
    "बंद करो",
    "अलविदा",
}
_SLEEP_WORDS = {
    "sleep",
    "go to sleep",
    "that's all",
    "nothing else",
    "nevermind",
    "so jao",
    "so ja",
    "bas karo",
    "bas kar",
    "theek hai bas",
    "ठीक है बस",
    "सो जाओ",
}
_FORGET_WORDS = {
    "forget our conversation",
    "clear memory",
    "forget everything",
    "clear our conversation",
    "reset memory",
    "sab bhool jao",
    "memory clear karo",
    "history delete karo",
    "conversation bhool jao",
    "सब भूल जाओ",
}

_STRONG_NAME_PHRASES = (
    "my name is ",
    "call me ",
    "mera naam hai ",
    "mera naam ",
    "mujhe bulao ",
    "main hoon ",
)
_WEAK_NAME_PHRASES = ("i am ", "i'm ")

_WEAK_NAME_BLOCKLIST = {
    "sorry",
    "sure",
    "fine",
    "okay",
    "ok",
    "going",
    "not",
    "just",
    "here",
    "still",
    "really",
    "so",
    "very",
    "trying",
    "about",
    "done",
    "ready",
    "afraid",
    "glad",
    "happy",
    "sad",
    "tired",
    "busy",
    "confused",
    "lost",
    "good",
    "great",
    "alright",
    "kidding",
    "joking",
    "serious",
    "curious",
    "worried",
    "excited",
    "bored",
    "annoyed",
    "stressed",
    "hungry",
}

_MAX_EMPTY_RETRIES = 3
_EMPTY_RETRY_GRACE_S = 8.0
_IDLE_SLEEP_TIMEOUT_S = 180

_WAKE_POLL_INTERVAL_S = 0.05
_WAKE_WAIT_TIMEOUT_S = 0.3

_BARGE_IN_POLL_S = 0.05
_BARGE_IN_GRACE_S = 0.2
_TTS_IDLE_POLL_S = 0.5
_WATCH_IDLE_POLL_S = 0.5
_DB_WRITER_IDLE_POLL_S = 1.0

_NETWORK_TOOL_TIMEOUT_S = 6.0

_CALC_EXPR_RE = re.compile(r"^[\d\s\+\-\*\/\(\)\.\%]+$")
_CALC_MAX_LEN = 200
_CALC_MAX_NUMBER_DIGITS = 12
_CALC_MAX_POW_OPS = 1
_CALC_MAX_EXPONENT_VALUE = 1000
_CALC_EXPONENT_RE = re.compile(r"\*\*\s*([+-]?\d+)")

_OLLAMA_HOST = getattr(Config, "OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = getattr(Config, "OLLAMA_MODEL", "qwen2.5")
_OLLAMA_READY_TIMEOUT_S = 60
_OLLAMA_POLL_INTERVAL_S = 0.25

_DEBUG = getattr(Config, "DEBUG_MODE", False)

# Kokoro speed range. Kokoro's `speed` parameter is DIRECTLY
# proportional to playback rate (1.0 = normal, >1.0 = faster).
_KOKORO_SPEED_MIN = 0.6
_KOKORO_SPEED_MAX = 1.4

_POST_TTS_SETTLE_WITH_AEC_S = 0.3

_THREAD_ERROR_BACKOFF_S = 0.5




# ----------------------------------------------------------------------------
# History restore
# ----------------------------------------------------------------------------


def _row_role(r):
    return r.get("role", "") if isinstance(r, dict) else getattr(r, "role", "")


def _row_content(r):
    if isinstance(r, dict):
        return r.get("message") or r.get("content", "")
    return getattr(r, "message", getattr(r, "content", ""))


def _restore_history(db, brain) -> None:
    try:
        limit = Config.MAX_MEMORY_EXCHANGES * 2
        try:
            rows = db.get_recent_messages(limit)
        except TypeError:
            rows = db.get_recent_messages()
        if not rows:
            return

        pairs = []
        i = 0
        while i < len(rows) - 1:
            row_a, row_b = rows[i], rows[i + 1]
            if _row_role(row_a) == "user" and _row_role(row_b) == "assistant":
                pairs.append((_row_content(row_a), _row_content(row_b)))
                i += 2
            else:
                i += 1
        brain.load_history(pairs)
    except Exception as e:
        print(f"[Warning] Could not restore conversation history: {e}")


# ----------------------------------------------------------------------------
# Object construction
# ----------------------------------------------------------------------------


def _apply_saved_preferences(db, ears):
    """Re-apply Voice Control slider preferences saved by the GUI (mic
    sensitivity / speech speed) so they persist across app restarts.

    LANGUAGE-SYNC FEATURE: also restores a previously-chosen manual
    EN/HI language (saved by Api.set_language via the pref writer) so a
    manual choice survives an app restart instead of silently reverting
    to auto-detect. Returns (mode, lang).
    """
    try:
        _saved_mic_sens = db.get_preference("mic_sensitivity")
        if _saved_mic_sens is not None:
            _sens_val = max(0, min(100, int(_saved_mic_sens)))
            ears.energy_threshold = max(100, 1000 - (_sens_val * 9))
            _debug_log(
                f"[Debug] Restored mic sensitivity: {_sens_val}% (threshold={ears.energy_threshold:.0f})"
            )
    except Exception as e:
        print(f"[Warning] Could not restore mic sensitivity: {e}")
    try:
        _saved_speed = db.get_preference("speech_speed")
        if _saved_speed is not None:
            _speed_val = max(0, min(100, int(_saved_speed)))
            span = _KOKORO_SPEED_MAX - _KOKORO_SPEED_MIN
            speed_val_mapped = round(_KOKORO_SPEED_MIN + (_speed_val / 100.0) * span, 3)
            Config.KOKORO_SPEED = speed_val_mapped
            Config.KOKORO_SPEED_EN = speed_val_mapped
            Config.KOKORO_SPEED_HI = speed_val_mapped
            _debug_log(
                f"[Debug] Restored speech speed: {_speed_val}% (kokoro_speed={speed_val_mapped:.2f})"
            )
    except Exception as e:
        print(f"[Warning] Could not restore speech speed: {e}")

    try:
        saved_mode = db.get_preference("language_mode")
        if saved_mode in ("en", "hi"):
            _debug_log(f"[Debug] Restored manual language preference: {saved_mode}")
            return "manual", saved_mode
    except Exception as e:
        print(f"[Warning] Could not restore language preference: {e}")
    return "auto", "en"


def _finish_brain_setup(db, brain) -> None:
    try:
        _restore_history(db, brain)
        saved_name = db.get_user_name()
        if saved_name:
            brain.set_user_name(saved_name)
    except Exception as e:
        print(f"[Warning] Deferred brain setup failed: {e}")
