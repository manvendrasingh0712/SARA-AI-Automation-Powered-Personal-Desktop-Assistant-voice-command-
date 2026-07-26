"""
sara.orchestrator.text_utils
Name-extraction and phrase-matching helpers used while parsing user
speech (e.g. "my name is ..." / exit/sleep/forget phrase sets).
"""

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
# Name extraction
# ----------------------------------------------------------------------------

_NAME_STOP_RE = re.compile(
    r"\b(and|but|so|because|today|right now|here|there)\b", re.IGNORECASE
)


def _cap_word(word: str) -> str:
    if not word:
        return word
    return word[0].upper() + word[1:]


def _capture_name_after(text: str, start_idx: int):
    remainder = text[start_idx:].strip()
    if not remainder:
        return None
    remainder = re.split(r"[.!?,;]", remainder, maxsplit=1)[0]
    stop_match = _NAME_STOP_RE.search(remainder)
    if stop_match:
        remainder = remainder[: stop_match.start()]
    words = remainder.strip().split()
    if not words:
        return None
    name_words = words[:3]
    return " ".join(_cap_word(w) for w in name_words)


def _extract_name(text: str):
    lowered = text.lower()

    for phrase in _STRONG_NAME_PHRASES:
        if phrase in lowered:
            idx = lowered.index(phrase) + len(phrase)
            return _capture_name_after(text, idx)

    for phrase in _WEAK_NAME_PHRASES:
        if phrase in lowered:
            idx = lowered.index(phrase) + len(phrase)
            remainder = text[idx:].strip()
            if not remainder:
                continue
            first_word = remainder.split()[0].lower().strip(".,!?;")
            if first_word in _WEAK_NAME_BLOCKLIST:
                continue
            return _capture_name_after(text, idx)

    return None


def _matches_phrase_set(text: str, phrases: set) -> bool:
    cleaned = text.lower().strip().strip(".!?,")
    if cleaned in phrases:
        return True
    words = cleaned.split()
    if not words:
        return False
    for phrase in phrases:
        phrase_words = phrase.split()
        n = len(phrase_words)
        if n == 0 or n > len(words):
            continue
        if words[:n] == phrase_words or words[-n:] == phrase_words:
            return True
    return False
