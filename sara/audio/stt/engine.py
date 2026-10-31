"""
sara.audio.stt.engine
SpeechToText -- the public class. Wires helpers + buffers together into the
mic-capture -> VAD -> faster-whisper transcription pipeline.
"""
from __future__ import annotations

from .helpers import (
    _rms,
    _detect_language,
    _lang_from_stt_language,
    _is_hallucinated_repetition,
    _is_known_hallucination,
    _is_prompt_echo,
    choose_transcript,
    need_main_model,
    pick_after_hinglish_first,
    _is_degenerate_text,
    _is_foreign_script_hallucination,
)
from .vocabulary import vocabulary_hint
from .buffers import _PreBuffer, _RingBuffer, _VADFilter, _SilenceGate, _NoiseFloor, _CollectState

import os
import atexit
import logging
import collections
import difflib
import math
import re
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional
from concurrent.futures import ThreadPoolExecutor

try:
    from sara.core.telemetry import mark_pending as _t_pending
except Exception:  # telemetry is optional: fall back to a no-op
    def _t_pending(*args, **kwargs) -> None:
        return None

import numpy as np


def _optional_import_failed(dep_name: str, exc: Exception) -> None:
    """Log a soft-disabled optional dependency without crashing this module.

    Deliberately catches Exception (not just ImportError/OSError): a
    version-incompatible transitive dependency (e.g. an sklearn/scipy build
    that predates the installed numpy/Python ABI) can raise TypeError,
    RuntimeError, AttributeError, etc. *during* import, not just the
    ImportError/OSError this used to be narrowed to. Any of those must
    still degrade this feature to "disabled", not take down STT entirely.
    """
    print(f"[STT] {dep_name} import failed; related features disabled. ({type(exc).__name__}: {exc})")


try:
    from faster_whisper import WhisperModel

    _HAS_WHISPER = True
except Exception as e:
    _HAS_WHISPER = False
    _optional_import_failed("faster-whisper", e)

try:
    import webrtcvad

    _HAS_VAD = True
except Exception as e:
    webrtcvad = None
    _HAS_VAD = False
    _optional_import_failed("webrtcvad", e)

try:
    import sounddevice as sd

    _HAS_SD = True
except Exception as e:
    _HAS_SD = False
    _optional_import_failed("sounddevice", e)

try:
    from openwakeword.model import Model as _OWWModel

    _HAS_WAKEWORD = True
except Exception as e:
    _OWWModel = None
    _HAS_WAKEWORD = False
    _optional_import_failed("openwakeword", e)

try:
    import pyaudio as _pyaudio

    _HAS_PYAUDIO = True
except Exception as e:
    _pyaudio = None
    _HAS_PYAUDIO = False
    _optional_import_failed("pyaudio", e)

try:
    import audioop as _audioop

    _HAS_AUDIOOP = True
except ImportError:
    _audioop = None
    _HAS_AUDIOOP = False

import queue

from config import Config

logger = logging.getLogger(__name__)

# Rate-limited logging for hot-path catches (_apply_aec, is_wake_word_detected,
# is_user_speaking): these run on every mic chunk / poll, so a persistent
# failure would otherwise flood the log. At most one line per function name
# per _RATE_LIMITED_LOG_INTERVAL_S seconds.
_RATE_LIMITED_LOG_INTERVAL_S = 30.0
_rate_limited_last_logged: dict = {}


def _log_rate_limited(func_name: str, msg: str, *args, exc_info: bool = False) -> None:
    now = time.monotonic()
    last = _rate_limited_last_logged.get(func_name)
    if last is not None and (now - last) < _RATE_LIMITED_LOG_INTERVAL_S:
        return
    _rate_limited_last_logged[func_name] = now
    logger.warning(msg, *args, exc_info=exc_info)


class TranscriptionResult(str):
    """
    A plain `str` subclass carrying an extra `.confidence` float
    attribute (0.0-1.0) alongside the transcribed text itself.

    WHY A str SUBCLASS (backward compatibility): every existing caller of
    SpeechToText.listen()/._transcribe() treats the return value as a
    plain string (`if not text:`, `.strip()`, `.lower()`, regex
    `.search()`, comparisons, f-strings, etc.). Because TranscriptionResult
    IS-A str, every one of those usages keeps working unchanged. New code
    that wants the confidence value reads `.confidence` off the same
    object.
    """

    def __new__(
        cls, text: str, confidence: float = 0.0, speech_rate: float = 0.0
    ) -> "TranscriptionResult":
        obj = super().__new__(cls, text)
        obj.confidence = float(confidence)
        obj.speech_rate = float(speech_rate)
        return obj

# ══════════════════════════════════════════════════════════════════════
# Audio Math & Utilities
# ══════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════
# AEC input queue tuning
# ══════════════════════════════════════════════════════════════════════

_AEC_QUEUE_MAXSIZE = 100
_AEC_QUEUE_IDLE_POLL_S = 0.5

# ══════════════════════════════════════════════════════════════════════
# Live mic audio-level push (GUI orb — js/home.js 'ev:audio_level')
# ══════════════════════════════════════════════════════════════════════
# Small and lossy by design, same reasoning as _AEC_QUEUE_MAXSIZE above:
# dropping an occasional chunk just means one skipped orb update, which
# is invisible at 10-15 pushes/sec. The real-time mic callback thread
# only ever does a non-blocking put here (see _ingest_processed_chunk) —
# the RMS math and the throttle-to-~10-15Hz decision both happen on
# _level_worker's own background thread, never on the callback thread.
_LEVEL_QUEUE_MAXSIZE = 8
_AUDIO_LEVEL_PUSH_INTERVAL_S = float(getattr(Config, "AUDIO_LEVEL_PUSH_INTERVAL_S", 0.08))
_AUDIO_LEVEL_MIC_PEAK_RMS = float(getattr(Config, "AUDIO_LEVEL_MIC_PEAK_RMS", 4000.0))

# TUNING (accuracy — reduces hallucination on near-silent input): below
# this raw peak amplitude (out of 1.0), a capture is treated as
# effectively silence/noise-floor rather than quiet speech. The old
# unconditional auto-gain (up to 8x) amplified a nearly-silent clip —
# room tone, fan noise, a faint HVAC hum — into a "loud" signal, which is
# exactly the kind of input Whisper is known to hallucinate boilerplate
# phrases on (see _is_known_hallucination()'s docstring in helpers.py for
# the established phrase list this project already guards against).
# Skipping the gain below this floor means such clips reach the model at
# their true (very quiet) level, which correlates with a genuinely higher
# no_speech_prob and gets caught earlier by the existing no_speech_thr
# filter instead of being artificially amplified into a false detection.
_MIN_GAIN_APPLY_PEAK = 0.02

# TUNING (accuracy — avoids biasing pure-English transcription toward
# Hindi words): static, non-echoing style-guidance prompt used ONLY when
# the turn is explicitly forced to English (manual STT_LANGUAGE == "en",
# or SARA_LANGUAGE == "english" with no other override in play -- see
# _get_transcribe_prompt() below).
_STATIC_TRANSCRIBE_PROMPT_EN = (
    "Transcribe naturally in English without translating or paraphrasing, "
    "keep proper names exactly as spoken, do not invent words."
)

# TUNING (accuracy): matches core_wiring.py's run_sara_logic() own
# post_tts_settle_s value (0.3s when AEC is active, else the flat
# STT_SETTLE_MIN_GAP_S) -- used only as wait_settle()'s fallback when a
# caller omits min_gap explicitly. core_wiring.py always passes min_gap
# explicitly today, so this was previously dead-code-in-practice; kept
# consistent here so any other/future caller gets the same AEC-aware
# behavior instead of silently over- or under-settling.
_POST_TTS_SETTLE_WITH_AEC_S = 0.3


def _load_whisper_model_offline_first(**kwargs) -> "WhisperModel":
    """
    Load a faster-whisper model without the online HF-hub revision-check
    when the model is already cached locally (the common case after the
    very first run). huggingface_hub normally makes a network call to
    check for a newer revision on every single load, even when nothing
    changed -- this adds real startup latency and, if that request is
    still in flight when the interpreter starts tearing down (e.g. a fast
    restart), can fail outright ("cannot schedule new futures after
    interpreter shutdown").

    Strategy: ask for a cache-only load first, then retry once allowing a
    download, so first-run behaviour is unchanged.

    D34 FIX -- this used to express "cache only" by mutating the global
    HF_HUB_OFFLINE environment variable and restoring it afterwards. That
    had two defects:

    1. IT WAS NOT THREAD-SAFE, and this function is genuinely called
       concurrently. SpeechToText.__init__ loads three models in ONE
       ThreadPoolExecutor -- _load_faster_whisper, _load_wakeword and
       _load_fast_wake_whisper -- and two of those reach this function. So
       thread A would set the var to "1", thread B would restore it to
       absent, and A's retry would then run under whatever B had left
       behind. Which attempt saw which setting was a race.
    2. It could not work as documented anyway. faster_whisper imports
       huggingface_hub at MODULE level (faster_whisper/utils.py), and
       huggingface_hub reads HF_HUB_OFFLINE into a module constant when it
       is imported. By the time this function runs, that value is already
       frozen, so mutating os.environ here changes nothing -- the
       "offline first, online retry" dance was reading as deliberate
       behaviour while actually being inert.

    faster_whisper's WhisperModel takes `local_files_only` as a first-class
    argument (verified against faster-whisper 1.0.3), which is per-call,
    thread-safe, and actually honoured. So that is what is used now. No
    global state is touched.

    NOTE ON A FAILURE THIS CANNOT FIX: if HF_HUB_OFFLINE is truthy in the
    real environment when the process starts, huggingface_hub is offline
    for the whole run and `local_files_only=False` cannot override it. A
    model that was never downloaded then cannot be fetched, and the caller
    is left with None. That is not something this function should paper
    over -- see _load_faster_whisper, which now reports it loudly with the
    remedy instead of letting SARA come up as a voice assistant that can
    never transcribe.
    """
    try:
        return WhisperModel(local_files_only=True, **kwargs)
    except Exception:
        # Not cached (or the cache entry is unusable): allow the download.
        return WhisperModel(local_files_only=False, **kwargs)


# ══════════════════════════════════════════════════════════════════════
# Main STT Engine
# ══════════════════════════════════════════════════════════════════════


