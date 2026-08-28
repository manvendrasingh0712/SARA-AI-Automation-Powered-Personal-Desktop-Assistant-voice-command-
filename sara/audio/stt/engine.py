"""
sara.audio.stt.engine
SpeechToText -- the public class. Wires helpers + buffers together into the
mic-capture -> VAD -> faster-whisper transcription pipeline.
"""
from __future__ import annotations

from .helpers import _rms, _detect_language, _lang_from_stt_language, _is_hallucinated_repetition, _is_known_hallucination
from .buffers import _PreBuffer, _RingBuffer, _VADFilter, _SilenceGate, _NoiseFloor, _CollectState

import os
import atexit
import collections
import difflib
import re
import threading
import time
from typing import Callable, List, Optional

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

    def __new__(cls, text: str, confidence: float = 0.0) -> "TranscriptionResult":
        obj = super().__new__(cls, text)
        obj.confidence = float(confidence)
        return obj

# ══════════════════════════════════════════════════════════════════════
# Audio Math & Utilities
# ══════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════
# AEC input queue tuning
# ══════════════════════════════════════════════════════════════════════

_AEC_QUEUE_MAXSIZE = 100
_AEC_QUEUE_IDLE_POLL_S = 0.5

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
# Hindi words): the original static prompt below is Hindi/Hinglish-styled
# unconditionally, even when Config.SARA_LANGUAGE is "english" and
# forced_lang resolves to something other than Hindi. Whisper's
# initial_prompt measurably steers transcription style/vocabulary, so an
# English-only setup was silently nudged toward Hindi-flavored output it
# never asked for. _get_transcribe_prompt() below picks the right one.
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

    # v8: first recalibration happens promptly (AEC/NS shifts the ambient
    # noise profile immediately at startup, so the energy threshold
    # shouldn't wait the full steady-state interval to adapt once).
    _RECALIB_FIRST_INTERVAL: float = 15.0
    _RECALIB_INTERVAL: float = 300.0

    # v8.1: mic-disconnect watchdog poll interval.
    _WATCHDOG_INTERVAL: float = 7.0

    def __init__(self, aec=None) -> None:
        """
        aec: optional sara.audio.aec.AECProcessor instance, shared with the
        TextToSpeech engine, constructed once in build_core_objects(). Raw
        mic chunks are handed off to a dedicated background worker thread
        which runs them through aec.process_near_end() before they reach
        the pre-speech/ring buffers — never inline on the real-time audio
        callback thread. If omitted, behaves exactly as before (no
        cancellation, zero added overhead).
        """
        self._closed = False
        self._aec = aec

        self._threshold_lock = threading.Lock()
        self._energy_threshold: float = float(
            getattr(Config, "BARGE_IN_ENERGY_THRESHOLD", 500)
        )
        self._manual_threshold_until: float = 0.0

        self._silence_gate = _SilenceGate()
        self._noise_floor = _NoiseFloor()
        self._vad = _VADFilter(self.SAMPLE_RATE, aggressiveness=2)

        self._pre_buf = _PreBuffer(
            self.SAMPLE_RATE, self.CHUNK_SIZE, self.PRE_SPEECH_MS
        )
        self._ring = _RingBuffer(maxlen=300)

        self._recent_transcript: str = ""
        self._transcript_lock = threading.Lock()

        self._detected_lang: str = "en"

        self._wakeword_last_triggered: float = 0.0
        self._wakeword_cooldown: float = float(
            getattr(Config, "WAKE_WORD_COOLDOWN_S", 2.0)
        )

        self._wake_variants = self._build_wake_variants()
        self._wake_re = self._compile_wake_regex(self._wake_variants)

        self._recalib_event = threading.Event()
        self._watchdog_event = threading.Event()  # v8.1: mic-disconnect watchdog
        self._tts_active = threading.Event()
        self._tts_stopped_at: float = 0.0
        self._tts_state_lock = threading.Lock()
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

        # v7: raw mic chunks awaiting AEC processing on a background
        # thread (only used when aec is not None — see _audio_callback).
        self._aec_raw_q: "queue.Queue[bytes]" = queue.Queue(maxsize=_AEC_QUEUE_MAXSIZE)
        self._aec_drop_count = 0

        # Load models directly into VRAM
        self._whisper_model = self._load_faster_whisper()
        self._wakeword_model: Optional["_OWWModel"] = self._load_wakeword()

        # Dedicated small/fast Whisper model for the STT-fallback wake-word
        # check in is_wake_word_detected() below -- only loaded when there's
        # no dedicated openwakeword model, since that's the only code path
        # that uses it. Using the same heavy WHISPER_MODEL_SIZE model just
        # to check "did they say Sara?" was the main latency+accuracy
        # bottleneck: every wake attempt paid the full large-model
        # transcription cost, on top of a fixed 3-5s capture window sized
        # for full commands, not a one-word wake phrase.
        self._wake_whisper_model: Optional[object] = None
        if self._wakeword_model is None:
            self._wake_whisper_model = self._load_fast_wake_whisper()

        self._stream = None
        self._pa = None
        self._stream_lock = threading.Lock()
        self._open_mic_stream()

        self._start_threads()
        atexit.register(self.close)
        self._log_init()

    # ── Wake word helpers ────────────────────────────────────────────

    @staticmethod
    def _build_wake_variants() -> List[str]:
        cfg_words = getattr(Config, "WAKE_WORDS", None)
        if cfg_words:
            variants = [w.strip().lower() for w in cfg_words if w and w.strip()]
        else:
            raw = getattr(Config, "WAKE_WORD", "sara")
            variants = [w.strip().lower() for w in re.split(r"[,;]", raw) if w.strip()]

        for must in ("sara", "sarah", "hey sara", "hey sarah"):
            if must not in variants:
                variants.append(must)
        return variants

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

    def _load_faster_whisper(self) -> Optional[object]:
        if not _HAS_WHISPER:
            print("[STT Error] faster-whisper not installed. Offline STT will fail.")
            return None

        model_size = getattr(Config, "WHISPER_MODEL_SIZE", "large-v3-turbo")

        cpu_threads = max(4, (os.cpu_count() or 4) // 2)

        print(f"[STT] Loading Faster-Whisper '{model_size}'...")

        try:
            model = WhisperModel(
                model_size_or_path=model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
            )

            print("[STT] ✅ Faster Whisper loaded on CPU.")
            return model

        except Exception as cpu_error:
            print(f"[STT Error] CPU initialization failed:\n{cpu_error}")
            return None

    def _load_fast_wake_whisper(self) -> Optional[object]:
        """
        Small/fast dedicated Whisper model used ONLY for the STT-fallback
        wake-word check (see is_wake_word_detected()). Failure here is
        non-fatal -- is_wake_word_detected() falls back to the main
        (larger, slower) self._whisper_model if this is None, so wake
        detection still works either way, just without the latency win.
        """
        if not _HAS_WHISPER:
            return None
        model_size = getattr(Config, "WAKE_WORD_FAST_MODEL_SIZE", "tiny")
        cpu_threads = max(2, (os.cpu_count() or 4) // 4)
        print(f"[STT] Loading dedicated wake-word Whisper '{model_size}'...")
        try:
            model = WhisperModel(
                model_size_or_path=model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
            )
            print("[STT] ✅ Wake-word Whisper loaded on CPU.")
            return model
        except Exception as e:
            print(
                f"[STT Warning] Dedicated wake-word model load failed "
                f"({e}) -- wake checks will use the main model instead."
            )
            return None

    def _load_wakeword(self) -> Optional["_OWWModel"]:
        if not _HAS_WAKEWORD or not _OWWModel:
            return None
        model_path = getattr(Config, "WAKE_WORD_MODEL_PATH", None)
        if not model_path:
            print(
                "[STT] No custom wake-word model configured (WAKE_WORD_MODEL_PATH unset) "
                "— using STT-based fallback wake detection for: "
                f"{', '.join(self._wake_variants)}"
            )
            return None
        try:
            return _OWWModel(wakeword_models=[model_path], inference_framework="onnx")
        except Exception as e:
            print(f"[STT Warning] Wake word model load failed: {e}")
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
            self._tts_active.set()
            self._pre_buf.clear()
        else:
            self._tts_active.clear()

    def mark_tts_stopped(self) -> None:
        with self._tts_state_lock:
            self._tts_stopped_at = time.monotonic()
        self._pre_buf.clear()
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
        self._ring.get_all(clear=True)
        self._pre_buf.clear()

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
            return True
        except Exception as e:
            print(f"[STT Error] sounddevice open failed: {e}")
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
            return True
        except Exception as e:
            print(f"[STT Error] PyAudio open failed: {e}")
            return False

    def _apply_aec(self, chunk: bytes) -> bytes:
        if self._aec is None:
            return chunk
        try:
            return self._aec.process_near_end(chunk)
        except Exception:
            return chunk

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
        bleed_multiplier = float(getattr(Config, "TTS_BLEED_GUARD_MULTIPLIER", 1.6))
        effective_thr = self.energy_threshold * bleed_multiplier
        if _rms(chunk) <= effective_thr:
            return False
        return self._vad.is_speech(chunk)

    def _ingest_processed_chunk(self, chunk: bytes) -> None:
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
        if status and getattr(Config, "DEBUG_MODE", False):
            print(f"[STT] input stream status: {status}")

        chunk = bytes(indata)
        if self._aec is None:
            self._ingest_processed_chunk(chunk)
            return

        try:
            self._aec_raw_q.put_nowait(chunk)
        except queue.Full:
            self._aec_drop_count += 1
            if getattr(Config, "DEBUG_MODE", False) and self._aec_drop_count % 50 == 1:
                print(
                    f"[STT WARNING] AEC input queue full — dropped "
                    f"{self._aec_drop_count} mic chunk(s) so far. This means "
                    f"something is holding up AEC processing far longer than "
                    f"expected; investigate CPU/GPU load."
                )

    def _pa_callback(self, in_data, frame_count, time_info, status_flags):
        if self._closed:
            return (None, _pyaudio.paContinue if _HAS_PYAUDIO else None)

        if status_flags and getattr(Config, "DEBUG_MODE", False):
            print(f"[STT] pyaudio input status flags: {status_flags}")

        if self._aec is None:
            self._ingest_processed_chunk(in_data)
        else:
            try:
                self._aec_raw_q.put_nowait(in_data)
            except queue.Full:
                self._aec_drop_count += 1
                if (
                    getattr(Config, "DEBUG_MODE", False)
                    and self._aec_drop_count % 50 == 1
                ):
                    print(
                        f"[STT WARNING] AEC input queue full — dropped "
                        f"{self._aec_drop_count} mic chunk(s) so far (pyaudio path)."
                    )

        return (None, _pyaudio.paContinue if _HAS_PYAUDIO else None)

    def _aec_worker_loop(self) -> None:
        while not self._closed:
            try:
                chunk = self._aec_raw_q.get(timeout=_AEC_QUEUE_IDLE_POLL_S)
            except queue.Empty:
                continue
            processed = self._apply_aec(chunk)
            self._ingest_processed_chunk(processed)

    def _start_threads(self) -> None:
        threading.Thread(
            target=self._recalib_loop, daemon=True, name="stt-recalib"
        ).start()
        if self._aec is not None:
            threading.Thread(
                target=self._aec_worker_loop, daemon=True, name="stt-aec-worker"
            ).start()
        # v8.1: mic-disconnect watchdog (see v8.1 CHANGES note at top of file).
        threading.Thread(
            target=self._watchdog_loop, daemon=True, name="stt-mic-watchdog"
        ).start()

    def _recalib_loop(self) -> None:
        # v8: first cycle uses a short interval so the energy threshold
        # can adapt to AEC/NS's altered noise profile promptly instead of
        # waiting the full steady-state interval on a fresh start.
        first_wait = min(self._RECALIB_FIRST_INTERVAL, self._RECALIB_INTERVAL)
        woken = self._recalib_event.wait(timeout=first_wait)
        if woken:
            self._recalib_event.clear()

        while not self._closed:
            if not (self._is_listening.is_set() or self._tts_active.is_set()):
                self._run_one_recalibration()

            woken = self._recalib_event.wait(timeout=self._RECALIB_INTERVAL)
            if self._closed:
                break
            if woken:
                self._recalib_event.clear()

    def _run_one_recalibration(self) -> None:
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
            return

        suggested = self._noise_floor.suggested_threshold(margin=250.0)
        new_thr = suggested if suggested is not None else (avg + 200.0)
        new_thr = min(max(new_thr, thr - 150.0), thr + 150.0)

        self.energy_threshold = new_thr

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

        self._is_listening.set()
        backlog = self._ring.get_all(clear=True)

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
                        if self._vad.is_speech(chunk) or _rms(chunk) > thr * 0.35:
                            pre = self._pre_buf.drain()
                            speech_chunks = [pre, chunk] if pre else [chunk]
                            silence_count = 0
                            speech_start = time.monotonic()
                            last_preview_time = speech_start
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
                    # a background best-effort transcribe of what's been
                    # captured SO FAR using the fast/small wake-word model,
                    # so the caller (see on_partial_transcript) can show a
                    # provisional dim/italic caption before the real,
                    # accurate transcript is ready. Gated on self._preview_busy
                    # so at most one preview transcribe runs at a time -- if
                    # the previous one hasn't finished yet, this cycle is
                    # just skipped rather than piling up more threads.
                    if (
                        state is _CollectState.SPEAKING
                        and on_partial_transcript is not None
                        and now - last_preview_time >= 0.9
                        and not self._preview_busy.is_set()
                    ):
                        last_preview_time = now
                        self._spawn_preview_transcribe(
                            b"".join(speech_chunks), on_partial_transcript
                        )

                    if state is _CollectState.SPEAKING:
                        if not pending_chunks:
                            self._ring.wait(timeout=0.05)
                            pending_chunks = self._ring.get_all(clear=True)

                        for chunk in pending_chunks:
                            energy = _rms(chunk)
                            energy_window.append(energy)
                            is_voice = self._vad.is_speech(chunk) or (
                                sum(energy_window) / len(energy_window) > thr * 0.3
                            )

                            speech_chunks.append(chunk)
                            if is_voice:
                                silence_count, trailing_silence_chunks = 0, 0
                            else:
                                silence_count += 1
                                trailing_silence_chunks += 1
                                if silence_count >= silence_limit_n:
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
        self, snapshot: bytes, on_partial_transcript: Callable[[str], None]
    ) -> None:
        """
        Best-effort, fire-and-forget "live caption" preview: transcribes
        the speech collected SO FAR (using the same small/fast model as
        the STT-fallback wake-word check, NOT the slower main model) on a
        background thread, so the caller can show a provisional caption
        while the user is still talking. Deliberately never touches the
        real/accurate transcription path in listen()/_transcribe() -- any
        exception here (model missing, transcribe error, callback
        raising) is swallowed so a preview glitch can never crash or
        stall the main _collect_speech() loop. self._preview_busy is set
        here (before the thread starts) and cleared in the thread's
        finally block, so _collect_speech() can tell a preview is still
        in flight and skip spawning another one on top of it.
        """
        self._preview_busy.set()

        def _run() -> None:
            try:
                result = self._transcribe(
                    snapshot,
                    beam_size_override=1,
                    model_override=self._wake_whisper_model,
                )
                if result:
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

        force_for_hinglish = getattr(Config, "STT_FORCE_LANG_FOR_HINGLISH", True)
        sara_lang = getattr(Config, "SARA_LANGUAGE", "hinglish")
        if force_for_hinglish and sara_lang in ("hindi", "hinglish"):
            return "hi"
        return None

    # v8: static, non-echoing style-guidance prompt. Deliberately contains
    # NO dynamic/previous-turn content — see v8 changelog at the top of
    # this file for why feeding the model its own prior output back as a
    # prompt is a direct hallucination-repetition trigger.
    _STATIC_TRANSCRIBE_PROMPT = (
        "यह बातचीत हिंदी, इंग्लिश और हिंग्लिश में हो सकती है। "
        "Transcribe naturally without translating, keep proper names exactly as spoken, "
        "do not invent words. Example style: 'aaj mujhe office jana hai', "
        "'mera naam Sara hai', 'kya haal hai bhai'."
    )

    def _get_transcribe_prompt(self, forced_lang: Optional[str]) -> str:
        """
        TUNING (accuracy): picks the style-guidance prompt actually
        appropriate for this turn instead of always using the
        Hindi/Hinglish-styled _STATIC_TRANSCRIBE_PROMPT. That prompt
        measurably steers Whisper's vocabulary/style, so unconditionally
        using it on a pure-English configuration was quietly biasing
        transcription toward Hindi words a user in English-only mode
        never wanted. Hindi/Hinglish styling is used only when it's
        actually relevant: forced_lang == "hi" (explicit forced-Hindi
        turn), or Config.SARA_LANGUAGE is "hindi"/"hinglish" (the
        project's default) with no more specific override in play.
        """
        sara_lang = getattr(Config, "SARA_LANGUAGE", "hinglish")
        if forced_lang == "hi" or (forced_lang is None and sara_lang in ("hindi", "hinglish")):
            return self._STATIC_TRANSCRIBE_PROMPT
        return _STATIC_TRANSCRIBE_PROMPT_EN

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
                Config, "STT_TEMPERATURE_FALLBACK", (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
            )

            segments, _ = model.transcribe(
                audio_np,
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

            # ── STT CONFIDENCE SIGNAL (NEW) ─────────────────────────────
            # Mean of (1 - no_speech_prob) across the SAME `usable`
            # segments already computed above -- free, no extra model
            # call. Restricted to `usable` so a segment thrown away by
            # the no_speech filter can't skew the confidence of the
            # segments that actually made it into `text`.
            if usable:
                confidence = sum(
                    1.0 - getattr(s, "no_speech_prob", 0.0) for s in usable
                ) / len(usable)
            else:
                confidence = 0.0

            # ── MINIMUM-CONFIDENCE REJECT GATE (NEW) ────────────────────
            # Separate from Config.STT_CONFIDENCE_CONFIRM_THRESHOLD (which
            # only gates the destructive-action spoken yes/no
            # confirmation, downstream in intent_handlers.py). This gate
            # discards the transcript outright, before it ever reaches
            # the intent router, when confidence is low AND the
            # transcript is short (<=3 words) — that combination is the
            # "phantom single word from silence/noise" shape that
            # neither _is_hallucinated_repetition() (needs repeats) nor
            # _is_known_hallucination() (needs a known boilerplate
            # phrase) can catch on their own. Deliberately NOT applied to
            # longer transcripts — a real 10-word sentence with one
            # muffled word shouldn't be thrown away over a mediocre
            # aggregate confidence.
            min_confidence_reject = float(
                getattr(Config, "STT_MIN_CONFIDENCE_REJECT", 0.35)
            )
            word_count = len(text.split()) if text else 0
            if text and confidence < min_confidence_reject and word_count <= 3:
                if getattr(Config, "DEBUG_MODE", False):
                    print(
                        f"[STT] Discarded low-confidence short transcript "
                        f"(confidence={confidence:.2f}, words={word_count}): "
                        f"{text[:80]!r}"
                    )
                return TranscriptionResult("", 0.0)

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

            if text:
                with self._transcript_lock:
                    self._recent_transcript = (self._recent_transcript + " " + text)[
                        -500:
                    ]
                self._update_detected_language(text)

            return TranscriptionResult(text, confidence)

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
            if not audio:
                return TranscriptionResult("", 0.0)

            beam_override = (
                int(getattr(Config, "WAKE_WORD_BEAM_SIZE", 1))
                if mode == "wake"
                else None
            )
            return self._transcribe(
                audio, beam_size_override=beam_override, model_override=model_override
            )
        finally:
            self._listen_lock.release()

    def is_wake_word_detected(self) -> bool:
        if self._closed:
            return False
        now = time.monotonic()
        if now - self._wakeword_last_triggered < self._wakeword_cooldown:
            return False
        if self._tts_active.is_set():
            return False

        if self._wakeword_model is not None:
            try:
                chunks = self._ring.peek_latest(
                    n=max(1, int(self.SAMPLE_RATE / self.CHUNK_SIZE))
                )
                if not chunks:
                    return False
                joined = b"".join(chunks)
                if _rms(joined) < self.energy_threshold * 0.25:
                    return False

                scores = self._wakeword_model.predict(
                    np.frombuffer(joined, dtype=np.int16)
                )
                threshold = float(getattr(Config, "WAKE_WORD_THRESHOLD", 0.5))
                if any(v >= threshold for v in scores.values()):
                    self._wakeword_last_triggered = now
                    return True
                return False
            except Exception:
                return False

        probe_n = max(1, int((self.SAMPLE_RATE / self.CHUNK_SIZE) * 0.3))
        probe_chunks = self._ring.peek_latest(n=probe_n)
        if not probe_chunks:
            return False
        probe_joined = b"".join(probe_chunks)
        has_energy = _rms(probe_joined) > self.energy_threshold * 0.5
        has_vad_speech = any(self._vad.is_speech(c) for c in probe_chunks)
        if not (has_energy or has_vad_speech):
            return False

        text = self.listen(mode="wake", model_override=self._wake_whisper_model)
        if not text:
            return False
        detected = self._text_has_wake_word(text)
        if detected:
            self._wakeword_last_triggered = now
        return detected

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

            bleed_multiplier = float(getattr(Config, "TTS_BLEED_GUARD_MULTIPLIER", 1.6))
            effective_thr = thr * bleed_multiplier
            loud = [c for c in chunks if _rms(c) > effective_thr]
            if not loud:
                return False

            vad_confirmed = sum(1 for c in loud if self._vad.is_speech(c))
            return vad_confirmed >= max(1, int(len(loud) * 0.6))
        except Exception:
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
        print(
            f"[STT] Ready — "
            f"FasterWhisper={'✓ (CPU INT8)' if self._whisper_model else '✗'} | "
            f"VAD={'✓' if _HAS_VAD else '✗'} | "
            f"AEC={'✓ (worker thread)' if (self._aec is not None and getattr(self._aec, 'enabled', False)) else '✗'} | "
            f"WakeWord(model)={'✓' if self._wakeword_model else '✗ (using STT fallback)'} | "
            f"WakeWords={self._wake_variants} | WakeBeam={wake_beam} | "
            f"ForcedLang={self._resolve_forced_language()} | "
            f"threshold={self.energy_threshold:.0f}"
        )