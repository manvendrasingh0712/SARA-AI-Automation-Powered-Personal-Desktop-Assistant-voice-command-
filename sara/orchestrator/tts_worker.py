"""
sara.orchestrator.tts_worker
TTSWorker -- speaking/barge-in coordination wrapper around TextToSpeech,
held as self.tts by the GUI Api object.
"""

import re
import time
import queue
import logging
import threading


from config import Config

from sara.audio.tts import TextToSpeech
from sara.audio.stt import SpeechToText

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
# TTS worker
# ----------------------------------------------------------------------------


class TTSWorker:
    # See sara/core/llm/engine.py (SaraLLM._serializable) -- self.tts is
    # exposed directly off the Api object, so this stops pywebview's js_api
    # bridge from recursing into it (this class's own attrs are already
    # underscore-prefixed via __slots__ below, but this closes the loop
    # for good measure and stops its public methods being exposed as
    # unused pywebview.api.tts.* stubs).
    _serializable = False

    __slots__ = (
        "_voice",
        "_ears",
        "_q",
        "_stop",
        "_speaking",
        "_barge_stop",
        "_speech_started_at",
        "_thread",
        "_watch_thread",
        "_db",
    )

    def __init__(self, voice: TextToSpeech, ears: SpeechToText, db=None):
        self._voice = voice
        self._ears = ears
        self._q: "queue.SimpleQueue" = queue.SimpleQueue()
        self._stop = threading.Event()

        self._speaking = threading.Event()
        self._barge_stop = threading.Event()
        self._speech_started_at = 0.0
        self._db = db

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._watch_thread.start()

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._speaking.wait(timeout=_WATCH_IDLE_POLL_S):
                    continue
                if not Config.BARGE_IN_ENABLED:
                    time.sleep(_WATCH_IDLE_POLL_S)
                    continue
                if time.monotonic() - self._speech_started_at < _BARGE_IN_GRACE_S:
                    time.sleep(_BARGE_IN_POLL_S)
                    continue
                if self._voice.is_speaking() and self._ears.is_user_speaking(
                    duration=0.3
                ):
                    self._voice.stop()
                    self._barge_stop.set()
                time.sleep(_BARGE_IN_POLL_S)
            except Exception as e:
                logger.exception(f"[TTSWorker] watch_loop error (continuing): {e}")
                time.sleep(_THREAD_ERROR_BACKOFF_S)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                try:
                    job = self._q.get(timeout=_TTS_IDLE_POLL_S)
                except queue.Empty:
                    continue
                if job is None:
                    continue
                text, fast, gen, on_first_chunk, on_chunk, done_event, sentences_out = job
                try:
                    if gen is not None:
                        sentences_out.extend(
                            self._speak_stream_blocking(gen, on_first_chunk, on_chunk)
                        )
                    else:
                        self._speak_blocking(text, fast)
                except Exception as e:
                    logger.exception(f"[TTSWorker] playback error: {e}")
                finally:
                    if done_event is not None:
                        done_event.set()
            except Exception as e:
                logger.exception(f"[TTSWorker] run loop error (continuing): {e}")
                time.sleep(_THREAD_ERROR_BACKOFF_S)

    def _arm_barge_in(self) -> None:
        self._barge_stop.clear()
        self._speech_started_at = time.monotonic()
        self._speaking.set()

    def _disarm_barge_in(self) -> None:
        self._speaking.clear()

    def _should_speak(self) -> bool:
        """Gate for actual audio synthesis/playback -- checks "muted"
        and "setting:voice_replies". Safe by default: no db, or any
        lookup error, => True (never block speech on an error)."""
        if self._db is None:
            return True
        try:
            if self._db.get_preference("muted", "0") == "1":
                return False
            if self._db.get_preference("setting:voice_replies", "1") == "0":
                return False
            return True
        except Exception as e:
            print(f"[TTSWorker] _should_speak preference check failed (defaulting to speak): {e}")
            return True

    def _speak_blocking(self, text: str, fast: bool) -> None:
        if not self._should_speak():
            return
        voice, ears = self._voice, self._ears
        ears.set_tts_active(True)
        try:
            if not Config.BARGE_IN_ENABLED:
                voice.speak(text, fast=fast)
                return
            self._arm_barge_in()
            try:
                voice.speak(text, fast=fast)
            finally:
                self._disarm_barge_in()
        finally:
            ears.set_tts_active(False)
            ears.mark_tts_stopped()

    def _speak_stream_blocking(self, gen, on_first_chunk=None, on_chunk=None) -> list:
        voice, ears = self._voice, self._ears
        sentences = []
        first_seen = False

        def _collecting_gen():
            nonlocal first_seen
            for s in gen:
                if self._barge_stop.is_set():
                    break
                if not first_seen:
                    first_seen = True
                    if on_first_chunk is not None:
                        try:
                            on_first_chunk()
                        except Exception as e:
                            print(f"[TTSWorker] on_first_chunk callback failed: {e}")
                sentences.append(s)
                if _DEBUG:
                    print(f"[LIVE-CAPTION-DEBUG] chunk fired @ {time.time():.3f}: {s[:30]}")
                if on_chunk is not None:
                    try:
                        on_chunk(s)
                    except Exception as e:
                        print(f"[TTSWorker] on_chunk callback failed: {e}")
                if _DEBUG:
                    print(f"[Streaming to Audio]: {s}")
                yield s

        if not self._should_speak():
            # Muted / voice_replies off: generator still gets fully
            # consumed so on_first_chunk/on_chunk fire and `sentences`
            # comes back complete (live captions keep working) -- but
            # voice.speak_stream() / ears.set_tts_active() /
            # mark_tts_stopped() are never touched, since nothing is
            # actually producing audio.
            for _ in _collecting_gen():
                pass
            return sentences

        ears.set_tts_active(True)
        try:
            if not Config.BARGE_IN_ENABLED:
                voice.speak_stream(_collecting_gen())
                return sentences

            self._arm_barge_in()
            try:
                voice.speak_stream(_collecting_gen())
            finally:
                self._disarm_barge_in()
            return sentences
        finally:
            ears.set_tts_active(False)
            ears.mark_tts_stopped()

    def speak(self, text: str, fast: bool = False, block: bool = True) -> None:
        done_event = threading.Event() if block else None
        self._q.put((text, fast, None, None, None, done_event, None))
        if block and done_event is not None:
            done_event.wait()

    def speak_stream(self, gen, block: bool = True, on_first_chunk=None, on_chunk=None) -> list:
        done_event = threading.Event()
        sentences_out: list = []
        self._q.put((None, False, gen, on_first_chunk, on_chunk, done_event, sentences_out))
        if block:
            done_event.wait()
            return sentences_out
        return []

    def set_language(self, lang: str) -> None:
        self._voice.set_language(lang)

    def set_speed(self, speed: float) -> None:
        """v8: forwards to the underlying TextToSpeech.set_speed()
        (see sara/audio/tts.py), used by Api.set_speech_speed() in
        sara/gui/app.py so the Voice Control page's speed slider
        actually affects live playback speed."""
        if hasattr(self._voice, "set_speed"):
            self._voice.set_speed(speed)

    def stop(self) -> None:
        self._voice.stop()

    def clear_interrupt(self) -> None:
        if hasattr(self._voice, "clear_interrupt"):
            self._voice.clear_interrupt()

    def is_interrupted(self) -> bool:
        if hasattr(self._voice, "is_interrupted"):
            return self._voice.is_interrupted()
        return False

    def shutdown(self) -> None:
        """v16 (LIFECYCLE): previously only set `self._stop` on the
        worker's own dispatch/watch threads -- the underlying
        TextToSpeech instance (persistent OutputStream, AEC far-end
        thread, chunker/synth thread pools) was never explicitly torn
        down here, so real cleanup depended entirely on
        TextToSpeech.__del__ firing at some GC-determined time. This now
        deterministically reaches TextToSpeech.shutdown(), which is
        idempotent (see engine.py), so calling this more than once
        remains safe."""
        self._stop.set()
        if hasattr(self._voice, "shutdown"):
            self._voice.shutdown()