class SpeechToText:
    # See sara/core/llm/engine.py (SaraLLM._serializable) -- self.ears is
    # exposed directly off the Api object, so this stops pywebview's js_api
    # bridge from recursing into the live Whisper model / audio stream.
    _serializable = False

    SAMPLE_RATE: int = 16000
    CHUNK_SIZE: int = 512
    SAMPLE_WIDTH: int = 2
    PRE_SPEECH_MS: int = 300

    # Bounded rolling live-preview window (seconds). The provisional
    # "live caption" preview transcribes only this many trailing seconds
    # of the current utterance, maintained via a genuinely bounded
    # rolling structure (see _collect_speech()'s preview_chunks deque)
    # rather than by rebuilding/slicing the full accumulated buffer each
    # cycle. Preview cost stays roughly constant as the utterance gets
    # longer. The FINAL, accurate transcription (in listen()/
    # _transcribe()) is completely unaffected and always uses the full
    # captured audio (speech_chunks).
    PREVIEW_WINDOW_S: float = 6.0

    # v8: first recalibration happens promptly (AEC/NS shifts the ambient
    # noise profile immediately at startup, so the energy threshold
    # shouldn't wait the full steady-state interval to adapt once).
    _RECALIB_FIRST_INTERVAL: float = 15.0
    _RECALIB_INTERVAL: float = 300.0

    # v8.1: mic-disconnect watchdog poll interval.
    _WATCHDOG_INTERVAL: float = 7.0

    def __init__(self, aec=None, on_audio_level=None) -> None:
        """
        aec: optional sara.audio.aec.AECProcessor instance, shared with the
        TextToSpeech engine, constructed once in build_core_objects(). Raw
        mic chunks are handed off to a dedicated background worker thread
        which runs them through aec.process_near_end() before they reach
        the pre-speech/ring buffers — never inline on the real-time audio
        callback thread. If omitted, behaves exactly as before (no
        cancellation, zero added overhead).

        on_audio_level: optional callback(level: float) -- 0.0-1.0 mic
        RMS, throttled to ~10-15Hz and pushed only while an actual
        command/dictate listen() session is active (see
        _ingest_processed_chunk/_level_worker below). Called from a
        dedicated background thread, never from the real-time audio
        callback. If omitted, behaves exactly as before (no level
        tracking, zero added overhead).
        """
        self._closed = False
        self._aec = aec
        self._on_audio_level = on_audio_level
        self._level_q: "queue.Queue[bytes]" = queue.Queue(maxsize=_LEVEL_QUEUE_MAXSIZE)

        self._threshold_lock = threading.Lock()
        self._energy_threshold: float = float(
            getattr(Config, "BARGE_IN_ENERGY_THRESHOLD", 500)
        )
        self._manual_threshold_until: float = 0.0

        self._silence_gate = _SilenceGate()
        self._noise_floor = _NoiseFloor()
        # BUGFIX: was hardcoding aggressiveness=2 here, which silently
        # defeated _VADFilter's own Config.VAD_AGGRESSIVENESS fallback
        # (see the BUGFIX comment on _VADFilter.__init__ in buffers.py) --
        # any .env tuning of VAD_AGGRESSIVENESS had zero effect on this
        # instance, which is the one actually used everywhere (speech
        # collection, barge-in detection, is_user_speaking). Passing
        # nothing lets Config.VAD_AGGRESSIVENESS (default 2, same as
        # before) actually take effect.
        self._vad = _VADFilter(self.SAMPLE_RATE)

        self._pre_buf = _PreBuffer(
            self.SAMPLE_RATE, self.CHUNK_SIZE, self.PRE_SPEECH_MS
        )
        self._ring = _RingBuffer(maxlen=300)

        self._recent_transcript: str = ""
        self._transcript_lock = threading.Lock()

        self._detected_lang: str = "en"

        self._wakeword_last_triggered: float = 0.0
        self._wakeword_last_inference: float = 0.0

        # Temporary one-shot diagnostics for wake pipeline isolation.
        self._wake_diag_audio_seen = False
        self._wake_diag_inference_seen = False

        # TEMP DIAGNOSTIC: peak-score-per-second tracker for
        # is_wake_word_detected() (see there). Initialized here so the
        # elapsed-time check has a real starting timestamp on the very
        # first call, instead of recomputing "now" as its own default
        # every call.
        self._wake_diag_window_start = time.monotonic()
        self._wake_diag_window_max = 0.0

        self._wakeword_cooldown: float = float(
            getattr(Config, "WAKE_WORD_COOLDOWN_S", 2.0)
        )

        # Dedicated RAW microphone buffer for OpenWakeWord.
        # Do NOT feed AEC/NS processed audio to the wake model.
        self._wake_ring = _RingBuffer(maxlen=50)

        # Sequential audio accumulator for OpenWakeWord.
        # IMPORTANT: _wake_ring is drained only once per wake check.
        # OpenWakeWord.predict() maintains its own streaming state, so we
        # must never repeatedly submit the same rolling audio window.
        self._wake_pending = bytearray()

        self._wake_variants = self._build_wake_variants()
        self._wake_re = self._compile_wake_regex(self._wake_variants)

        self._recalib_event = threading.Event()
        self._watchdog_event = threading.Event()  # v8.1: mic-disconnect watchdog
        self._tts_active = threading.Event()
        self._tts_stopped_at: float = 0.0
        self._tts_state_lock = threading.Lock()

        # BARGE-IN HANDOFF (see capture_barge_in_audio()/_collect_speech()
        # below): audio snapshotted by capture_barge_in_audio() at the
        # instant a barge-in interrupts TTS. A small, separate buffer --
        # NOT part of self._ring -- so mark_tts_stopped()/wait_settle()
        # can keep unconditionally clearing self._ring exactly as before
        # without also discarding this. Guarded by the existing
        # self._tts_state_lock above (already used for all other
        # TTS-transition state on this class) rather than a new lock.
        # List of raw mic-callback-sized chunks, NOT joined into one
        # blob, so _collect_speech() can hand it straight to its normal
        # per-chunk VAD scan -- webrtcvad requires fixed-size frames and
        # would choke on one giant merged blob.
        self._barge_in_handoff: List[bytes] = []

        self._is_listening = threading.Event()

        # v7: guards listen() so only one _collect_speech()/_transcribe()
        # session can ever be active at a time on this instance.
        self._listen_lock = threading.Lock()

        # Guards the best-effort "live caption" preview transcribe (see
        # _spawn_preview_transcribe()/_collect_speech() below) so only one
        # preview transcribe is ever in flight at a time -- prevents CPU
        # pile-up if previews start taking longer than the ~900ms cadence
        # between them.
        self._preview_busy = threading.Event()

        # Monotonically increasing generation counter, incremented once
        # per _collect_speech() session (see _collect_speech() below). A
        # preview transcription started during an OLDER session captures
        # the generation value at spawn time; if a NEWER session has
        # since started by the time that preview finishes, its result is
        # discarded instead of being delivered to on_partial_transcript --
        # this stops a stale preview from updating the UI for an
        # already-finished utterance. Only ever mutated/read from the
        # main STT thread (_collect_speech) and read from the daemon
        # preview thread it spawns; a plain int is sufficient here since
        # CPython attribute reads/increments of this kind don't need an
        # extra lock for this single-writer usage pattern.
        self._preview_generation = 0

        # v7: raw mic chunks awaiting AEC processing on a background
        # thread (only used when aec is not None — see _audio_callback).
        self._aec_raw_q: "queue.Queue[bytes]" = queue.Queue(maxsize=_AEC_QUEUE_MAXSIZE)
        self._aec_drop_count = 0

        # Load models directly into VRAM.
        #
        # TUNING (startup latency): these three loads are fully
        # independent -- separate model objects (main Whisper, the
        # OpenWakeWord ONNX classifier, and the small "tiny" Whisper --
        # see its own role below), no shared state, no cross-dependency
        # -- so they run concurrently instead of one-after-another.
        # Previously total time-to-ready was the SUM of all three load
        # times (each doing real disk I/O and, for the two Whisper
        # models, a CTranslate2 session build); now it's roughly the MAX
        # of the three, since they overlap. Assignment to self only
        # happens AFTER all three futures resolve (via .result()), so
        # nothing below this block (mic stream open, background threads,
        # _log_init) ever sees a partially-loaded model set -- behavior
        # is otherwise identical to the old sequential version, just
        # faster to reach. The only observable side effect is that the
        # three loaders' own print() messages may interleave in the
        # console instead of appearing strictly in order -- cosmetic
        # only.
        #
        # The tiny model loaded below is used for BOTH the STT-fallback
        # wake-word check in is_wake_word_detected() when no custom
        # OpenWakeWord model is configured, AND the live-caption preview
        # in _spawn_preview_transcribe() (always, regardless of whether
        # a custom OWW model is configured) -- see _load_fast_wake_whisper()
        # for the full history/reasoning. Failure to load it is
        # non-fatal: is_wake_word_detected() falls back to the main
        # (larger, slower) self._whisper_model for its own use, while
        # _spawn_preview_transcribe() skips preview entirely rather than
        # falling back to the large model.
        # D36: set before the loaders run -- _load_faster_whisper sets it to
        # True once it has PROVEN the model can infer, and _warm_recognisers
        # reads it to avoid paying the first-inference cost twice.
        self._main_model_warmed = False
        try:
            from sara.audio.ducking import recover_after_crash

            _restored = recover_after_crash()
            if _restored:
                print(f"[STT] restored the volume of {_restored} app(s) that a previous run had turned down")
        except Exception:
            pass

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="stt-model-load") as _pool:
            _hinglish_future = _pool.submit(self._load_hinglish_model)
            _whisper_future = _pool.submit(self._load_faster_whisper)
            _wakeword_future = _pool.submit(self._load_wakeword)
            _wake_whisper_future = _pool.submit(self._load_fast_wake_whisper)

            self._whisper_model = _whisper_future.result()
            self._hinglish_model: Optional[object] = _hinglish_future.result()
            self._wakeword_model: Optional["_OWWModel"] = _wakeword_future.result()
            self._wake_whisper_model: Optional[object] = _wake_whisper_future.result()

        # ── D30 (audit batch D22-b): warm the recognisers before the mic ──
        # Loading a Whisper model and RUNNING one are different costs. The
        # load is what the futures above just paid; the first actual
        # transcription additionally pays CTranslate2's kernel/graph
        # bring-up, which is far larger and was being charged to whatever
        # the user said FIRST -- the worst possible moment, because someone
        # who waits several seconds concludes it is broken and repeats
        # themselves, and now two turns overlap.
        #
        # prewarm.py pre-synthesises TTS phrases but there was no STT
        # equivalent anywhere in this file, so nothing absorbed that cost.
        # One throwaway recognition on silence moves it to boot, where a
        # progress bar is already showing and nobody is waiting on a reply.
        #
        # PLACEMENT IS THE SAFETY ARGUMENT: this runs inside __init__,
        # after the models are assigned and BEFORE _open_mic_stream() and
        # _start_threads(). No caller holds a reference to self yet, so no
        # listen() or preview transcribe can be in flight and the models
        # cannot be used concurrently -- which is why this needs no lock
        # and cannot delay a real command. A background thread would have
        # raced the orchestrator's first listen() on the same model object.
        self._warm_recognisers()

        self._stream = None
        self._pa = None
        self._stream_lock = threading.Lock()
        self._open_mic_stream()

        self._start_threads()
        atexit.register(self.close)
        self._log_init()

    def _warm_recognisers(self) -> None:
        """
        One throwaway transcription per loaded model, so the first real
        command does not pay the recogniser's first-run cost (D30).

        Both models are warmed because both are on the first command's
        critical path: `_whisper_model` does the command itself, and
        `_wake_whisper_model` (tiny) serves the live-caption preview and
        the STT wake-word fallback.

        Never raises: a warm-up failure must not stop SARA from starting.
        Returns without doing anything when disabled, so the cost can be
        removed without code changes -- set STT_WARMUP_ENABLED=False in .env
        to take this out of the startup path entirely, which is also how to
        A/B whether warming is implicated in any capture problem.

        ISOLATION (D33): this deliberately does NOT go through
        self._transcribe(). It calls the model directly with cheap settings
        and discards the result, so warming shares no state and no code path
        with a real command. See the comment on the transcribe call below.
        """
        if not bool(getattr(Config, "STT_WARMUP_ENABLED", True)):
            return
        seconds = max(0.25, float(getattr(Config, "STT_WARMUP_SECONDS", 0.25)))
        silence = np.zeros(int(self.SAMPLE_RATE * seconds), dtype=np.float32)

        targets = [
            ("main", self._whisper_model),
            ("wake", getattr(self, "_wake_whisper_model", None)),
        ]
        for label, model in targets:
            if model is None:
                continue
            # D36: _load_faster_whisper now proves the main model can infer
            # before accepting it, and that probe warms it. Warming twice
            # would just spend the first-inference cost over again.
            #
            # D52 added the same probe to the wake/preview model's GPU
            # path, so it needs the same skip -- but ONLY there. The CPU
            # fallback in _load_fast_wake_whisper does not probe, so a
            # model that came back from it still has its first-inference
            # cost unpaid and must still be warmed here.
            already = {
                "main": "_main_model_warmed",
                "wake": "_wake_model_warmed",
            }.get(label)
            if already and getattr(self, already, False):
                continue
            try:
                started = time.monotonic()
                # D33 FIX: call the model DIRECTLY rather than through
                # self._transcribe(). _transcribe() is the live command path
                # and it MUTATES INSTANCE STATE on any non-empty result:
                #
                #     self._recent_transcript = (self._recent_transcript + ...)
                #     self._update_detected_language(text)
                #
                # Whisper is well known to hallucinate on pure silence
                # ("Thank you.", boilerplate credits, and so on), and
                # _transcribe()'s hallucination filters catch many but not
                # all of them. Anything that slipped through would have set
                # self._detected_lang from garbage before the user had said
                # a single word -- and that value is read by core_wiring to
                # pick the reply language. Warming must not be able to
                # influence a later turn, so it no longer shares a code path
                # with one.
                #
                # Also much cheaper: beam_size/best_of 1 and a single
                # temperature, because the ONLY goal is to make CTranslate2
                # build its kernels. Accuracy of the discarded result is
                # irrelevant, and _transcribe()'s real settings (beam 2,
                # 3-step temperature fallback, initial_prompt) made the
                # warm-up several times slower than it needed to be -- paid
                # synchronously during boot, twice.
                #
                # DRAINING IS LOAD-BEARING: faster-whisper's transcribe()
                # returns a GENERATOR and does the work lazily. Without the
                # list() below, nothing is computed and the warm-up silently
                # does nothing at all while still appearing to succeed.
                segments, _ = model.transcribe(
                    silence,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    task="transcribe",
                    condition_on_previous_text=False,
                    vad_filter=False,
                )
                list(segments)
                elapsed_ms = (time.monotonic() - started) * 1000.0
                if getattr(Config, "DEBUG_MODE", False):
                    print(
                        f"[STT] warm-up ({label}) {elapsed_ms:.0f}ms "
                        f"on {seconds:.2f}s of silence"
                    )
            except Exception as e:  # noqa: BLE001 -- must never block boot
                print(f"[STT] warm-up ({label}) failed, continuing: {e}")

    # ── Wake word helpers ────────────────────────────────────────────

    @staticmethod
    def _build_wake_variants() -> List[str]:
        cfg_words = getattr(Config, "WAKE_WORDS", None)

        if cfg_words:
            variants = [
                w.strip().lower()
                for w in cfg_words
                if w and w.strip()
            ]
        else:
            raw = getattr(Config, "WAKE_WORD", "hey sara")
            variants = [
                w.strip().lower()
                for w in re.split(r"[,;]", raw)
                if w.strip()
            ]

        # OpenWakeWord is the authoritative passive wake detector.
        # Do NOT inject legacy variants such as "sara", "sarah",
        # or "hey sarah".
        return variants or ["hey sara"]

    @staticmethod
    def _compile_wake_regex(variants: List[str]) -> "re.Pattern":
        escaped = sorted((re.escape(v) for v in variants), key=len, reverse=True)
        pattern = r"\b(" + "|".join(escaped) + r")\b"
        return re.compile(pattern, re.IGNORECASE)

    def _text_has_wake_word(self, text: str) -> bool:
        if not text:
            return False
        if self._wake_re.search(text) is not None:
            return True

        # Fuzzy fallback: the fast/small model used for wake checks
        # sometimes mishears "sara" as something close ("sarah", "sada",
        # "zara") without matching the exact regex above. Comparing
        # against individual short words (not the whole sentence) keeps
        # this cheap and precise -- a fuzzy match against a whole
        # sentence would false-positive constantly, but a single word
        # rarely coincidentally resembles "sara" this closely. Only
        # single-word variants are fuzzy-matched (phrases like "hey
        # sara" still need the exact regex above, or their own words to
        # line up) -- fuzzy-matching a whole phrase word-for-word here
        # would be a much looser, noisier match than intended.
        if not getattr(Config, "WAKE_FUZZY_MATCH_ENABLED", True):
            return False
        threshold = float(getattr(Config, "WAKE_FUZZY_MATCH_THRESHOLD", 0.75))
        words = re.findall(r"[a-zA-Z]+", text.lower())
        single_word_variants = [v for v in self._wake_variants if " " not in v]
        for word in words:
            if len(word) < 3:
                continue
            for variant in single_word_variants:
                ratio = difflib.SequenceMatcher(None, word, variant).ratio()
                if ratio >= threshold:
                    return True
        return False

    def _prove_model_can_infer(self, model: object) -> None:
        """
        Run one throwaway inference and let any failure propagate (D36).

        Constructing a WhisperModel proves nothing: CTranslate2 resolves its
        CUDA libraries lazily, on first use. So a partial CUDA install gives
        a model that constructs happily and then fails every single
        transcription -- and because _transcribe() catches inference errors
        and returns an empty result, the only outward symptom is "SARA
        cannot hear me".

        Raising here is the point: it lets _load_faster_whisper's existing
        GPU-to-CPU fallback actually engage, which it previously could not,
        because construction never raised.

        This also warms the model, so the work is not duplicated --
        _warm_recognisers() skips any model already proven here.
        """
        seconds = max(0.25, float(getattr(Config, "STT_WARMUP_SECONDS", 0.25)))
        silence = np.zeros(int(self.SAMPLE_RATE * seconds), dtype=np.float32)
        segments, _ = model.transcribe(  # type: ignore[attr-defined]
            silence,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            task="transcribe",
            condition_on_previous_text=False,
            vad_filter=False,
        )
        # Draining is load-bearing: transcribe() is lazy, so without this the
        # inference never runs and the proof proves nothing.
        list(segments)

    @staticmethod
    def _gpu_free_mb() -> int:
        """Free GPU memory in MB (0 when it cannot be read)."""
        try:
            import subprocess

            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
                timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).decode().split(",")
            return int(out[0]) - int(out[1])
        except Exception:
            return 0

    def _dump_utterance(self, audio_np, text: str, confidence: float) -> None:
        """STT_DEBUG_DUMP_AUDIO: keep the capture so a real-world failure can be replayed."""
        try:
            import json as _json
            import wave as _wave
            from pathlib import Path as _Path

            from config import app_data_subdir

            root = _Path(str(app_data_subdir("logs"))) / "stt_dumps"
            root.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int((time.time() % 1) * 1000):03d}"
            pcm = (np.clip(audio_np, -1.0, 1.0) * 32767).astype(np.int16)
            with _wave.open(str(root / f"{stamp}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.SAMPLE_RATE)
                w.writeframes(pcm.tobytes())
            with open(root / "index.jsonl", "a", encoding="utf-8") as f:
                f.write(_json.dumps({"file": f"{stamp}.wav", "text": text, "confidence": round(confidence, 3),
                                     "seconds": round(len(pcm) / self.SAMPLE_RATE, 2), "threshold": round(self.energy_threshold)},
                                    ensure_ascii=False) + "\n")
            wavs = sorted(root.glob("*.wav"))
            for old in wavs[:-200]:
                old.unlink(missing_ok=True)
        except Exception as e:
            print(f"[STT] could not save the debug capture: {e}")

    def _load_hinglish_model(self) -> Optional[object]:
        """Second recogniser for Hindi / Hinglish (see helpers.choose_transcript). None when off or unavailable."""
        name = str(getattr(Config, "WHISPER_HINGLISH_MODEL", "") or "").strip()
        if not name or not _HAS_WHISPER:
            return None
        device = str(getattr(Config, "WHISPER_HINGLISH_DEVICE", "cpu") or "cpu").lower()
        if device == "auto":
            device = "cuda" if self._gpu_free_mb() >= int(getattr(Config, "WHISPER_HINGLISH_GPU_MIN_FREE_MB", 900)) else "cpu"
        try:
            model = _load_whisper_model_offline_first(
                model_size_or_path=name,
                device=device,
                compute_type="int8_float16" if device == "cuda" else "int8",
                **({} if device == "cuda" else {"cpu_threads": max(4, (os.cpu_count() or 4) // 2)}),
            )
            # CTranslate2 pays its kernel bring-up on the first inference: pay it now, on silence.
            list(model.transcribe(np.zeros(8000, dtype=np.float32), beam_size=1, vad_filter=False)[0])
            print(f"[STT] Hinglish model ready: {name} ({device})")
            return model
        except Exception as e:
            print(f"[STT] Hinglish model '{name}' unavailable ({type(e).__name__}: {e}); using the main model only.")
            return None

    def _load_faster_whisper(self) -> Optional[object]:
        if not _HAS_WHISPER:
            print("[STT Error] faster-whisper not installed. Offline STT will fail.")
            return None

        model_size = getattr(Config, "WHISPER_MODEL_SIZE", "large-v3-turbo")

        cpu_threads = max(4, (os.cpu_count() or 4) // 2)

        print(f"[STT] Loading Faster-Whisper '{model_size}'...")

        try:
            if str(getattr(Config, "WHISPER_MAIN_DEVICE", "cuda") or "cuda").lower() == "cpu":
                raise RuntimeError("WHISPER_MAIN_DEVICE=cpu")
            model = _load_whisper_model_offline_first(
                model_size_or_path=model_size,
                device="cuda",
                compute_type="int8_float16",
            )
            # D36 FIX: constructing a CUDA WhisperModel does NOT touch the
            # CUDA libraries -- CTranslate2 resolves them lazily, on the
            # first inference. So on a machine with a partial CUDA install
            # the construction above SUCCEEDS, this branch reported
            # "loaded on GPU", and every transcription then failed with
            # "Library cublas64_12.dll is not found or cannot be loaded".
            # The except-clause below never ran, because nothing raised
            # here -- so the CPU fallback could not engage and SARA kept a
            # model that cannot infer. Observed in the field exactly this
            # way; the symptom is indistinguishable from "SARA cannot hear
            # me", because _transcribe() swallows the inference error and
            # returns an empty transcript.
            #
            # A load is therefore only accepted once it has been PROVEN to
            # infer. The probe doubles as the warm-up (see
            # _warm_recognisers), so this costs nothing extra.
            self._prove_model_can_infer(model)
            print("[STT] ✅ Faster Whisper loaded on GPU (CUDA, int8_float16).")
            self._main_model_warmed = True
            # D37: record what was ACTUALLY accepted, so _log_init can report
            # the real device instead of a hardcoded string.
            self._model_device = f"{model_size} on GPU (CUDA, int8_float16)"
            return model

        except Exception as gpu_error:
            if "WHISPER_MAIN_DEVICE=cpu" in str(gpu_error):
                print("[STT] Main model on the CPU as configured (WHISPER_MAIN_DEVICE=cpu).")
            else:
                print(f"[STT Warning] GPU initialization failed ({gpu_error}) -- falling back to CPU.")
            try:
                model = _load_whisper_model_offline_first(
                    model_size_or_path=model_size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=cpu_threads,
                )
                # Same proof on the CPU path: a model that cannot infer is
                # not a usable model, whichever device it claims.
                self._prove_model_can_infer(model)
                print("[STT] ✅ Faster Whisper loaded on CPU (fallback).")
                self._main_model_warmed = True
                self._model_device = f"{model_size} on CPU (int8, {cpu_threads} threads)"
                return model
            except Exception as cpu_error:
                print(f"[STT Error] CPU initialization failed:\n{cpu_error}")
                self._report_no_stt_model(model_size, cpu_error)
                return None

    @staticmethod
    def _report_no_stt_model(model_size: str, error: Exception) -> None:
        """
        D35: say plainly that speech recognition is dead, and how to fix it.

        Without this, a failed model load printed one `[STT Error]` line and
        SARA carried on to print "[STT] Ready — FasterWhisper=✗ | VAD=✓ |
        ..." -- a line whose shape reads like success, with the one symbol
        that matters buried among ticks. _transcribe() then returns an empty
        result on its `model is None` guard for every single turn, so the
        microphone, the gate, VAD, the wake word and the GUI level meter all
        behave perfectly while nothing is ever transcribed. That is the
        hardest possible failure to attribute from the outside, and it is
        what made this cost days rather than minutes.

        A voice assistant that cannot transcribe has lost its primary
        function. It should say so, in terms that name the remedy.
        """
        text = str(error)
        offline = "HF_HUB_OFFLINE" in text or "outgoing traffic has been disabled" in text
        print("")
        print("  " + "=" * 70)
        print("  SPEECH RECOGNITION IS UNAVAILABLE -- SARA CANNOT HEAR COMMANDS")
        print("  " + "=" * 70)
        print(f"  The faster-whisper '{model_size}' model could not be loaded, so")
        print("  every spoken command will produce an empty transcript. Wake word,")
        print("  playback and the microphone level meter will still appear to work.")
        print("")
        if offline:
            print("  CAUSE: the model is not in the local Hugging Face cache, and")
            print("         offline mode is active, so it cannot be downloaded.")
            print("")
            print("  FIX (run once, with a working connection):")
            print("      $env:HF_HUB_OFFLINE=0")
            print(f"      python -c \"from faster_whisper import WhisperModel;"
                  f" WhisperModel('{model_size}', device='cpu', compute_type='int8')\"")
            print("")
            print("  Then make sure HF_HUB_OFFLINE is not set to 1 in your")
            print("  environment, or SARA will not be able to load it either:")
            print("      [Environment]::GetEnvironmentVariable('HF_HUB_OFFLINE','User')")
        else:
            print("  Check that the model name in WHISPER_MODEL_SIZE is valid and")
            print("  that there is room on disk for it.")
        print("  " + "=" * 70)
        print("")
    def _load_fast_wake_whisper(self) -> Optional[object]:
        """
        Small/fast dedicated Whisper model used for the STT-fallback
        wake-word check (is_wake_word_detected()) AND the live-caption
        preview (_spawn_preview_transcribe()). Failure here is non-fatal
        for wake detection -- is_wake_word_detected() falls back to the
        main (larger, slower) self._whisper_model if this is None, so
        wake detection still works either way, just without the latency
        win. For preview, failure here means preview is skipped entirely
        rather than falling back to the large model (see
        _spawn_preview_transcribe()).
        """
        if not _HAS_WHISPER:
            return None
        model_size = getattr(Config, "WAKE_WORD_FAST_MODEL_SIZE", "tiny")
        cpu_threads = max(2, (os.cpu_count() or 4) // 4)
        print(f"[STT] Loading dedicated wake-word Whisper '{model_size}'...")

        # D52: try the GPU first, exactly as _load_faster_whisper does.
        #
        # This model is not only the wake probe -- it also runs the live
        # LIVE-CAPTION PREVIEW, and that preview's partial transcript is
        # what the adaptive endpoint reads to recognise a finished command.
        # Measured on the target machine (D51): the partial arrived at
        # ~640ms of silence when it has to arrive within ENDPOINT_MIN_MS
        # (350ms) to be useful, so the "complete command" fast path never
        # fired and every recognised command waited `base` (625ms) instead
        # of 350ms. 288ms per command, spent waiting for an inference that
        # was pinned to the CPU by a literal while the main model ran on
        # CUDA on the same machine.
        #
        # Safety: the probe below means a GPU that cannot actually infer is
        # rejected (CTranslate2 resolves its CUDA libraries lazily, so
        # construction alone proves nothing -- that is D36). On any failure
        # this falls through to the original CPU path, so the worst case is
        # exactly today's behaviour.
        if bool(getattr(Config, "WAKE_MODEL_USE_GPU", True)):
            try:
                model = _load_whisper_model_offline_first(
                    model_size_or_path=model_size,
                    device="cuda",
                    compute_type="int8_float16",
                )
                self._prove_model_can_infer(model)
                print(
                    "[STT] ✅ Wake-word Whisper loaded on GPU "
                    "(CUDA, int8_float16) -- live preview and wake checks "
                    "share it."
                )
                self._wake_model_device = f"{model_size} on GPU (CUDA, int8_float16)"
                # The probe above already ran one inference on this model,
                # so _warm_recognisers() must not pay that cost again.
                self._wake_model_warmed = True
                return model
            except Exception as gpu_error:
                print(
                    f"[STT] Wake-word Whisper GPU load failed "
                    f"({gpu_error}) -- falling back to CPU."
                )

        try:
            model = _load_whisper_model_offline_first(
                model_size_or_path=model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
            )
            print("[STT] ✅ Wake-word Whisper loaded on CPU.")
            self._wake_model_device = f"{model_size} on CPU (int8, {cpu_threads} threads)"
            return model
        except Exception as e:
            print(
                f"[STT Warning] Dedicated wake-word model load failed "
                f"({e}) -- wake checks will use the main model instead, "
                f"and live preview will be skipped."
            )
            return None

    def _load_wakeword(self) -> Optional["_OWWModel"]:
        """
        Load the passive wake detector.

        OpenWakeWord is the ONLY passive wake detection mechanism.
        Whisper/STT fallback is intentionally not allowed.
        """
        if not _HAS_WAKEWORD or not _OWWModel:
            print(
                "[STT Warning] OpenWakeWord is unavailable. "
                "Passive wake detection is disabled."
            )
            return None

        model_path = getattr(Config, "WAKE_WORD_MODEL_PATH", None)

        if not model_path:
            print(
                "[STT Warning] WAKE_WORD_MODEL_PATH is not configured. "
                "Passive wake detection is disabled."
            )
            return None

        model_path = str(model_path)

        if not os.path.isabs(model_path):
            model_path = str(
                Path(__file__).resolve().parents[3] / model_path
            )

        if not os.path.exists(model_path):
            print(
                f"[STT Warning] OpenWakeWord model not found: "
                f"{model_path}. Passive wake detection is disabled."
            )
            return None

        try:
            model = _OWWModel(
                wakeword_models=[model_path],
                inference_framework="onnx",
            )

            print(
                "[STT] ✅ OpenWakeWord loaded successfully: "
                f"{model_path}"
            )

            # Startup test inference is intentionally silent.
            # Successful model loading is already reported above.

            return model

        except Exception as e:
            print(
                f"[STT Warning] OpenWakeWord model load failed: {e}. "
                "Passive wake detection is disabled."
            )
            return None

    @property
    def energy_threshold(self) -> float:
        with self._threshold_lock:
            return self._energy_threshold

    @energy_threshold.setter
    def energy_threshold(self, value: float) -> None:
        with self._threshold_lock:
            self._energy_threshold = value

    def set_manual_energy_threshold(
        self, value: float, suppress_recalib_s: float = 600.0
    ) -> None:
        """User-driven (GUI slider) sensitivity change. Unlike the plain
        energy_threshold setter (also used internally by auto-recalibration),
        this suppresses _run_one_recalibration() from silently overwriting
        the value for `suppress_recalib_s` seconds, so it actually sticks."""
        with self._threshold_lock:
            self._energy_threshold = value
            self._manual_threshold_until = time.monotonic() + suppress_recalib_s

    def get_detected_language(self) -> str:
        with self._transcript_lock:
            return self._detected_lang

    def set_tts_active(self, active: bool) -> None:
        if active:
            self._ambient_rms = self._recent_ambient_rms()
            self._tts_active.set()
            self._pre_buf.clear()
        else:
            self._tts_active.clear()

    def mark_tts_stopped(self) -> None:
        with self._tts_state_lock:
            self._tts_stopped_at = time.monotonic()
        self._pre_buf.clear()
        # Still unconditionally cleared -- this is now only ever TTS
        # echo-tail / settle-window noise. Any audio that actually
        # mattered (a barge-in interruption) was already copied out to
        # self._barge_in_handoff by capture_barge_in_audio() BEFORE this
        # runs (see TTSWorker._watch_loop() in tts_worker.py), and that
        # buffer lives separately from self._ring, so it survives this
        # clear untouched.
        self._ring.get_all(clear=True)

    def wait_settle(self, min_gap: Optional[float] = None) -> None:
        if min_gap is None:
            # TUNING: AEC-aware fallback (see _POST_TTS_SETTLE_WITH_AEC_S
            # module-level comment) instead of always using the flat
            # STT_SETTLE_MIN_GAP_S regardless of whether AEC is active.
            aec_active = self._aec is not None and getattr(
                self._aec, "enabled", False
            )
            min_gap = (
                _POST_TTS_SETTLE_WITH_AEC_S
                if aec_active
                else float(getattr(Config, "STT_SETTLE_MIN_GAP_S", 1.3))
            )
        with self._tts_state_lock:
            stopped_at = self._tts_stopped_at
        if stopped_at <= 0:
            return
        remaining = min_gap - (time.monotonic() - stopped_at)
        if remaining > 0:
            time.sleep(remaining)
        # Same as mark_tts_stopped() above -- unconditional clear is
        # fine here too: self._barge_in_handoff (if a barge-in captured
        # anything) lives in its own buffer and is untouched by this.
        self._ring.get_all(clear=True)
        self._pre_buf.clear()

    def capture_barge_in_audio(self) -> None:
        """Called by TTSWorker._watch_loop() at the exact instant a
        barge-in interrupts TTS (right after self._voice.stop(), before
        self._barge_stop is set -- see there) -- i.e. BEFORE
        mark_tts_stopped() gets a chance to run (that happens shortly
        after, as _speak_blocking()/_speak_stream_blocking() unwind from
        voice.stop() above) and clear self._ring.

        Snapshots (copies, does NOT drain) whatever's currently sitting
        in self._ring: with STT_HARD_MUTE_DURING_TTS (the default -- see
        _ingest_processed_chunk()), that's exactly the audio that already
        passed _passes_barge_in_gate() while TTS was active, i.e. the
        start of what the user is interrupting with. Stashed as a list of
        raw chunks (not joined into one blob) in a small dedicated
        handoff buffer -- self._ring itself is left alone so it keeps
        accumulating normally for the rest of this TTS-stopping window
        (is_user_speaking() etc. still reads off it) -- so
        mark_tts_stopped()/wait_settle() can keep unconditionally
        clearing self._ring exactly as before without losing this.
        _collect_speech() below consumes (and clears) this buffer at the
        start of the very next listen() session, prepending it to that
        session's own backlog so the interruption becomes the start of
        the user's next command instead of being thrown away.

        Defensive/never raises, matching this class's other TTS-thread-
        facing methods (e.g. _ears_is_listening() in tts_worker.py) --
        a failure here must never be able to interfere with the actual
        barge-in (stopping TTS), which TTSWorker._watch_loop() always
        does first regardless of this call's outcome.
        """
        if self._closed:
            return
        try:
            chunks = self._ring.peek_latest(n=4)

            if not chunks:
                return

            if not getattr(self, "_wake_diag_audio_seen", False):
                self._wake_diag_audio_seen = True
                print(
                    f"[WakeDiag] RAW mic stream reached wake detector | "
                    f"chunks={len(chunks)} | "
                    f"rms={_rms(chunks[-1]):.1f}"
                )
            with self._tts_state_lock:
                # BUGFIX-adjacent hardening: extend rather than overwrite. In
                # practice _watch_loop() only calls this once per barge-in
                # (self._speaking/self._voice.is_speaking() gate it off after
                # the first trigger), but extending is a one-line safety net
                # against ever silently losing a partial capture if that ever
                # changes -- no realistic downside since this list is always
                # drained (see _collect_speech()) before the next barge-in
                # could possibly refill it.
                self._barge_in_handoff.extend(chunks)
        except Exception:
            # Barge-in capture must never interfere with stopping TTS.
            return

    def _close_stream(self) -> None:
        try:
            if self._stream is not None:
                self._stream.stop() if _HAS_SD else self._stream.stop_stream()
                self._stream.close()
                self._stream = None
            if self._pa is not None:
                self._pa.terminate()
                self._pa = None
        except Exception:
            pass

    def _open_mic_stream(self) -> bool:
        with self._stream_lock:
            self._close_stream()
            if _HAS_SD:
                return self._open_sd_stream()
            return self._open_pyaudio_stream()

    def _open_sd_stream(self) -> bool:
        try:
            self._stream = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=self.CHUNK_SIZE,
                dtype="int16",
                channels=1,
                callback=self._audio_callback,
            )
            self._stream.start()
            try:
                _dev_idx = self._stream.device
                _dev_info = sd.query_devices(_dev_idx)
                print(
                    f"[STT] Mic input device (sounddevice): index={_dev_idx} "
                    f"name='{_dev_info.get('name', '?')}'"
                )
            except Exception as _dev_e:
                print(f"[STT] Could not resolve mic device info: {_dev_e}")
            return True
        except (sd.PortAudioError, OSError, ValueError) as e:
            print(f"[STT Error] sounddevice open failed: {e}")
            return False
        except Exception as e:
            logger.exception(
                "[STT] unexpected error type opening the sounddevice stream "
                "(this may be a bug): %s", e
            )
            return False

    def _open_pyaudio_stream(self) -> bool:
        if not _HAS_PYAUDIO:
            return False
        try:
            self._pa = _pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=_pyaudio.paInt16,
                channels=1,
                rate=self.SAMPLE_RATE,
                input=True,
                frames_per_buffer=self.CHUNK_SIZE,
                stream_callback=self._pa_callback,
            )
            self._stream.start_stream()
            try:
                _dev_info = self._pa.get_default_input_device_info()
                print(
                    f"[STT] Mic input device (pyaudio): index={_dev_info.get('index', '?')} "
                    f"name='{_dev_info.get('name', '?')}'"
                )
            except Exception as _dev_e:
                print(f"[STT] Could not resolve mic device info: {_dev_e}")
            return True
        except (OSError, ValueError) as e:
            print(f"[STT Error] PyAudio open failed: {e}")
            return False
        except Exception as e:
            logger.exception(
                "[STT] unexpected error type opening the PyAudio stream "
                "(this may be a bug): %s", e
            )
            return False

    def _apply_aec(self, chunk: bytes) -> bytes:
        if self._aec is None:
            return chunk
        try:
            return self._aec.process_near_end(chunk)
        except (ValueError, OSError) as e:
            _log_rate_limited(
                "_apply_aec",
                "[STT] AEC process_near_end failed (passing raw chunk through): %s: %s",
                type(e).__name__, e,
            )
            return chunk
        except Exception as e:
            _log_rate_limited(
                "_apply_aec",
                "[STT] AEC process_near_end raised an unexpected error type "
                "(this may be a bug; passing raw chunk through): %s: %s",
                type(e).__name__, e,
                exc_info=True,
            )
            return chunk

    def _barge_in_threshold(self) -> float:
        """
        Energy a chunk must beat to count toward interrupting SARA.

        D63: this used to be `self.energy_threshold` directly, and D65
        broke that by making the threshold track the ROOM NOISE. Barge-in
        and capture need different quantities:

          * the CAPTURE gate must sit above room noise, so in a noisy room
            it has to rise -- that is exactly what D65 does
          * barge-in must clear SARA's own TTS ECHO BLEED, which is bounded
            by playback level and has nothing to do with room noise

        Multiplying a noise-scaled threshold by TTS_BLEED_GUARD_MULTIPLIER
        (1.6, tuned against a FIXED threshold) conflated them, and made
        interruption harder precisely where it was already hardest.
        Measured against the seven floor/speech pairs from the target
        machine, with BARGE_IN_ENERGY_THRESHOLD at its 600 default:

            floor  speech   D65 gate   margin
              164    2337       2971    0.79x   <-- INTERRUPTION IMPOSSIBLE
              203    3082       2971    1.04x
              125    4996       2286    2.19x

        At floor 164 the gate sat ABOVE the user's own speech, so they
        could not have interrupted SARA at all in that room.

        The cap is the fix: barge-in may become EASIER than the configured
        base when calibration lowers the threshold in a quiet room, but
        never HARDER. That restores the pre-D65 ceiling exactly -- the
        value that was observably working, since telemetry recorded
        barge_in outcomes -- while leaving D65's capture-gate fix intact.

            floor  speech   capped gate   margin
              164    2337           960    2.43x
              203    3082           960    3.21x
              125    4996           960    5.20x

        Config.BARGE_IN_ENERGY_THRESHOLD is literally the field named for
        this job; `energy_threshold` is the calibrated capture value.
        """
        base = float(getattr(Config, "BARGE_IN_ENERGY_THRESHOLD", 600))
        return min(float(self.energy_threshold), base)

    def _recent_ambient_rms(self) -> Optional[float]:
        """Room level just before SARA starts talking: the 20th-percentile
        RMS of the last ~3 s of mic audio. The user's own command and the
        wait for the reply are both in that window; the low percentile
        lands in the gaps between words, i.e. on the background (fan,
        traffic, music). Unlike _NoiseFloor, which only samples chunks
        below 40 % of the capture threshold and so sees nothing at all in
        a loud room, this stays meaningful exactly when the room is loud.
        None when there is too little audio to judge."""
        try:
            n = int(self.SAMPLE_RATE / self.CHUNK_SIZE * 3.0)
            chunks = self._ring.peek_latest(n=n)
            if not chunks or len(chunks) < 8:
                return None
            energies = sorted(_rms(c) for c in chunks)
            return float(energies[len(energies) // 5])
        except Exception:
            return None

    def _barge_in_effective_threshold(self, boost: float = 1.0) -> float:
        """Energy a chunk must beat to interrupt SARA while she speaks.

        The configured/calibrated threshold times the echo-bleed guard, as
        before -- and, new, never less than BARGE_IN_NOISE_RATIO x the room
        level captured when TTS started. E2E test 2026-10-29: the room sat
        at RMS ~664 against an effective gate of 300 x 1.6 = 480, so
        background noise the VAD took for speech cut off 73 of 77 LLM
        replies. In a quiet room (floors 125-203 measured on this machine)
        3x the floor stays far below real speech (2300-5000)."""
        bleed = float(getattr(Config, "TTS_BLEED_GUARD_MULTIPLIER", 1.6))
        thr = self._barge_in_threshold() * bleed * boost
        ambient = getattr(self, "_ambient_rms", None)
        ratio = float(getattr(Config, "BARGE_IN_NOISE_RATIO", 3.0))
        if ambient and ratio > 0:
            thr = max(thr, ambient * ratio)
        return thr

    def _passes_barge_in_gate(self, chunk: bytes) -> bool:
        """Elevated energy+VAD check used to decide whether a single mic
        chunk arriving while TTS is speaking is allowed into the ring
        buffer at all (see Config.STT_HARD_MUTE_DURING_TTS and
        _ingest_processed_chunk below). Deliberately reuses the SAME
        TTS_BLEED_GUARD_MULTIPLIER that is_user_speaking() already uses
        for its own (multi-chunk, majority-vote) barge-in check below —
        one config value controls "how loud/confident does an
        interruption need to be" everywhere, whether checked per-chunk
        at ingestion (here) or over a rolling window (is_user_speaking).
        This is a per-chunk gate, not a substitute for is_user_speaking's
        sustained-speech check — a single loud transient chunk passing
        this gate just means it's ALLOWED to be stored; it still takes
        is_user_speaking's majority-vote over several chunks to actually
        trigger a barge-in stop.
        """
        effective_thr = self._barge_in_effective_threshold()
        if _rms(chunk) <= effective_thr:
            return False
        return self._vad.is_speech(chunk)

    def _ingest_processed_chunk(self, chunk: bytes) -> None:
        # Cheap, non-blocking hand-off for the GUI orb's live level meter —
        # only while a real command/dictate listen() session is active
        # (never during idle wake-word polling, never while TTS is
        # speaking). Actual RMS math + throttling happen on _level_worker's
        # background thread, not here (this may run on the real-time mic
        # callback thread when AEC is disabled).
        if (
            self._on_audio_level is not None
            and self._is_listening.is_set()
            and not self._tts_active.is_set()
        ):
            try:
                self._level_q.put_nowait(chunk)
            except queue.Full:
                pass

        if self._tts_active.is_set():
            # BUGFIX/hardening (mic-blocking during TTS): previously every
            # chunk was written to self._ring during TTS regardless of
            # loudness — barge-in detection relied entirely on
            # is_user_speaking() filtering the ring AFTER the fact (a
            # soft, read-time threshold), and nothing structurally
            # prevented TTS/echo audio from sitting in the ring if some
            # future/other caller ever read it mid-TTS. With
            # STT_HARD_MUTE_DURING_TTS (default True), a chunk is now
            # dropped BEFORE it ever reaches the ring unless it clears
            # the same elevated barge-in bar. This changes nothing about
            # today's barge-in behavior (is_user_speaking already
            # effectively filtered out everything this drops) — it's a
            # hardening of WHERE that filtering happens, not a behavior
            # change.
            if getattr(Config, "STT_HARD_MUTE_DURING_TTS", True):
                if self._passes_barge_in_gate(chunk):
                    self._ring.put(chunk)
                return
            self._ring.put(chunk)
            return
        self._pre_buf.push(chunk)
        self._ring.put(chunk)

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if self._closed:
            return
        # D60: the stream-status report is RATE-LIMITED, because an
        # unbounded print here is self-worsening. `status` is set on
        # overflow/underflow -- i.e. exactly when the audio device is
        # already struggling -- and this is the real-time capture
        # callback, running ~31 times a second. A raw print() to a Windows
        # console costs milliseconds against a 32ms budget, so a stream in
        # trouble printed 31 lines/second FROM THE AUDIO THREAD and made
        # the trouble worse. The project's own brief is explicit: keep
        # audio-device callbacks minimal and non-blocking, no I/O.
        #
        # _log_rate_limited() already exists in this module for the AEC
        # error paths, so this reuses it rather than adding a second
        # mechanism. Still gated on DEBUG_MODE, so a normal run does
        # nothing at all here.
        if status and getattr(Config, "DEBUG_MODE", False):
            _log_rate_limited(
                "_audio_callback_status",
                "[STT] input stream status: %s (rate-limited)", status,
            )

        chunk = bytes(indata)

        # OpenWakeWord must see RAW microphone PCM.
        # AEC/NS can distort the acoustic pattern expected by the
        # custom wake-word model.
        self._wake_ring.put(chunk)

        if self._aec is None:
            self._ingest_processed_chunk(chunk)
            return

        try:
            self._aec_raw_q.put_nowait(chunk)
        except queue.Full:
            self._aec_drop_count += 1
            # D60: was `print(...)` every 50th drop. A count-based limit is
            # not a limit from the audio thread's point of view -- at ~31
            # chunks/s a sustained AEC stall still printed every ~1.6s. The
            # drop itself is counted unconditionally (so nothing is lost
            # silently); only the REPORT is throttled, on time.
            _log_rate_limited(
                "_audio_callback_aec_full",
                "[STT] AEC input queue full — dropped %d mic chunk(s) so far. "
                "Something is holding up AEC processing far longer than "
                "expected; investigate CPU/GPU load. (rate-limited)",
                self._aec_drop_count,
            )

    def _pa_callback(self, in_data, frame_count, time_info, status_flags):
        if self._closed:
            return (None, _pyaudio.paContinue if _HAS_PYAUDIO else None)

        # D60: rate-limited for the same reason as the sounddevice path
        # above -- an unbounded print from the real-time callback when the
        # stream is already struggling.
        if status_flags and getattr(Config, "DEBUG_MODE", False):
            _log_rate_limited(
                "_pa_callback_status",
                "[STT] pyaudio input status flags: %s (rate-limited)",
                status_flags,
            )

        # OpenWakeWord receives the RAW microphone signal.
        self._wake_ring.put(in_data)

        if self._aec is None:
            self._ingest_processed_chunk(in_data)
        else:
            try:
                self._aec_raw_q.put_nowait(in_data)
            except queue.Full:
                self._aec_drop_count += 1
                # D60: see the sounddevice path above.
                _log_rate_limited(
                    "_pa_callback_aec_full",
                    "[STT] AEC input queue full — dropped %d mic chunk(s) so "
                    "far (pyaudio path). (rate-limited)",
                    self._aec_drop_count,
                )

        return (None, _pyaudio.paContinue if _HAS_PYAUDIO else None)

    def _aec_worker_loop(self) -> None:
        """
        Move mic audio from the real-time callback into the ring buffer,
        applying AEC on the way.

        D61: the body is GUARDED, because this thread is the only route
        microphone audio takes to the capture path when AEC is enabled --
        which is the default, and the configuration the target machine
        runs. It is a daemon thread and nothing supervises it: the
        mic-disconnect watchdog probes the STREAM, not this worker. So a
        single unexpected exception here used to kill the thread and leave
        SARA permanently deaf, with the device still open, the callback
        still firing, the GUI level meter still moving, and NOTHING logged.

        That is the same shape as D34/D35 -- deaf with no error -- which
        cost about twelve days before it was found. _apply_aec() already
        swallows its own errors, but _ingest_processed_chunk() does not:
        it runs _passes_barge_in_gate() (numpy over the raw bytes) and the
        ring/pre-buffer writes, any of which could raise on a malformed or
        short chunk.

        One bad chunk must cost one chunk, not the microphone. The error is
        rate-limited because if it recurs it recurs ~31 times a second.
        """
        while not self._closed:
            try:
                chunk = self._aec_raw_q.get(timeout=_AEC_QUEUE_IDLE_POLL_S)
            except queue.Empty:
                continue
            try:
                processed = self._apply_aec(chunk)
                self._ingest_processed_chunk(processed)
            except Exception as e:  # noqa: BLE001 -- see docstring
                _log_rate_limited(
                    "_aec_worker_loop",
                    "[STT] AEC worker dropped one chunk (%s: %s). The worker "
                    "is STILL RUNNING; if this repeats, microphone audio is "
                    "being degraded. (rate-limited)",
                    type(e).__name__, e,
                )

    def _level_worker(self) -> None:
        """Background consumer for the live mic level meter (see
        _ingest_processed_chunk). Only started when on_audio_level is
        given (_start_threads). Does the RMS math and the ~10-15Hz
        throttling itself, so the real-time audio thread never does more
        than a lossy queue.put_nowait()."""
        last_push = 0.0
        while not self._closed:
            try:
                chunk = self._level_q.get(timeout=_AEC_QUEUE_IDLE_POLL_S)
            except queue.Empty:
                continue
            now = time.monotonic()
            if now - last_push < _AUDIO_LEVEL_PUSH_INTERVAL_S:
                continue
            last_push = now
            try:
                level = min(1.0, _rms(chunk) / _AUDIO_LEVEL_MIC_PEAK_RMS)
                self._on_audio_level(level)
            except Exception:
                pass

    def _start_threads(self) -> None:
        threading.Thread(
            target=self._recalib_loop, daemon=True, name="stt-recalib"
        ).start()
        if self._aec is not None:
            threading.Thread(
                target=self._aec_worker_loop, daemon=True, name="stt-aec-worker"
            ).start()
        if self._on_audio_level is not None:
            threading.Thread(
                target=self._level_worker, daemon=True, name="stt-audio-level"
            ).start()
        # v8.1: mic-disconnect watchdog (see v8.1 CHANGES note at top of file).
        threading.Thread(
            target=self._watchdog_loop, daemon=True, name="stt-mic-watchdog"
        ).start()

    def _recalib_loop(self) -> None:
        # v8: first cycle uses a short interval so the energy threshold
        # can adapt to AEC/NS's altered noise profile promptly instead of
        # waiting the full steady-state interval on a fresh start.
        # D47: a FAST first pass, before the 15s one. _NoiseFloor needs 5
        # samples to report a floor and the ring fills at ~31 chunks/s, so
        # ~1.5s is enough -- and it has to happen this early because the
        # old first cycle at 15s was already later than a stt_doctor run
        # lives, and later than the user's first command.
        # D71: the getattr fallback is 0.0, matching the Config default.
        # It read 1.5 while Config said 0.0, so a test double without the
        # attribute would have run a fast pass production does not run.
        # Only doubles reach the fallback -- Config always has the field --
        # but a fallback that disagrees with its own default is how a
        # behaviour comes back after being switched off.
        fast_s = float(getattr(Config, "STT_RECALIB_FIRST_FAST_S", 0.0))
        if fast_s > 0 and not self._recalib_event.wait(timeout=fast_s):
            # D69: RETRY while the engine is busy instead of giving up.
            #
            # This used to be a single attempt at fast_s, skipped silently
            # if a listen or TTS was in flight. On the target machine it was
            # skipped every time and the startup calibration never ran at
            # all: TTS warm-up takes ~3.3s there and the attempt lands at
            # 1.5s, so _tts_active was always set. The next chance is
            # _RECALIB_FIRST_INTERVAL (15s), by which point the user is
            # usually mid-command, and after that _RECALIB_INTERVAL (300s).
            # A 50-second session therefore kept the gate at whatever
            # BARGE_IN_ENERGY_THRESHOLD (or the GUI slider) last set --
            # observed as 793, gate 278 -- so D65's whole benefit was lost
            # to a one-shot that lost a race with the TTS warm-up.
            #
            # Polling for a window keeps the fast path's intent (calibrate
            # before the first command) without depending on winning that
            # race. The deadline is bounded so a permanently busy engine
            # cannot spin here forever -- it just falls through to the
            # steady-state loop as before.
            deadline = time.monotonic() + float(
                getattr(Config, "STT_RECALIB_FIRST_FAST_WINDOW_S", 12.0)
            )
            ran = False
            while not self._closed and time.monotonic() < deadline:
                if self._is_listening.is_set() or self._tts_active.is_set():
                    if self._recalib_event.wait(timeout=0.25):
                        break
                    continue
                # D61: guarded -- this is code added in this same audit, and
                # an unguarded startup call would kill the thread before the
                # steady-state loop below ever runs.
                try:
                    self._run_one_recalibration(first=True)
                    ran = True
                except Exception as e:  # noqa: BLE001
                    _log_rate_limited(
                        "_recalib_loop_first",
                        "[STT] startup calibration failed (%s: %s); "
                        "continuing with the configured threshold.",
                        type(e).__name__, e,
                    )
                break
            if not ran and not self._closed and getattr(Config, "DEBUG_MODE", False):
                # Say so. A silent skip is why this went unnoticed.
                print(
                    "[STT] startup calibration skipped: the engine stayed "
                    "busy (listening or speaking) for the whole window; the "
                    "gate keeps its configured value until the next cycle."
                )

        first_wait = min(self._RECALIB_FIRST_INTERVAL, self._RECALIB_INTERVAL)
        woken = self._recalib_event.wait(timeout=first_wait)
        if woken:
            self._recalib_event.clear()

        while not self._closed:
            # D61: guarded for the same reason as the AEC worker, one step
            # less severe -- a dead recalibration thread does not make SARA
            # deaf, it freezes the energy threshold at whatever it last
            # had, so the gate silently stops following the room. Also
            # daemon, also unsupervised.
            if not (self._is_listening.is_set() or self._tts_active.is_set()):
                try:
                    self._run_one_recalibration()
                except Exception as e:  # noqa: BLE001
                    _log_rate_limited(
                        "_recalib_loop",
                        "[STT] threshold recalibration failed (%s: %s); the "
                        "loop continues with the previous threshold. "
                        "(rate-limited)",
                        type(e).__name__, e,
                    )

            woken = self._recalib_event.wait(timeout=self._RECALIB_INTERVAL)
            if self._closed:
                break
            if woken:
                self._recalib_event.clear()

    def _run_one_recalibration(self, first: bool = False) -> None:
        """
        Re-derive the energy threshold from the measured noise floor.

        `first=True` is the one-off startup calibration (D47): it is allowed
        to move the threshold straight to the suggested value instead of
        being clamped to +/-150, because the clamp is a stability device for
        the steady state and at boot there is nothing yet to stabilise.
        """
        if time.monotonic() < self._manual_threshold_until:
            return
        chunks = self._ring.peek_latest(n=30)
        if not chunks:
            return

        thr = self.energy_threshold
        energies = [_rms(c) for c in chunks]
        avg = sum(energies) / len(energies)
        for e in energies:
            self._noise_floor.update(e, thr)

        if avg > thr * 0.5:
            # D70: say why calibration declined, instead of returning in
            # silence. This guard exists so the gate is never derived from
            # the user's own voice -- correct, and it must stay. But it
            # fires identically for SUSTAINED room audio, and then nothing
            # anywhere says the room is simply too loud to measure.
            #
            # On the target machine this hid an entire session's worth of
            # odd behaviour. A YouTube track was playing (the log shows
            # `[volume-match] title='Tu_Har_Lamha...'`) and reached the mic
            # at RMS ~980 against a start gate of 278, so captures opened on
            # the music: a 5.58s capture transcribed as 'Yes.', two Thai
            # hallucinations from a Hindi song ('ออบนคร้ม', 'โอเป็นครู' --
            # both correctly discarded by D43), a 0.12s capture, and a wake
            # score of 0.6586 barely over its 0.6 threshold. Every one of
            # those traces to the music, and nothing said so.
            #
            # NOTE the consequence, which is a real limitation and not
            # fixed here: because this bails, VOICE_GATE_NOISE_RELATIVE can
            # LOWER the gate in a quiet room but can never RAISE it in a
            # loud one -- the loud-room measurement is exactly what it
            # refuses to take. Distinguishing sustained noise from a person
            # talking needs evidence across attempts, and getting it wrong
            # raises the gate until SARA goes deaf, so it wants its own
            # batch and a measurement. Recorded as D70.
            if getattr(Config, "DEBUG_MODE", False):
                _log_rate_limited(
                    "_recalib_too_loud",
                    "[STT] calibration declined: the room averaged RMS %.0f, "
                    "over half the current threshold %.0f, so it cannot be "
                    "told apart from speech. The gate stays at %.0f (start "
                    "gate %.0f) -- anything louder than that opens a capture, "
                    "so music or a fan nearby will produce spurious "
                    "transcripts. (rate-limited)",
                    avg, thr, thr, thr * 0.35,
                )
            return

        # D49: prefer the noise-RELATIVE suggestion when enabled. The
        # additive `floor + 250` form cannot hold a margin once the caller
        # multiplies it by 0.35 -- above a floor of ~135 it puts the start
        # gate BELOW the room noise it came from, which is the D45
        # false-start condition arriving via calibration instead of via the
        # VAD. The target machine measured floors of 164 and 203.
        if bool(getattr(Config, "VOICE_GATE_NOISE_RELATIVE", False)):
            suggested = self._noise_floor.suggested_threshold_relative(
                gate_multiplier=float(
                    getattr(Config, "VOICE_GATE_NOISE_MULTIPLIER", 4.0)
                ),
                gate_min=float(getattr(Config, "VOICE_GATE_MIN_RMS", 90.0)),
                gate_max=float(getattr(Config, "VOICE_GATE_MAX_RMS", 650.0)),
            )
        else:
            suggested = self._noise_floor.suggested_threshold(margin=250.0)
        new_thr = suggested if suggested is not None else (avg + 200.0)

        # D47: the +/-150 step clamp means moving 600 down to a
        # floor-derived ~283 takes three cycles -- 15s + 300s + 300s, about
        # ten minutes -- and inside a bench/stt_doctor.py run the engine
        # does not live long enough for even the first cycle. So within any
        # session a user experiences, the threshold was effectively just
        # BARGE_IN_ENERGY_THRESHOLD. The startup calibration skips the
        # clamp so the gate reaches the right order of magnitude at once;
        # every later cycle keeps it, so ordinary drift stays gradual.
        if not first:
            new_thr = min(max(new_thr, thr - 150.0), thr + 150.0)

        self.energy_threshold = new_thr
        if first and getattr(Config, "DEBUG_MODE", False):
            floor = getattr(self._noise_floor, "floor", None)
            # Name what this supersedes. The GUI's saved mic-sensitivity
            # preference is restored moments earlier (see
            # orchestrator/history.py) and prints its own threshold, so
            # without this the two lines silently contradict each other and
            # a reader cannot tell which value is live. `moved` is the
            # change this calibration actually made, so a no-op reads as a
            # no-op rather than as an override.
            moved = abs(new_thr - thr) > 1.0
            print(
                f"[STT] startup calibration: noise floor "
                f"{('%.0f' % floor) if floor is not None else '?'} "
                f"-> threshold {new_thr:.0f} (start gate {new_thr * 0.35:.0f})"
                + (
                    f", superseding {thr:.0f}"
                    if moved
                    else " (unchanged)"
                )
            )

    # ── v8.1: mic-disconnect watchdog ────────────────────────────────
    # (see v8.1 CHANGES note at top of file — this is the only addition
    # in this revision; nothing above or below this block was touched)

    def _stream_is_healthy(self) -> bool:
        """
        Best-effort mic stream health probe.

        Deliberately NOT based on "has new audio data arrived in the ring
        buffer recently" — complete silence (nobody talking) is a normal,
        healthy state and would cause false-positive "dead stream"
        detections. Instead this probes the stream object itself: does it
        still exist, and (where the backend exposes it) does it still
        report itself as active. Any exception while probing is treated as
        an unhealthy stream so the watchdog errs on the side of
        reconnecting rather than staying silently deaf.
        """
        with self._stream_lock:
            stream = self._stream

        if stream is None:
            return False

        try:
            if _HAS_SD:
                # sounddevice streams expose an `.active` bool property.
                active = getattr(stream, "active", None)
                return bool(active) if active is not None else True
            else:
                # PyAudio streams expose an `is_active()` method.
                is_active = getattr(stream, "is_active", None)
                if callable(is_active):
                    return bool(is_active())
                return True
        except Exception:
            return False

    def _watchdog_loop(self) -> None:
        """
        Periodically probes the mic input stream and transparently
        reopens it if it has died (USB unplug, driver crash, portaudio
        callback silently stopping, etc.) so Sara doesn't go permanently
        "deaf" with no visible error. Fully fail-safe: any exception
        inside this loop is caught and logged so the watchdog thread
        itself can never crash or die.
        """
        while not self._closed:
            self._watchdog_event.wait(timeout=self._WATCHDOG_INTERVAL)
            self._watchdog_event.clear()

            if self._closed:
                break

            try:
                if not self._stream_is_healthy():
                    print(
                        "[STT WARNING] Mic input stream appears dead "
                        "(disconnected/crashed) — attempting to reopen..."
                    )
                    reopened = self._open_mic_stream()
                    if reopened:
                        print("[STT] ✅ Mic input stream reopened successfully.")
                    else:
                        print(
                            "[STT WARNING] Mic input stream reopen attempt "
                            "failed; will retry on next watchdog cycle."
                        )
            except Exception as e:
                print(f"[STT WARNING] Watchdog check failed (non-fatal): {e}")

    # ── end v8.1 watchdog block ──────────────────────────────────────

    @staticmethod
    def _endpoint_noop(_text: str) -> None:
        """Preview callback used when adaptive endpointing runs without a caller callback."""

    @staticmethod
    def _endpoint_lang() -> str:
        """Map Config.SARA_LANGUAGE to the endpointing hint: en | hinglish | hi."""
        raw = str(getattr(Config, "SARA_LANGUAGE", "") or "").strip().lower()
        if raw.startswith("hinglish"):
            return "hinglish"
        return "hi" if raw.startswith("hi") else "en"

    def _endpoint_guard_get(self) -> object:
        """Lazily build the per-engine EarlyCutGuard; window and back-off follow Config per turn."""
        from sara.audio.stt.endpoint import EarlyCutGuard

        window = float(getattr(Config, "ENDPOINT_EARLY_CUT_WINDOW_MS", 700))
        step = float(getattr(Config, "ENDPOINT_EARLY_CUT_BACKOFF_MS", 100))
        guard = getattr(self, "_endpoint_guard", None)
        if guard is None:
            guard = EarlyCutGuard(window, step)
            self._endpoint_guard = guard
        guard.window_ms, guard.backoff_step_ms = window, step
        return guard

    def endpoint_stats(self) -> dict:
        """Adaptive-endpointing counters: endpoints, early cuts and a per-rule histogram."""
        guard = getattr(self, "_endpoint_guard", None)
        try:
            if guard is not None:
                return guard.stats()
        except Exception:
            pass
        return {"endpoints": 0, "early_cuts": 0, "rules": {}}

    def _endpoint_open(
        self,
        silence_limit: float,
        silence_limit_n: int,
        preview_chunks: "collections.deque[bytes]",
        generation: int,
        user_cb: Optional[Callable[[str], None]],
    ) -> Optional[dict]:
        """Per-utterance adaptive-endpoint state, or None (flag off / failure -> legacy rule)."""
        if not getattr(Config, "VOICE_ENDPOINT_ADAPTIVE", False):
            return None
        try:
            return {
                "on": True,
                "limit_n": silence_limit_n,
                "limit_ms": float(silence_limit) * 1000.0,
                "chunk_ms": 1000.0 * self.CHUNK_SIZE / self.SAMPLE_RATE,
                "preview": preview_chunks,
                "gen": generation,
                "user_cb": user_cb,
                "seq": 0,
                "partial": None,
                "sent": False,
                "lang": self._endpoint_lang(),
            }
        except Exception:
            print("[STT] adaptive endpoint unavailable; using legacy rule")
            return None

    @staticmethod
    def _endpoint_cb(ep: dict, user_cb: Optional[Callable[[str], None]]) -> Callable[[str], None]:
        """Preview callback that stamps the partial with the voice sequence at spawn time."""
        stamp = ep["seq"]

        def _cb(text: str) -> None:
            ep["partial"] = (stamp, str(text))
            if user_cb is not None:
                user_cb(text)

        return _cb

    @staticmethod
    def _endpoint_voice(ep: dict) -> None:
        """Voice resumed: older partials are stale and a new silence run may preview again."""
        ep["seq"] += 1
        ep["sent"] = False

    def _endpoint_speech_started(self) -> None:
        """Tell the early-cut guard that speech started again."""
        try:
            self._endpoint_guard_get().note_next_speech_start(None)
        except Exception:
            print("[STT] endpoint guard failed")

    def _endpoint_preview(self, ep: dict) -> None:
        """Request the one extra preview of this silence run (skipped while one is in flight)."""
        if self._preview_busy.is_set():
            return
        ep["sent"] = True
        self._spawn_preview_transcribe(
            b"".join(ep["preview"]), self._endpoint_cb(ep, ep["user_cb"]), ep["gen"]
        )

    def _endpoint_reached(self, ep: dict, silence_count: int, utt_chunks: int) -> bool:
        """Adaptive replacement for `silence_count >= silence_limit_n`; failure -> legacy rule."""
        if not ep["on"]:
            return silence_count >= ep["limit_n"]
        try:
            from sara.audio.stt import endpoint as _ep

            silence_ms = silence_count * ep["chunk_ms"]
            utt_ms = utt_chunks * ep["chunk_ms"]
            if not ep["sent"] and silence_ms >= float(
                getattr(Config, "ENDPOINT_PREVIEW_AT_MS", 150)
            ):
                self._endpoint_preview(ep)
            fresh = ep["partial"]
            text = fresh[1] if fresh is not None and fresh[0] == ep["seq"] else None
            floor = getattr(getattr(self, "_noise_floor", None), "floor", None)
            noise = _ep.noise_level_from(floor, float(self.energy_threshold))
            guard = self._endpoint_guard_get()
            kw = {
                "utterance_ms": utt_ms,
                "base_ms": ep["limit_ms"],
                "backoff_ms": guard.backoff_ms(),
            }
            if not _ep.decide_endpoint(text, silence_ms, noise, ep["lang"], **kw):
                return False
            rule = _ep.required_silence_ms(text, noise, ep["lang"], **kw)[1]
            guard.note_endpoint(text, rule)
            if getattr(Config, "DEBUG_MODE", False):
                print(
                    f"[STT] endpoint rule={rule} silence_ms={int(silence_ms)} "
                    f"utt_ms={int(utt_ms)}"
                )
            return True
        except Exception:
            ep["on"] = False
            print("[STT] adaptive endpoint failed; using legacy rule for this utterance")
            return silence_count >= ep["limit_n"]

    def _collect_speech(
        self,
        timeout: float,
        max_duration: float,
        silence_limit: float,
        on_partial_transcript: Optional[Callable[[str], None]] = None,
    ) -> bytes:
        thr = self.energy_threshold
        silence_limit_n = max(
            1, int(silence_limit * (self.SAMPLE_RATE / self.CHUNK_SIZE))
        )
        state = _CollectState.WAITING
        speech_chunks: List[bytes] = []
        silence_count, trailing_silence_chunks = 0, 0
        speech_start = 0.0
        last_preview_time = 0.0
        start_time = time.monotonic()
        energy_window = collections.deque(maxlen=3)

        # BOUNDED ROLLING PREVIEW WINDOW: a separate, genuinely bounded
        # structure maintained ALONGSIDE speech_chunks (which keeps
        # growing and remains the untouched source for the FINAL
        # transcription). preview_chunks holds only the trailing
        # PREVIEW_WINDOW_S seconds of audio, tracked by total byte length
        # rather than item count (chunk sizes are not all uniform --
        # the initial pre-buffer blob is larger than a single mic
        # chunk). Chunks are appended in O(1) and old chunks are evicted
        # from the left in O(1) amortized as new ones arrive, so building
        # the preview input never requires rebuilding or scanning the
        # whole (potentially many-seconds-long) speech_chunks buffer --
        # see _push_preview_chunk() below.
        preview_window_bytes = int(
            self.SAMPLE_RATE * self.SAMPLE_WIDTH * self.PREVIEW_WINDOW_S
        )
        preview_chunks: "collections.deque[bytes]" = collections.deque()
        preview_bytes_total = 0

        def _push_preview_chunk(c: bytes) -> None:
            nonlocal preview_bytes_total
            preview_chunks.append(c)
            preview_bytes_total += len(c)
            while preview_bytes_total > preview_window_bytes and len(preview_chunks) > 1:
                removed = preview_chunks.popleft()
                preview_bytes_total -= len(removed)

        self._is_listening.set()
        # New session -> new generation. Any preview spawned by a
        # previous session will see this advance and discard its result
        # instead of delivering a stale caption (see
        # _spawn_preview_transcribe()).
        self._preview_generation += 1
        my_generation = self._preview_generation
        ep = self._endpoint_open(
            silence_limit, silence_limit_n, preview_chunks, my_generation, on_partial_transcript
        )
        if ep is not None and on_partial_transcript is None:
            on_partial_transcript = self._endpoint_noop

        # BARGE-IN HANDOFF: prepend any audio captured by
        # capture_barge_in_audio() at the moment a barge-in interrupted
        # TTS (see TTSWorker._watch_loop() in tts_worker.py). Consuming
        # it here -- right as THIS session starts pulling its own
        # backlog off self._ring -- is what makes the interruption
        # become the start of the user's next command instead of being
        # lost. Cleared under the same lock immediately after reading so
        # it's picked up by exactly one listen() call, never reused. In
        # the normal (non-interrupted) wake->listen flow this is always
        # empty -- capture_barge_in_audio() is only ever called from the
        # barge-in path -- so that behavior is completely unchanged.
        with self._tts_state_lock:
            handoff_chunks = self._barge_in_handoff
            self._barge_in_handoff = []
        backlog = self._ring.get_all(clear=True)
        if handoff_chunks:
            backlog = handoff_chunks + list(backlog)

        try:
            pending_chunks = list(backlog)
            while not self._closed:
                now = time.monotonic()
                if state is _CollectState.WAITING:
                    if now - start_time > timeout:
                        return b""
                    if not pending_chunks:
                        self._ring.wait(timeout=0.05)
                        pending_chunks = self._ring.get_all(clear=True)

                    consumed = 0
                    for chunk in pending_chunks:
                        consumed += 1
                        # D45: STARTING a capture must not be decided by
                        # webrtcvad either. This read
                        #
                        #   self._vad.is_speech(chunk) or _rms(chunk) > thr * 0.35
                        #
                        # and D39's comment claimed leaving the `or` here was
                        # safe because "a false start is cheap again once a
                        # capture can end". THAT WAS WRONG, and the target
                        # machine proved it on the very next run:
                        #
                        #   [STT] endpoint rule=base silence_ms=800 utt_ms=64
                        #   listen completed | audio=0.32s | text=''
                        #   listen() returned after 1.12s
                        #
                        # with stage 3 measuring room noise at RMS 29 and
                        # stage 5 showing webrtcvad voicing 112 of 123 chunks
                        # of that same near-silent clip. Energy cannot have
                        # started that capture (29 is far below this gate's
                        # 210), so VAD started it on room tone, before the
                        # user spoke. Every number matches a false start
                        # exactly: 300ms pre-buffer + one 32ms trigger chunk
                        # returned as 0.32s of audio, 2 list entries counted
                        # as utt_ms=64, the endpoint then firing correctly
                        # after 800ms of silence.
                        #
                        # A false start is not cheap. It CONSUMES the
                        # listen() call: the window opens on noise, ends
                        # 800ms later with nothing, and the user's actual
                        # speech arrives after it has already closed. Before
                        # D39 this was invisible only because capture never
                        # ended -- it ran the full 12s and usually caught the
                        # speech anyway. D39 did not create this; it removed
                        # what was masking it.
                        #
                        # So energy decides both gates. Margins on the
                        # measured hardware: speech RMS 1599 against a 210
                        # start gate (7.6x), room noise RMS 29 (6.3x below
                        # the 180 continuation gate). Nothing is lost by
                        # dropping VAD, because _pre_buf already holds
                        # PRE_SPEECH_MS (300ms) of history, so a gate that
                        # triggers a chunk later still captures the onset.
                        #
                        # CORRECTION (D47). This comment originally claimed
                        # "a quiet speaker gets a lower gate automatically"
                        # because _run_one_recalibration() re-derives the
                        # threshold from the measured noise floor. That is
                        # true in principle and MISLEADING in practice, so
                        # do not rely on it: the first calibration waits
                        # _RECALIB_FIRST_INTERVAL (15s), every later one
                        # waits _RECALIB_INTERVAL (300s), each step is
                        # clamped to +/-150, and the loop skips a cycle
                        # entirely while _is_listening is set. Moving 600
                        # down to a floor-derived ~283 therefore takes three
                        # steps, i.e. about ten minutes -- and inside a
                        # bench/stt_doctor.py run the engine does not live
                        # long enough for even the first one to fire.
                        #
                        # So within any session a user actually experiences,
                        # thr is effectively the BARGE_IN_ENERGY_THRESHOLD
                        # default. On a quiet microphone that makes this gate
                        # the binding constraint -- see D47/D48/D49. Until
                        # that is resolved, a quiet mic is tuned by setting
                        # BARGE_IN_ENERGY_THRESHOLD in .env.
                        #
                        # VAD is untouched at its other call sites
                        # (_passes_barge_in_gate, is_user_speaking) -- this
                        # is only about who decides a capture window.
                        if _rms(chunk) > thr * 0.35:
                            pre = self._pre_buf.drain()
                            speech_chunks = [pre, chunk] if pre else [chunk]
                            for _c in speech_chunks:
                                _push_preview_chunk(_c)
                            silence_count = 0
                            speech_start = time.monotonic()
                            _t_pending("speech_start")
                            last_preview_time = speech_start
                            if ep is not None:
                                self._endpoint_speech_started()
                            state = _CollectState.SPEAKING
                            break
                    pending_chunks = (
                        pending_chunks[consumed:]
                        if state is _CollectState.SPEAKING
                        else []
                    )

                elif state is _CollectState.SPEAKING:
                    if now - speech_start > max_duration:
                        state = _CollectState.DONE

                    # LIVE CAPTION PREVIEW (best-effort, non-blocking): every
                    # ~900ms while still actively collecting speech, kick off
                    # a background best-effort transcribe of the BOUNDED
                    # trailing preview_chunks window (NOT the whole
                    # accumulated speech_chunks buffer) using the fast/small
                    # wake-word model, so the caller (see
                    # on_partial_transcript) can show a provisional
                    # dim/italic caption before the real, accurate
                    # transcript is ready. Gated on self._preview_busy so
                    # at most one preview transcribe runs at a time -- if
                    # the previous one hasn't finished yet, this cycle is
                    # just skipped rather than piling up more threads.
                    if (
                        state is _CollectState.SPEAKING
                        and on_partial_transcript is not None
                        and now - last_preview_time >= 0.9
                        and not self._preview_busy.is_set()
                    ):
                        last_preview_time = now
                        preview_audio = b"".join(preview_chunks)
                        self._spawn_preview_transcribe(
                            preview_audio,
                            on_partial_transcript
                            if ep is None
                            else self._endpoint_cb(ep, ep["user_cb"]),
                            my_generation,
                        )

                    if state is _CollectState.SPEAKING:
                        if not pending_chunks:
                            self._ring.wait(timeout=0.05)
                            pending_chunks = self._ring.get_all(clear=True)

                        for chunk in pending_chunks:
                            energy = _rms(chunk)
                            energy_window.append(energy)
                            # D39: ENDING a capture must not be decided by
                            # webrtcvad. This test used to be
                            #
                            #   vad.is_speech(chunk) or (avg > thr * 0.3)
                            #
                            # and webrtcvad at aggressiveness 2 reports a
                            # quiet room as speech most of the time: measured
                            # on the target machine, 74/123 and 112/123 chunks
                            # voiced on 4s clips containing roughly 1s of
                            # speech (bench/stt_doctor.py stage 5 runs the
                            # same _VADFilter over the same 1024-byte chunks
                            # this loop uses, so those are literally this
                            # is_speech() call's return values).
                            #
                            # Under `or`, that made the energy term
                            # unreachable: is_voice stayed True through
                            # silence, silence_count never incremented, the
                            # endpoint never fired and EVERY command ran to
                            # max_duration (12s) -- after which Whisper
                            # hallucinated on the trailing silence
                            # ('こんにちは!' on a captured 12.26s where the
                            # user said "open chrome" and stopped). The energy
                            # term was healthy in that very run (room noise
                            # 125 against this gate's 180); it simply never
                            # got a vote.
                            #
                            # Energy decides, because energy is the signal
                            # that actually separates speech from a quiet
                            # room here: measured speech is RMS 2337-2660
                            # against a 180 gate, 13x of headroom. Dropping
                            # VAD costs nothing in sensitivity either, since
                            # the START gate above is thr * 0.35 (210) --
                            # anything loud enough to begin a capture is
                            # comfortably loud enough to sustain one at 180.
                            #
                            # Unchanged where webrtcvad is absent:
                            # _VADFilter.is_speech() returns False
                            # unconditionally there, so that path was already
                            # energy-only. The START gate is deliberately left
                            # alone -- it is meant to be sensitive, and a
                            # false start is cheap again once a capture can
                            # end. See D32 for calibrating this gate to the
                            # measured noise floor instead of a constant.
                            is_voice = (
                                sum(energy_window) / len(energy_window)
                                > thr * 0.3
                            )

                            speech_chunks.append(chunk)
                            _push_preview_chunk(chunk)
                            if is_voice:
                                silence_count, trailing_silence_chunks = 0, 0
                                if ep is not None:
                                    self._endpoint_voice(ep)
                            else:
                                silence_count += 1
                                trailing_silence_chunks += 1
                                if ep is None:
                                    ended = silence_count >= silence_limit_n
                                else:
                                    # D46: this passed
                                    # `len(speech_chunks) - trailing_silence_chunks`,
                                    # a count of LIST ENTRIES -- but
                                    # speech_chunks[0] is the drained
                                    # _pre_buf BLOB, PRE_SPEECH_MS (300ms,
                                    # ~9 chunks) joined into one bytes
                                    # object by _PreBuffer.drain(). So the
                                    # utterance length was under-reported by
                                    # about 8 chunks (~256ms) on every turn.
                                    #
                                    # Seen in the target machine's trace as
                                    # `utt_ms=896` for a capture that
                                    # returned `audio=1.15s`: 36 chunk-
                                    # equivalents counted as 28 entries,
                                    # exactly the 9-chunk blob collapsing to
                                    # one.
                                    #
                                    # It matters because required_silence_ms()
                                    # refuses to endpoint below `base` while
                                    # utterance_ms < _SHORT_UTTERANCE_MS
                                    # (500ms) -- a guard meant for stray
                                    # blips. Under-reporting pushed genuine
                                    # 500-760ms commands under that bar, so
                                    # they waited `base` (625ms) instead of
                                    # ENDPOINT_MIN_MS (350ms). Measuring
                                    # bytes restores the designed boundary
                                    # rather than moving it.
                                    _voiced = (
                                        len(speech_chunks)
                                        - trailing_silence_chunks
                                    )
                                    _voiced_bytes = sum(
                                        len(c) for c in speech_chunks[:_voiced]
                                    )
                                    _bytes_per_chunk = (
                                        self.CHUNK_SIZE * self.SAMPLE_WIDTH
                                    )
                                    ended = self._endpoint_reached(
                                        ep,
                                        silence_count,
                                        _voiced_bytes / _bytes_per_chunk,
                                    )
                                if ended:
                                    state = _CollectState.DONE
                                    break
                        pending_chunks = []

                if state is _CollectState.DONE:
                    if (
                        trailing_silence_chunks > 0
                        and len(speech_chunks) > trailing_silence_chunks
                    ):
                        speech_chunks = speech_chunks[:-trailing_silence_chunks]
                    duration = time.monotonic() - speech_start
                    self._silence_gate.record(duration)
                    return b"".join(speech_chunks)

            return b""
        finally:
            self._is_listening.clear()

    def _spawn_preview_transcribe(
        self,
        snapshot: bytes,
        on_partial_transcript: Callable[[str], None],
        generation: int,
    ) -> None:
        """
        Best-effort, fire-and-forget "live caption" preview: transcribes
        `snapshot` -- a BOUNDED trailing window of the speech collected so
        far (see _collect_speech()'s preview_chunks, NOT the whole
        accumulated buffer) -- using the dedicated small/fast Whisper
        model (self._wake_whisper_model), NEVER the slower main model, on
        a background thread, so the caller can show a provisional caption
        while the user is still talking. Deliberately never touches the
        real/accurate transcription path in listen()/_transcribe() -- any
        exception here (transcribe error, callback raising) is swallowed
        so a preview glitch can never crash or stall the main
        _collect_speech() loop. self._preview_busy is set here (before
        the thread starts) and cleared in the thread's finally block, so
        _collect_speech() can tell a preview is still in flight and skip
        spawning another one on top of it.

        `generation` is the _preview_generation value captured by the
        CALLING _collect_speech() session. If a newer session has started
        (self._preview_generation has advanced) by the time this
        background transcribe finishes, the result is discarded instead
        of being delivered via on_partial_transcript -- this prevents a
        stale preview from an already-finished utterance updating the UI
        for a newer one.

        SAFETY: if self._wake_whisper_model is unavailable (failed to
        load), preview is skipped entirely rather than falling back to
        the large main model -- see _transcribe()'s
        `model_override is not None else self._whisper_model` fallback,
        which would otherwise silently run preview on the slow/large
        model.
        """
        if self._wake_whisper_model is None:
            return

        self._preview_busy.set()

        def _run() -> None:
            # D51: time this. The preview's latency decides how much of the
            # adaptive endpoint's "complete command" fast path is collected,
            # and until this line existed it had to be INFERRED.
            #
            # D72 CORRECTED THIS INSTRUMENT. It used to print a binary
            # "in budget | TOO SLOW" against ENDPOINT_MIN_MS minus
            # ENDPOINT_PREVIEW_AT_MS, and D51's own note claimed that being
            # over that budget meant the command "fell back to base (625ms)"
            # and the 288ms saving "was never collected".
            #
            # Both were wrong, and replaying the real decide_endpoint()
            # against the seven measured previews is what showed it:
            # _endpoint_reached() re-reads ep["partial"] on EVERY 32ms tick,
            # so a LATE partial is still used the moment it lands. The
            # endpoint therefore fires at about
            # ENDPOINT_PREVIEW_AT_MS + preview_ms, not at base:
            #
            #   preview 218 282 313 344 375 390 485 ms
            #   fires   384 448 480 512 544 544 640 ms   (all rule=complete)
            #   ideal 352ms, base-if-no-partial 640ms
            #
            # So the budget is not a cliff: overshooting it costs the
            # overshoot, not the whole saving. Mean cost of the measured
            # distribution is 155ms per command, not 288ms. Only a preview
            # that never lands at all falls back to base.
            #
            # The old line therefore said TOO SLOW seven times out of seven
            # while the fast path was in fact firing seven times out of
            # seven, just late -- a verdict that would have justified
            # changing ENDPOINT_PREVIEW_AT_MS to fix a cliff that does not
            # exist. This prints the real cost in milliseconds instead.
            # Debug-gated, no transcript text logged.
            _started = time.monotonic()
            try:
                result = self._transcribe(
                    snapshot,
                    beam_size_override=1,
                    model_override=self._wake_whisper_model,
                )
                if getattr(Config, "DEBUG_MODE", False):
                    _took = (time.monotonic() - _started) * 1000.0
                    _at = float(getattr(Config, "ENDPOINT_PREVIEW_AT_MS", 150))
                    _min = float(getattr(Config, "ENDPOINT_MIN_MS", 350))
                    _max = float(getattr(Config, "ENDPOINT_MAX_MS", 900))
                    _chunk = (
                        float(getattr(self, "CHUNK_SIZE", 512))
                        / float(getattr(self, "SAMPLE_RATE", 16000) or 16000)
                        * 1000.0
                    ) or 32.0
                    # D72b: the base the endpoint ACTUALLY races against.
                    #
                    # It is not ENDPOINT_MIN + (MAX-MIN)/2. _endpoint_open()
                    # passes base_ms=_SilenceGate.silence_limit*1000, which
                    # is 800ms on a fresh engine, 450ms once the recent
                    # utterances average under 1.5s (the normal state for
                    # short commands) and 1200ms after long dictation,
                    # clamped into [MIN, MAX]. Reading the fallback instead
                    # of this is how the earlier figures came out wrong --
                    # the same mistake as D71's, a number taken from a
                    # default rather than from the running system.
                    _gate = getattr(self, "_silence_gate", None)
                    _limit = getattr(_gate, "silence_limit", None)
                    _base = (
                        min(_max, max(_min, float(_limit) * 1000.0))
                        if _limit is not None
                        else _min + 0.5 * (_max - _min)
                    )
                    # Each is the first tick at which that rule can fire;
                    # whichever comes first is what ends the turn. A tie
                    # goes to "complete" -- at the same tick the partial is
                    # present, so required_silence_ms returns MIN.
                    _t_done = math.ceil(max(_min, _at + _took) / _chunk) * _chunk
                    _t_base = math.ceil(_base / _chunk) * _chunk
                    _ideal = math.ceil(_min / _chunk) * _chunk
                    _fires = min(_t_done, _t_base)
                    _wasted = _t_done > _t_base
                    if _wasted:
                        _verdict = (
                            f"the {_base:.0f}ms base rule fired first, so "
                            f"this preview changed nothing"
                        )
                    elif _fires <= _ideal:
                        _verdict = "free -- it landed inside ENDPOINT_MIN_MS"
                    else:
                        _verdict = (
                            f"saved {_t_base - _fires:.0f}ms against the "
                            f"{_base:.0f}ms base rule"
                        )
                    print(
                        f"[STT] preview took {_took:.0f}ms -> endpoint fires "
                        f"~{_fires:.0f}ms (ideal {_ideal:.0f}ms, "
                        f"+{_fires - _ideal:.0f}ms); {_verdict}"
                    )
                if result and generation == self._preview_generation:
                    try:
                        on_partial_transcript(str(result))
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                self._preview_busy.clear()

        threading.Thread(
            target=_run, daemon=True, name="stt-preview-transcribe"
        ).start()

    @staticmethod
    def _normalize_audio_float32(
        audio_bytes: bytes, target_peak: float = 0.95
    ) -> np.ndarray:
        arr = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        peak = np.max(np.abs(arr))
        # TUNING (accuracy): only auto-gain a clip whose peak is already
        # above _MIN_GAIN_APPLY_PEAK -- see that constant's module-level
        # comment. A clip quieter than this is treated as noise-floor, not
        # under-recorded speech, and is left at its true (very quiet)
        # level rather than amplified up to 8x into the model.
        if _MIN_GAIN_APPLY_PEAK < peak < target_peak:
            gain = min(target_peak / peak, 8.0)
            arr = np.clip(arr * gain, -1.0, 1.0)
        return arr

    def _update_detected_language(self, text: str) -> None:
        lang_mode = getattr(Config, "LANG_DETECTION_MODE", "auto")
        stt_lang = getattr(Config, "STT_LANGUAGE", None)
        with self._transcript_lock:
            self._detected_lang = (
                _lang_from_stt_language(stt_lang)
                if (lang_mode == "manual" and stt_lang)
                else _detect_language(text)
            )

    def _resolve_forced_language(self) -> Optional[str]:
        lang_mode = getattr(Config, "LANG_DETECTION_MODE", "auto")
        stt_lang = getattr(Config, "STT_LANGUAGE", None)
        if lang_mode == "manual" and stt_lang:
            return stt_lang

        # POLICY: only force Hindi when the project is explicitly
        # configured for Hindi (SARA_LANGUAGE == "hindi"). The default
        # "hinglish" setting used to ALSO force language="hi" on every
        # turn, biasing clearly-English and English-heavy mixed speech
        # toward Hindi transcription. For "hinglish" (and "english")
        # this now returns None, letting Whisper auto-detect per
        # utterance from the audio itself -- this does NOT translate or
        # alter transcript text, it only affects which language Whisper
        # decodes toward. See _get_transcribe_prompt() below for the
        # matching prompt-selection policy.
        force_for_hindi = getattr(Config, "STT_FORCE_LANG_FOR_HINGLISH", True)
        sara_lang = getattr(Config, "SARA_LANGUAGE", "hinglish")
        if force_for_hindi and sara_lang == "hindi":
            return "hi"
        return None

    # Style-guidance prompt used ONLY when the turn is explicitly forced
    # to Hindi (forced_lang == "hi": either a manual STT_LANGUAGE == "hi"
    # override, or SARA_LANGUAGE == "hindi"). Contains Devanagari +
    # romanized Hindi/Hinglish style examples, which is appropriate here
    # because the turn is *known* to be Hindi -- there is no English
    # speech in this branch to accidentally bias.
    _STATIC_TRANSCRIBE_PROMPT_HI = (
        "यह बातचीत हिंदी, इंग्लिश और हिंग्लिश में हो सकती है। "
        "Transcribe naturally without translating, keep proper names exactly as spoken, "
        "do not invent words. Example style: 'aaj mujhe office jana hai', "
        "'mera naam Sara hai', 'kya haal hai bhai'."
    )

    # Style-guidance prompt used when NO language is explicitly forced
    # (forced_lang is None) and the project default is "hinglish" (or any
    # value other than "english"). Deliberately neutral: it acknowledges
    # that the speech may be English, Hindi, or a code-switched mix,
    # WITHOUT the Hindi/Hinglish-specific romanized vocabulary examples
    # used in _STATIC_TRANSCRIBE_PROMPT_HI above -- those examples were
    # found to measurably bias Whisper's vocabulary/style even for
    # clearly-English or English-heavy input, which is exactly what this
    # branch must avoid.
    _STATIC_TRANSCRIBE_PROMPT_NEUTRAL = (
        "This conversation may be in English, Hindi, or a natural code-switched "
        "mix of both. Transcribe exactly what is spoken, in whichever language "
        "or mix it was actually spoken in, without translating between "
        "languages, without switching script, and without inventing words. "
        "Keep proper names and technical terms exactly as spoken."
    )

    def _get_transcribe_prompt(self, forced_lang: Optional[str]) -> str:
        """
        TUNING (accuracy): picks the style-guidance prompt appropriate
        for this specific turn.

        - forced_lang == "hi" (explicit Hindi, manual or
          SARA_LANGUAGE == "hindi"): the speech is KNOWN to be Hindi, so
          the Hindi/Hinglish-styled prompt is safe and helpful.
        - forced_lang == "en" (explicit manual English): the speech is
          KNOWN to be English, so the English-only prompt is used.
        - forced_lang is None (auto -- the default "hinglish" mode, or
          any unrecognized SARA_LANGUAGE): the language for THIS turn is
          not yet known and may be English, Hindi, or a code-switched
          mix. Using the Hindi/Hinglish-styled prompt here (as this
          project used to do unconditionally) would bias English-heavy
          or pure-English speech toward Hindi vocabulary/style. Instead
          this branch uses the neutral, language-agnostic prompt, EXCEPT
          when SARA_LANGUAGE == "english" (project explicitly configured
          English-first with no other override in play), where the
          English-only prompt is still the better default.
        """
        sara_lang = getattr(Config, "SARA_LANGUAGE", "hinglish")

        # Names the user actually uses (vocabulary.py): measured English
        # word errors 5.7 % -> 2.1 %, no hallucinated names on silence.
        hint = vocabulary_hint()
        if forced_lang == "hi":
            return self._STATIC_TRANSCRIBE_PROMPT_HI + hint
        if forced_lang == "en":
            return _STATIC_TRANSCRIBE_PROMPT_EN + hint
        if sara_lang == "english":
            return _STATIC_TRANSCRIBE_PROMPT_EN + hint
        return self._STATIC_TRANSCRIBE_PROMPT_NEUTRAL + hint

    def _start_ducking(self, mode: str):
        """Turn other apps down while a COMMAND is being captured. Returns the ducker (to restore) or None."""
        if mode not in ("command", "dictate") or not getattr(Config, "VOICE_DUCK_OTHER_AUDIO", True):
            return None
        try:
            from sara.audio.ducking import AudioDucker

            ducker = AudioDucker(level=float(getattr(Config, "VOICE_DUCK_LEVEL", 0.15)))
            n = ducker.duck()
            if n and getattr(Config, "DEBUG_MODE", False):
                print(f"[STT] other audio turned down ({n} app session(s)) while listening")
            return ducker
        except Exception as e:
            print(f"[STT] ducking unavailable ({type(e).__name__}: {e})")
            return None

    @staticmethod
    def _level_for_main(pcm_i16) -> "np.ndarray":
        """Raise a quiet clip to RMS ~0.08 (never more than x30, never clipping): the main model loses words at RMS ~200."""
        a = pcm_i16.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0
        if rms < 1e-4:
            return a
        return np.clip(a * min(0.08 / rms, 30.0), -1.0, 1.0)

    def _hinglish_first(self, raw_np, no_speech_thr):
        """
        Run the Hinglish model on the raw recording. Returns {"text", "segs", "lp", "done", "ms"}; done=True when its
        answer is final (see helpers.need_main_model).
        """
        try:
            from sara.core.intent.engine import detect_intent

            routes = lambda t: detect_intent(t)[0] != "chat"
        except Exception:
            routes = lambda t: False
        t0 = time.perf_counter()
        segs, _ = self._hinglish_model.transcribe(
            raw_np, beam_size=2, condition_on_previous_text=False, vad_filter=False,
            no_speech_threshold=no_speech_thr, temperature=0.0, compression_ratio_threshold=2.0,
            log_prob_threshold=-1.0,
        )
        segs = [s for s in segs if getattr(s, "no_speech_prob", 0.0) < no_speech_thr]
        text = "".join(s.text for s in segs).strip()
        lp = (sum(s.avg_logprob for s in segs) / len(segs)) if segs else -9.0
        heard = bool(segs)
        if text and (_is_hallucinated_repetition(text, min_repeats=3) or _is_degenerate_text(text) or lp < -1.0):
            text, segs = "", []
        done = (not heard) or not need_main_model(
            text, lp, routes, float(getattr(Config, "WHISPER_HINGLISH_CONFIDENT_LP", -0.3))
        )
        return {"text": text, "segs": segs, "lp": lp, "done": done, "heard": heard,
                "ms": (time.perf_counter() - t0) * 1000, "routes": routes}

    def _pick_after_first(self, first, a_text, usable, info):
        """The main model has also listened: choose between its text and the Hinglish model's."""
        lp = (sum(s.avg_logprob for s in usable) / len(usable)) if usable else -9.0
        which, chosen = pick_after_hinglish_first(
            first["text"], a_text, getattr(info, "language", None),
            float(getattr(info, "language_probability", 0.0) or 0.0), lp, first["routes"],
            min_en_prob=float(getattr(Config, "WHISPER_HINGLISH_MIN_EN_PROB", 0.9)),
            min_logprob=float(getattr(Config, "WHISPER_HINGLISH_MIN_LOGPROB", -0.5)),
        )
        if getattr(Config, "DEBUG_MODE", False):
            print(
                f"[STT] hinglish={first['text'][:50]!r} (lp {first['lp']:.2f}, {first['ms']:.0f} ms) | "
                f"main={a_text[:50]!r} ({getattr(info, 'language', '?')} "
                f"{float(getattr(info, 'language_probability', 0.0) or 0.0):.2f}, lp {lp:.2f}) -> using "
                f"{'main' if which == 'A' else 'hinglish'}"
            )
        if which == "B" and first["segs"]:
            return chosen, first["segs"]
        return a_text, usable

    def _maybe_use_hinglish(self, audio_np, text, usable, info, beam_size, no_speech_thr):
        """Returns (text, usable_segments) after applying choose_transcript()."""
        try:
            from sara.core.intent.engine import detect_intent

            def routes(t: str) -> bool:
                return detect_intent(t)[0] != "chat"
        except Exception:
            def routes(t: str) -> bool:
                return False

        box = {}

        # Nothing was heard at all (every segment judged "no speech"): the second model would only invent text
        # on that audio -- the live log had "kahaan kahaan kahaan ...", "li, li, li ...", "yayaya ..." from it.
        if not usable and not text:
            return text, usable

        def run_b() -> str:
            t0 = time.perf_counter()
            segs, _ = self._hinglish_model.transcribe(
                audio_np, beam_size=1, condition_on_previous_text=False, vad_filter=False,
                no_speech_threshold=no_speech_thr, temperature=0.0, compression_ratio_threshold=2.0,
                log_prob_threshold=-1.0,
            )
            segs = [s for s in segs if getattr(s, "no_speech_prob", 0.0) < no_speech_thr]
            out = "".join(s.text for s in segs).strip()
            box["ms"] = (time.perf_counter() - t0) * 1000
            lp = (sum(s.avg_logprob for s in segs) / len(segs)) if segs else -9.0
            box["lp"] = lp
            # A looped / contentless / very unsure result is worse than the main model's text.
            if _is_hallucinated_repetition(out, min_repeats=3) or _is_degenerate_text(out) or lp < -1.0:
                box["rejected"] = out
                return ""
            box["segs"] = segs
            return out

        lp = (sum(s.avg_logprob for s in usable) / len(usable)) if usable else -9.0
        which, chosen = choose_transcript(
            text,
            getattr(info, "language", None),
            float(getattr(info, "language_probability", 0.0) or 0.0),
            lp,
            run_b,
            routes,
            min_en_prob=float(getattr(Config, "WHISPER_HINGLISH_MIN_EN_PROB", 0.85)),
            min_logprob=float(getattr(Config, "WHISPER_HINGLISH_MIN_LOGPROB", -0.6)),
        )
        if getattr(Config, "DEBUG_MODE", False):
            if "ms" in box:
                print(
                    f"[STT] main={text[:60]!r} ({getattr(info, 'language', '?')} "
                    f"{float(getattr(info, 'language_probability', 0.0) or 0.0):.2f}, lp {lp:.2f}) | "
                    f"hinglish={chosen[:60] if which == 'B' else (box.get('rejected') or '')[:60]!r} "
                    f"(lp {box.get('lp', 0):.2f}, {box['ms']:.0f} ms) -> using {which}"
                )
        if which == "B" and box.get("segs"):
            return chosen, box["segs"]
        return text, usable

    def _transcribe(
        self,
        audio_bytes: bytes,
        beam_size_override: Optional[int] = None,
        model_override: Optional[object] = None,
    ) -> "TranscriptionResult":
        model = model_override if model_override is not None else self._whisper_model
        if not audio_bytes or model is None:
            return TranscriptionResult("", 0.0)

        duration_s = len(audio_bytes) / (self.SAMPLE_RATE * self.SAMPLE_WIDTH)
        if duration_s < 0.20:
            return TranscriptionResult("", 0.0)

        try:
            audio_np = self._normalize_audio_float32(audio_bytes)

            forced_lang = self._resolve_forced_language()
            beam_size = (
                int(beam_size_override)
                if beam_size_override is not None
                else int(getattr(Config, "WHISPER_BEAM_SIZE", 3))
            )
            no_speech_thr = float(getattr(Config, "STT_NO_SPEECH_THRESHOLD", 0.6))

            temperature = getattr(
                Config, "STT_TEMPERATURE_FALLBACK", (0.0, 0.4, 0.8)
            )

            # "Hinglish model first": it listens to the untouched recording (a level boost hurts it: -8 points
            # measured); the main model only when it is not sure. Skipped for previews and when English is pinned.
            first = None
            if (
                model_override is None
                and forced_lang in (None, "hi")
                and getattr(self, "_hinglish_model", None) is not None
                and getattr(Config, "WHISPER_HINGLISH_FIRST", True)
            ):
                try:
                    first = self._hinglish_first(
                        np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0, no_speech_thr
                    )
                except Exception as _e:
                    print(f"[STT] Hinglish pass failed ({type(_e).__name__}: {_e}); using the main model.")

            if first is not None and first["done"]:
                usable = first["segs"]
                text = first["text"]
                _info = None
            else:
                main_audio = audio_np
                if first is not None and getattr(Config, "WHISPER_MAIN_GAIN", True):
                    main_audio = self._level_for_main(np.frombuffer(audio_bytes, dtype=np.int16))
                segments, _info = model.transcribe(
                    main_audio,
                    beam_size=beam_size,
                    best_of=beam_size,
                    temperature=temperature,
                    language=forced_lang,
                    task="transcribe",
                    initial_prompt=self._get_transcribe_prompt(forced_lang),
                    condition_on_previous_text=False,
                    vad_filter=False,  # Handled upstream by WebRTC
                    no_speech_threshold=no_speech_thr,
                    log_prob_threshold=float(
                        getattr(Config, "STT_LOG_PROB_THRESHOLD", -1.0)
                    ),
                    compression_ratio_threshold=float(
                        getattr(Config, "STT_COMPRESSION_RATIO_THRESHOLD", 2.4)
                    ),
                )

                segments = list(segments)
                usable = [
                    s for s in segments if getattr(s, "no_speech_prob", 0.0) < no_speech_thr
                ]
                text = "".join(segment.text for segment in usable).strip()

                if first is not None:
                    text, usable = self._pick_after_first(first, text, usable, _info)
                elif (
                    model_override is None
                    and forced_lang in (None, "hi")
                    and getattr(self, "_hinglish_model", None) is not None
                ):
                    # older ordering (WHISPER_HINGLISH_FIRST=False): main model first, Hinglish model second
                    try:
                        text, usable = self._maybe_use_hinglish(
                            audio_np, text, usable, _info, beam_size, no_speech_thr
                        )
                    except Exception as _e:
                        print(f"[STT] Hinglish pass failed ({type(_e).__name__}: {_e}); keeping the main transcript.")

            # ── STT CONFIDENCE SIGNAL ────────────────────────────────────
            # Mean of (1 - no_speech_prob) across the SAME `usable`
            # segments already computed above -- free, no extra model
            # call. Restricted to `usable` so a segment thrown away by
            # the no_speech filter can't skew the confidence of the
            # segments that actually made it into `text`. This value is
            # part of the public TranscriptionResult API (`.confidence`,
            # consumed downstream by core_wiring.py / intent_handlers.py
            # for the destructive-action spoken confirmation gate) and is
            # NOT used here to reject or discard any transcript --
            # short-transcript rejection based on this signal was
            # evaluated and intentionally NOT implemented, pending
            # runtime evidence (see project notes) that it can reliably
            # distinguish genuine short commands ("yes", "haan", "stop",
            # "open chrome", "call mom", "what now?") from noise/silence
            # hallucinations across English/Hindi/Hinglish conditions.
            if usable:
                confidence = sum(
                    1.0 - getattr(s, "no_speech_prob", 0.0) for s in usable
                ) / len(usable)
            else:
                confidence = 0.0

            min_repeats = int(getattr(Config, "STT_HALLUCINATION_MIN_REPEATS", 3))
            if _is_hallucinated_repetition(text, min_repeats=min_repeats):
                if getattr(Config, "DEBUG_MODE", False):
                    print(f"[STT] Discarded hallucinated repetition: {text[:80]!r}...")
                return TranscriptionResult("", 0.0)

            # BUGFIX: see _HALLUCINATION_PHRASES docstring in helpers.py —
            # catches single-shot boilerplate hallucinations (e.g.
            # "Subtitles by the Amara.org community") that the repetition
            # check above can't, since they only appear once per capture.
            if _is_known_hallucination(text):
                if getattr(Config, "DEBUG_MODE", False):
                    print(f"[STT] Discarded known hallucination phrase: {text[:80]!r}")
                return TranscriptionResult("", 0.0)

            # The style prompt echoed back, or text with no content (punctuation only, one unit
            # repeated "ndndnd...", "vay,vay,..."): invented on silence / music, never a command.
            if _is_prompt_echo(text) or _is_degenerate_text(text):
                if getattr(Config, "DEBUG_MODE", False):
                    print(f"[STT] Discarded prompt-echo / degenerate text: {text[:80]!r}")
                return TranscriptionResult("", 0.0)

            # D43: the two filters above are phrase-shaped and
            # repetition-shaped, and the target machine produced a
            # hallucination that is neither --
            #
            #   [STTDiag] listen completed | audio=12.26s | text='こんにちは!'
            #
            # on a capture where the user said "open chrome" and stopped.
            # It passed both filters, reached the router, matched no command
            # and was dispatched to the LLM as conversation, so the command
            # was silently lost. SARA_LANGUAGE is clamped to
            # english/hindi/hinglish, so a transcript written in a script
            # none of those use is invented text, not a mis-heard command.
            # Inert when STT_LANGUAGE pins a language explicitly -- see the
            # note on _is_foreign_script_hallucination in helpers.py.
            if _is_foreign_script_hallucination(
                text, getattr(Config, "STT_LANGUAGE", None)
            ):
                if getattr(Config, "DEBUG_MODE", False):
                    print(
                        f"[STT] Discarded foreign-script hallucination: {text[:80]!r}"
                    )
                return TranscriptionResult("", 0.0)

            if text:
                with self._transcript_lock:
                    self._recent_transcript = (self._recent_transcript + " " + text)[
                        -500:
                    ]
                self._update_detected_language(text)

            word_count = max(1, len(text.split()))
            speech_rate = word_count / duration_s if duration_s > 0 else 0.0
            if model_override is None and getattr(Config, "STT_DEBUG_DUMP_AUDIO", False):
                self._dump_utterance(audio_np, text, confidence)
            return TranscriptionResult(text, confidence, speech_rate)

        except Exception as e:
            print(f"[STT Error] Faster-Whisper Inference Failed: {e}")
            return TranscriptionResult("", 0.0)

    def listen(
        self,
        mode: str = "command",
        model_override: Optional[object] = None,
        on_partial_transcript: Optional[Callable[[str], None]] = None,
    ) -> "TranscriptionResult":
        if self._closed:
            return TranscriptionResult("", 0.0)

        if not self._listen_lock.acquire(blocking=False):
            if getattr(Config, "DEBUG_MODE", False):
                print(
                    f"[STT] listen(mode='{mode}') skipped — another listen() session is already active."
                )
            return TranscriptionResult("", 0.0)

        try:
            cfg = {
                # v9: was a fixed 3.0s/5.0s window sized for full commands.
                # "Sara"/"Hey Sara" is a one-second utterance -- waiting a
                # command-sized window before even starting to transcribe
                # was pure added latency on every single wake attempt.
                # Now config-driven (WAKE_LISTEN_TIMEOUT_S/
                # WAKE_LISTEN_MAX_DURATION_S), default 1.5s/1.8s.
                "wake": {
                    "timeout": float(getattr(Config, "WAKE_LISTEN_TIMEOUT_S", 1.5)),
                    "max_duration": float(
                        getattr(Config, "WAKE_LISTEN_MAX_DURATION_S", 1.8)
                    ),
                },
                # v8: command max_duration reduced 20s -> 12s. A shorter
                # capture window limits how much residual-echo-confused
                # silence padding a single session can accumulate before
                # forcibly ending, which limits the size of any potential
                # hallucination blob even before the prompt fix above.
                "command": {"timeout": 8.0, "max_duration": 12.0},
                "dictate": {"timeout": 8.0, "max_duration": 60.0},
            }.get(mode, {"timeout": 8.0, "max_duration": 12.0})

            ducker = self._start_ducking(mode)
            try:
                audio = self._collect_speech(
                    timeout=cfg["timeout"],
                    max_duration=cfg["max_duration"],
                    silence_limit=self._silence_gate.silence_limit,
                    # NEW: only "command"/"dictate" callers pass this (see
                    # core_wiring.py's ears.listen(mode="command", ...) call) --
                    # "wake" mode never does, so the live-caption preview
                    # mechanism above is naturally inert during wake-word
                    # detection without needing an explicit mode check here.
                    on_partial_transcript=on_partial_transcript,
                )
            finally:
                if ducker is not None:
                    ducker.restore()
            if not audio:
                return TranscriptionResult("", 0.0)

            if mode == "command":
                _t_pending("speech_end")

            beam_override = (
                int(getattr(Config, "WAKE_WORD_BEAM_SIZE", 1))
                if mode == "wake"
                else None
            )

            result = self._transcribe(
                audio,
                beam_size_override=beam_override,
                model_override=model_override,
            )

            if mode == "command":
                _t_pending("stt_done")

            print(
                f"[STTDiag] listen completed | "
                f"mode={mode} | "
                f"audio={len(audio) / (self.SAMPLE_RATE * self.SAMPLE_WIDTH):.2f}s | "
                f"text={str(result)!r} | "
                f"confidence={getattr(result, 'confidence', 0.0):.3f}"
            )

            return result
        finally:
            self._listen_lock.release()

    def is_wake_word_detected(self) -> bool:
        """
        Passive wake detection using OpenWakeWord only.

        Whisper/STT is NEVER used here.
        """
        if self._closed:
            return False

        model = self._wakeword_model

        if not getattr(self, "_wake_diag_gate_seen", False):
            self._wake_diag_gate_seen = True
            _pending_now = len(self._wake_pending) if hasattr(self, "_wake_pending") else -1
            _ring_now = len(self._wake_ring.peek_latest(9999)) if hasattr(self, "_wake_ring") else -1
            print(
                "[WakeDiag] is_wake_word_detected() first call | "
                f"model_loaded={model is not None} | "
                f"tts_active={self._tts_active.is_set()} | "
                f"wake_pending_bytes={_pending_now} | "
                f"wake_ring_chunks={_ring_now}"
            )

        # OpenWakeWord is the ONLY passive wake detector.
        # No Whisper fallback.
        if model is None:
            if getattr(Config, "DEBUG_MODE", False):
                print("[WakeDebug] OpenWakeWord model is unavailable.")
            return False

        now = time.monotonic()

        with self._threshold_lock:
            last_triggered = self._wakeword_last_triggered

        # Prevent repeated triggers.
        if now - last_triggered < self._wakeword_cooldown:
            return False

        # Never listen for wake word while TTS is active.
        if self._tts_active.is_set():
            # Discard any audio accumulated while speaking to prevent an
            # instant backlog (and false positives from her own echo)
            # the moment she stops.
            self._wake_ring.get_all(clear=True)
            self._wake_pending.clear()
            return False

        # Do not run inference more often than configured.
        inference_interval = float(
            getattr(
                Config,
                "WAKE_WORD_INFERENCE_INTERVAL_S",
                0.08,
            )
        )

        if now - self._wakeword_last_inference < inference_interval:
            return False

        # OpenWakeWord must consume NEW audio from the dedicated RAW
        # microphone stream.
        new_chunks = self._wake_ring.get_all(clear=True)

        if new_chunks:
            self._wake_pending.extend(b"".join(new_chunks))

        frame_samples = int(
            self.SAMPLE_RATE
            * getattr(Config, "WAKE_WORD_FRAME_MS", 80)
            / 1000
        )

        # Defensive OpenWakeWord frame size.
        if frame_samples != 1280:
            frame_samples = 1280

        frame_bytes = frame_samples * self.SAMPLE_WIDTH

        if len(self._wake_pending) < frame_bytes:
            return False

        self._wakeword_last_inference = now
        
        processed_any = False
        max_score = 0.0
        scores = {}

        # Consume ALL available 80 ms frames sequentially.
        # This prevents unbounded accumulation and real-time drift if the
        # polling caller is slower than the ingestion rate.
        while len(self._wake_pending) >= frame_bytes:
            frame = np.frombuffer(
                bytes(self._wake_pending[:frame_bytes]),
                dtype=np.int16,
            )
            del self._wake_pending[:frame_bytes]
            
            try:
                frame_scores = model.predict(frame)
                processed_any = True
                
                # We need the highest score across the catch-up batch
                frame_max = max(frame_scores.values(), default=0.0)
                if frame_max > max_score:
                    max_score = frame_max
                    scores = frame_scores
            except Exception as e:
                _log_rate_limited(
                    "is_wake_word_detected_predict",
                    "[STT] OpenWakeWord predict failed during catch-up: %s: %s",
                    type(e).__name__,
                    e,
                )
                break
                
        if not processed_any:
            return False

        try:
            threshold = float(
                getattr(
                    Config,
                    "WAKE_WORD_THRESHOLD",
                    0.50,
                )
            )

            # TUNING (latency/hot-path cost): this diagnostic used to run
            # unconditionally every ~1s for the entire time the wake loop
            # is active (i.e. all idle runtime), doing string formatting
            # + a blocking print() on a path that's already polled every
            # WAKE_WORD_INFERENCE_INTERVAL_S (default 0.08s). Now gated
            # behind DEBUG_MODE -- same peak-score-tracking output when
            # actively debugging wake detection, zero cost otherwise.
            if getattr(Config, "DEBUG_MODE", False):
                # Track the PEAK score within each 1-second window (not
                # just whichever frame happens to land on the throttle
                # boundary) so a brief spike is never missed.
                if max_score > self._wake_diag_window_max:
                    self._wake_diag_window_max = max_score
                if now - self._wake_diag_window_start >= 1.0:
                    print(
                        f"[WakeDiag] 1s-peak score={self._wake_diag_window_max:.4f} | "
                        f"threshold={threshold:.4f} | raw={scores}"
                    )
                    self._wake_diag_window_start = now
                    self._wake_diag_window_max = 0.0

            if max_score >= threshold:
                with self._threshold_lock:
                    self._wakeword_last_triggered = now

                print(
                    f"[STT] ✅ Wake word detected | "
                    f"score={max_score:.4f} | "
                    f"threshold={threshold:.4f}"
                )

                return True

            return False

        except (ValueError, TypeError, RuntimeError) as e:
            _log_rate_limited(
                "is_wake_word_detected",
                "[STT] OpenWakeWord inference failed "
                "(no passive wake): %s: %s",
                type(e).__name__,
                e,
            )
            return False

        except Exception as e:
            _log_rate_limited(
                "is_wake_word_detected",
                "[STT] OpenWakeWord inference raised an unexpected "
                "error (no passive wake): %s: %s",
                type(e).__name__,
                e,
                exc_info=True,
            )
            return False

    def decode_barge_ring(self, max_ms: int = 700) -> str | None:
        """Decode the most recent `max_ms` of the AEC/NS-processed ring with
        the small preview model. None on any failure, missing model or
        timeout (~0.4 s). Never takes self._listen_lock."""
        from sara.audio.stt.barge_decode import decode_recent

        return decode_recent(self, max_ms)

    def set_barge_boost(self, factor: float) -> None:
        """Scale the TTS-time barge-in energy gate (1.0 = unchanged).
        Never raises."""
        try:
            self._barge_boost = max(1.0, min(4.0, float(factor)))
        except (TypeError, ValueError):
            self._barge_boost = 1.0

    def is_user_speaking(self, duration: float = 0.3) -> bool:
        if self._closed:
            return False
        try:
            n = max(1, int((self.SAMPLE_RATE / self.CHUNK_SIZE) * duration))
            chunks = self._ring.peek_latest(n=n)
            if not chunks:
                return False

            thr = self.energy_threshold
            tts_playing = self._tts_active.is_set()

            if not tts_playing:
                return any(_rms(c) > thr for c in chunks)

            # D63: same cap as _passes_barge_in_gate -- the two must agree
            # about how loud an interruption has to be, and neither may
            # inherit D65's noise-scaled capture threshold.
            effective_thr = self._barge_in_effective_threshold(
                float(getattr(self, "_barge_boost", 1.0))
            )
            loud = [c for c in chunks if _rms(c) > effective_thr]
            if not loud:
                return False

            vad_confirmed = sum(1 for c in loud if self._vad.is_speech(c))
            return vad_confirmed >= max(1, int(len(loud) * 0.6))
        except (ValueError, TypeError) as e:
            _log_rate_limited(
                "is_user_speaking",
                "[STT] is_user_speaking check failed (treating as not speaking): %s: %s",
                type(e).__name__, e,
            )
            return False
        except Exception as e:
            _log_rate_limited(
                "is_user_speaking",
                "[STT] is_user_speaking raised an unexpected error type "
                "(this may be a bug; treating as not speaking): %s: %s",
                type(e).__name__, e,
                exc_info=True,
            )
            return False

    # TUNING (robustness): validated against a fixed allow-list before
    # touching Config, instead of accepting any string unconditionally.
    # Previously any value (e.g. "hinglish", which sara/gui/app/settings.py
    # never actually sends today, but a future caller could) would be
    # written straight into Config.STT_LANGUAGE and handed to
    # faster-whisper's model.transcribe(language=...) as-is via
    # _resolve_forced_language() -- "hinglish" is not a valid Whisper
    # ISO-639-1 language code, so that call would misbehave/error instead
    # of transcribing anything. "en"/"hi"/"auto" (the only values this
    # project's GUI currently sends) are completely unaffected by this
    # change; an unrecognized value is now safely ignored (with a printed
    # warning) instead of being written through blindly.
    _VALID_STT_LANGS = frozenset({"en", "hi"})

    def set_language(self, lang: str) -> None:
        # BUGFIX: _resolve_forced_language() only honors Config.STT_LANGUAGE
        # when Config.LANG_DETECTION_MODE == "manual" — this flag was never
        # being set anywhere, so a manually forced language was silently
        # ignored during transcription. Also handle "auto" so switching
        # back actually clears the force instead of leaving it stuck.
        lang = (lang or "").lower().strip()
        if lang == "auto":
            Config.LANG_DETECTION_MODE = "auto"
            Config.STT_LANGUAGE = None
        elif lang in self._VALID_STT_LANGS:
            Config.STT_LANGUAGE = lang
            Config.LANG_DETECTION_MODE = "manual"
        else:
            print(
                f"[STT] set_language('{lang}') ignored -- not a recognized "
                f"Whisper language code (expected one of {sorted(self._VALID_STT_LANGS)} "
                f"or 'auto'). Forced language, if any, is unchanged."
            )
            return
        print(f"[STT] Language set to '{lang}'.")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._recalib_event.set()
        self._watchdog_event.set()  # v8.1: wake watchdog thread so it exits promptly
        self._close_stream()
        print("[STT] Closed.")

    def __enter__(self) -> "SpeechToText":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _log_init(self) -> None:
        wake_beam = int(getattr(Config, "WAKE_WORD_BEAM_SIZE", 1))
        # D35: do not call it "Ready" when the one component that makes this
        # a speech engine is missing. The old line said "Ready" with
        # FasterWhisper=✗ among five ticks, which reads as success.
        state = "Ready" if self._whisper_model is not None else "DEGRADED (no STT model)"
        print(
            f"[STT] {state} — "
            # D37: this used to read '✓ (CPU INT8)' -- a HARDCODED string,
            # printed regardless of the device actually in use. On a machine
            # where CUDA worked, the load line said "loaded on GPU" and this
            # line said "CPU INT8" two lines later, which is how a GPU claim
            # became unverifiable from the logs. It now reports what
            # _load_faster_whisper recorded.
            f"FasterWhisper="
            f"{'✓ ' + getattr(self, '_model_device', 'loaded, device unknown') if self._whisper_model else '✗'} | "
            # D52: report the PREVIEW model's device too. It was being
            # recorded and never read -- dead code that looks like a
            # feature, which is the trap D59 was reverted for. It matters
            # because this is the model whose partial transcript gates the
            # 350ms endpoint path (D51), so "is it on the GPU" is the
            # question a trace has to answer.
            f"Preview="
            f"{getattr(self, '_wake_model_device', 'none') if self._wake_whisper_model else '✗'} | "
            f"VAD={'✓' if _HAS_VAD else '✗'} | "
            f"AEC={'✓ (worker thread)' if (self._aec is not None and getattr(self._aec, 'enabled', False)) else '✗'} | "
            f"WakeWord(OpenWakeWord)={'✓' if self._wakeword_model else '✗ (model unavailable)'} | "
            f"WakeWords={self._wake_variants} | WakeBeam={wake_beam} | "
            f"ForcedLang={self._resolve_forced_language()} | "
            f"threshold={self.energy_threshold:.0f}"
        )