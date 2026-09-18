"""
sara.orchestrator.tts_worker
TTSWorker -- speaking/barge-in coordination wrapper around TextToSpeech,
held as self.tts by the GUI Api object.
"""

import time
import queue
import logging
import threading
import itertools

from config import Config

from sara.audio.tts import TextToSpeech
from sara.audio.stt import SpeechToText

from sara.orchestrator._constants import (
    _DEBUG,
    _BARGE_IN_POLL_S,
    _BARGE_IN_GRACE_S,
    _TTS_IDLE_POLL_S,
    _WATCH_IDLE_POLL_S,
    _THREAD_ERROR_BACKOFF_S,
)

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

# Grace period for the worker/watch threads to notice shutdown and exit
# their loops cleanly before we give up waiting on join().
_SHUTDOWN_JOIN_TIMEOUT_S = 2.0


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
        "_seq",
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
        self._q: "queue.PriorityQueue" = queue.PriorityQueue()
        self._seq = itertools.count()
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
                    _priority, _seq, job = self._q.get(timeout=_TTS_IDLE_POLL_S)
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

    def _ears_is_listening(self) -> bool:
        """Defensive check: is STT mid-utterance right now? Never raises --
        missing attr, wrong type, or any exception => False, so old/fake
        `ears` objects (tests, stubs) never crash TTS playback."""
        try:
            ev = getattr(self._ears, "_is_listening", None)
            return bool(ev.is_set()) if ev is not None else False
        except Exception:
            return False

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
            if not self._ears_is_listening():
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
            if not self._ears_is_listening():
                ears.mark_tts_stopped()

    def speak(self, text: str, fast: bool = False, block: bool = True, priority: int = 1) -> None:
        done_event = threading.Event() if block else None
        job = (text, fast, None, None, None, done_event, None)
        self._q.put((priority, next(self._seq), job))
        if block and done_event is not None:
            done_event.wait()

    def speak_stream(self, gen, block: bool = True, on_first_chunk=None, on_chunk=None) -> list:
        done_event = threading.Event()
        sentences_out: list = []
        job = (None, False, gen, on_first_chunk, on_chunk, done_event, sentences_out)
        self._q.put((1, next(self._seq), job))
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

    def _drain_pending_jobs(self) -> None:
        """Unblock any caller stuck in speak()/speak_stream() with
        block=True whose job never got a chance to run because
        shutdown() fired first -- without this, done_event.wait() in
        those callers would hang forever once _run() has already
        exited its loop."""
        while True:
            try:
                _priority, _seq, job = self._q.get_nowait()
            except queue.Empty:
                break
            if job is None:
                continue
            *_rest, done_event, _sentences_out = job
            if done_event is not None:
                done_event.set()

    def shutdown(self) -> None:
        """v16 (LIFECYCLE): previously only set `self._stop` on the
        worker's own dispatch/watch threads -- the underlying
        TextToSpeech instance (persistent OutputStream, AEC far-end
        thread, chunker/synth thread pools) was never explicitly torn
        down here, so real cleanup depended entirely on
        TextToSpeech.__del__ firing at some GC-determined time. This now
        deterministically reaches TextToSpeech.shutdown(), which is
        idempotent (see engine.py), so calling this more than once
        remains safe.

        v17 (cleanup pass): also joins the dispatch/watch threads with a
        bounded timeout and drains any job left sitting in the queue,
        setting its done_event so a caller blocked in speak()/
        speak_stream() can't hang forever past shutdown.
        """
        self._stop.set()
        self._speaking.clear()
        self._barge_stop.set()
        if hasattr(self._voice, "shutdown"):
            self._voice.shutdown()
        self._drain_pending_jobs()
        self._thread.join(timeout=_SHUTDOWN_JOIN_TIMEOUT_S)
        self._watch_thread.join(timeout=_SHUTDOWN_JOIN_TIMEOUT_S)
