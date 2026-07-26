"""
sara.orchestrator.ollama_manager
Starts/stops/health-checks the local Ollama server process.
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
# Ollama auto-start
# ----------------------------------------------------------------------------

_ollama_process = None


def _is_ollama_ready(timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(
            f"{_OLLAMA_HOST}/api/tags", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _start_ollama_background(model: str = _OLLAMA_MODEL) -> None:
    global _ollama_process
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        _ollama_process = subprocess.Popen(
            ["ollama", "run", model],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        _debug_log(
            f"[Ollama] Launched 'ollama run {model}' in background (pid={_ollama_process.pid})."
        )
    except FileNotFoundError:
        print("[Ollama] 'ollama' command not found. Is Ollama installed and on PATH?")
    except Exception as e:
        print(f"[Ollama] Failed to launch background process: {e}")


def _ensure_ollama_running(ui_update=None, model: str = _OLLAMA_MODEL) -> None:
    if _is_ollama_ready():
        _debug_log("[Ollama] Server already running.")
        return

    _debug_log(
        f"[Ollama] Server not detected — starting 'ollama run {model}' in background..."
    )
    if ui_update is not None:
        ui_update("footer", "Starting AI engine (Ollama)...")
    _start_ollama_background(model)

    start = time.monotonic()
    while (time.monotonic() - start) < _OLLAMA_READY_TIMEOUT_S:
        if _is_ollama_ready():
            _debug_log(
                f"[Ollama] Server is ready after {time.monotonic() - start:.1f}s."
            )
            return
        time.sleep(_OLLAMA_POLL_INTERVAL_S)

    print("[Ollama] Warning: server did not respond within timeout; continuing anyway.")
    if ui_update is not None:
        ui_update("footer", "AI engine slow to start — continuing anyway.")


def _stop_ollama_background() -> None:
    global _ollama_process
    if _ollama_process is None:
        return
    try:
        if _ollama_process.poll() is None:
            _ollama_process.terminate()
            try:
                _ollama_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _ollama_process.kill()
            _debug_log("[Ollama] Background process stopped.")
    except Exception as e:
        print(f"[Ollama] Failed to stop background process: {e}")
