"""
sara.orchestrator.network_utils
Bounded-timeout wrapper for network-bound tool calls (search/weather/news/
URL fetch), so a slow network can never hang the conversation loop.
"""

import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError


from config import Config


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
# Network-bound tool call wrapper
# ----------------------------------------------------------------------------

_NETWORK_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sara-net")

# CIRCUIT BREAKER: if a given tool times out / errors this many times in a
# row, we stop even attempting it for a cooldown window and return an
# immediate friendly message instead. Protects the voice loop from
# repeatedly stalling for the full timeout on a tool that's clearly down
# (dead network, blocked API, etc.) -- one bad call is a fluke, three in a
# row is a real outage.
_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_COOLDOWN_S = 30.0

_breaker_failure_counts: dict[str, int] = {}
_breaker_open_until: dict[str, float] = {}
_breaker_lock = threading.Lock()


def _call_with_timeout(
    fn, *args, timeout: float = _NETWORK_TOOL_TIMEOUT_S, tool_name: str = None, **kwargs
):
    name = tool_name or getattr(fn, "__name__", "unknown_tool")

    with _breaker_lock:
        open_until = _breaker_open_until.get(name, 0.0)
        if time.time() < open_until:
            remaining = int(open_until - time.time())
            return (
                f"Sorry, that's not responding right now -- give it about "
                f"{max(remaining, 1)} seconds and try again."
            )

    future = _NETWORK_EXECUTOR.submit(fn, *args, **kwargs)
    try:
        result = future.result(timeout=timeout)
    except FutureTimeoutError:
        future.cancel()
        _record_breaker_failure(name)
        return (
            "Sorry, that's taking longer than expected. Please try again in a moment."
        )
    except Exception as e:
        _record_breaker_failure(name)
        return f"Sorry, I ran into a problem: {e}"
    else:
        _record_breaker_success(name)
        return result


def _record_breaker_failure(name: str) -> None:
    with _breaker_lock:
        count = _breaker_failure_counts.get(name, 0) + 1
        _breaker_failure_counts[name] = count
        if count >= _BREAKER_FAILURE_THRESHOLD:
            _breaker_open_until[name] = time.time() + _BREAKER_COOLDOWN_S
            _breaker_failure_counts[name] = 0
            logger.warning(
                f"[CircuitBreaker] '{name}' tripped after {_BREAKER_FAILURE_THRESHOLD} "
                f"consecutive failures, cooling down for {_BREAKER_COOLDOWN_S}s"
            )


def _record_breaker_success(name: str) -> None:
    with _breaker_lock:
        _breaker_failure_counts[name] = 0
        _breaker_open_until.pop(name, None)


def _shutdown_network_executor() -> None:
    try:
        _NETWORK_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        _NETWORK_EXECUTOR.shutdown(wait=False)
    except Exception as e:
        logger.error(f"[Shutdown] Failed to shut down network executor: {e}")