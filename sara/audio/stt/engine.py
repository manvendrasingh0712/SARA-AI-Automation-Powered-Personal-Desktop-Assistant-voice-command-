"""
sara.audio.stt.engine
SpeechToText -- the public class. Wires helpers + buffers together into the
mic-capture -> VAD -> faster-whisper transcription pipeline.
"""
from __future__ import annotations

from .helpers import _rms, _rms_numpy, _detect_language, _lang_from_stt_language, _is_hallucinated_repetition, _is_known_hallucination
from .buffers import _PreBuffer, _RingBuffer, _VADFilter, _SilenceGate, _NoiseFloor, _CollectState

import os
import atexit
import collections
import math
import re
import struct
import threading
import time
from enum import Enum, auto
from typing import Deque, List, Optional, Tuple

import numpy as np

if os.name == "nt":
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        bin_path = os.path.join(cuda_path, "bin")
        if os.path.isdir(bin_path):
            try:
                os.add_dll_directory(bin_path)
                print(f"[STT] CUDA DLL Path Added: {bin_path}")
            except (AttributeError, FileNotFoundError):
                pass

try:
    from faster_whisper import WhisperModel

    _HAS_WHISPER = True
except (ImportError, OSError) as e:
    _HAS_WHISPER = False
    print(f"[STT] faster-whisper import failed; offline STT features disabled. ({e})")

try:
    import webrtcvad

    _HAS_VAD = True
except (ImportError, OSError):
    webrtcvad = None
    _HAS_VAD = False

try:
    import sounddevice as sd

    _HAS_SD = True
except (ImportError, OSError):
    _HAS_SD = False

try:
    from openwakeword.model import Model as _OWWModel

    _HAS_WAKEWORD = True
except (ImportError, OSError):
    _OWWModel = None
    _HAS_WAKEWORD = False

try:
    import pyaudio as _pyaudio

    _HAS_PYAUDIO = True
except (ImportError, OSError):
    _pyaudio = None
    _HAS_PYAUDIO = False

try:
    import audioop as _audioop

    _HAS_AUDIOOP = True
except ImportError:
    _audioop = None
    _HAS_AUDIOOP = False

import queue

from config import Config

# ══════════════════════════════════════════════════════════════════════
# Audio Math & Utilities
# ══════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════
# AEC input queue tuning
# ══════════════════════════════════════════════════════════════════════

_AEC_QUEUE_MAXSIZE = 100
_AEC_QUEUE_IDLE_POLL_S = 0.5


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

        # v7: raw mic chunks awaiting AEC processing on a background
        # thread (only used when aec is not None — see _audio_callback).
        self._aec_raw_q: "queue.Queue[bytes]" = queue.Queue(maxsize=_AEC_QUEUE_MAXSIZE)
        self._aec_drop_count = 0

        # Load models directly into VRAM
        self._whisper_model = self._load_faster_whisper()
        self._wakeword_model: Optional["_OWWModel"] = self._load_wakeword()

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
        return self._wake_re.search(text) is not None

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
                device="cuda",
                compute_type="float16",
                cpu_threads=cpu_threads,
            )

            print("[STT] ✅ Faster Whisper loaded on CUDA.")
            return model

        except Exception as cuda_error:
            print(f"[STT Warning] CUDA initialization failed:\n{cuda_error}")
            print("[STT] Falling back to CPU...")

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
            min_gap = float(getattr(Config, "STT_SETTLE_MIN_GAP_S", 1.3))
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

    def _ingest_processed_chunk(self, chunk: bytes) -> None:
        if self._tts_active.is_set():
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
        self, timeout: float, max_duration: float, silence_limit: float
    ) -> bytes:
        thr = self.energy_threshold
        silence_limit_n = max(
            1, int(silence_limit * (self.SAMPLE_RATE / self.CHUNK_SIZE))
        )
        state = _CollectState.WAITING
        speech_chunks: List[bytes] = []
        silence_count, trailing_silence_chunks = 0, 0
        speech_start = 0.0
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

    @staticmethod
    def _normalize_audio_float32(
        audio_bytes: bytes, target_peak: float = 0.95
    ) -> np.ndarray:
        arr = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        peak = np.max(np.abs(arr))
        if peak > 0 and peak < target_peak:
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

    def _transcribe(
        self, audio_bytes: bytes, beam_size_override: Optional[int] = None
    ) -> str:
        if not audio_bytes or self._whisper_model is None:
            return ""

        duration_s = len(audio_bytes) / (self.SAMPLE_RATE * self.SAMPLE_WIDTH)
        if duration_s < 0.20:
            return ""

        try:
            audio_np = self._normalize_audio_float32(audio_bytes)

            forced_lang = self._resolve_forced_language()
            beam_size = (
                int(beam_size_override)
                if beam_size_override is not None
                else int(getattr(Config, "WHISPER_BEAM_SIZE", 3))
            )
            no_speech_thr = float(getattr(Config, "STT_NO_SPEECH_THRESHOLD", 0.6))

            # v8.2: temperature fallback ladder (see v8.2 CHANGES note at top
            # of file). Config.STT_TEMPERATURE_FALLBACK can override this;
            # default matches Whisper's own standard ladder. Only the first
            # (0.0, greedy/beam) pass runs for normal speech — later, higher
            # temperatures are only attempted by faster-whisper itself if
            # that first pass fails no_speech/log_prob/compression_ratio
            # checks, so this adds zero latency in the common case.
            temperature = getattr(
                Config, "STT_TEMPERATURE_FALLBACK", (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
            )

            segments, _ = self._whisper_model.transcribe(
                audio_np,
                beam_size=beam_size,
                best_of=beam_size,
                temperature=temperature,
                language=forced_lang,
                task="transcribe",
                initial_prompt=self._STATIC_TRANSCRIBE_PROMPT,
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

            min_repeats = int(getattr(Config, "STT_HALLUCINATION_MIN_REPEATS", 3))
            if _is_hallucinated_repetition(text, min_repeats=min_repeats):
                if getattr(Config, "DEBUG_MODE", False):
                    print(f"[STT] Discarded hallucinated repetition: {text[:80]!r}...")
                return ""

            # BUGFIX: see _HALLUCINATION_PHRASES docstring in helpers.py —
            # catches single-shot boilerplate hallucinations (e.g.
            # "Subtitles by the Amara.org community") that the repetition
            # check above can't, since they only appear once per capture.
            if _is_known_hallucination(text):
                if getattr(Config, "DEBUG_MODE", False):
                    print(f"[STT] Discarded known hallucination phrase: {text[:80]!r}")
                return ""

            if text:
                with self._transcript_lock:
                    self._recent_transcript = (self._recent_transcript + " " + text)[
                        -500:
                    ]
                self._update_detected_language(text)

            return text

        except Exception as e:
            print(f"[STT Error] Faster-Whisper Inference Failed: {e}")
            return ""

    def listen(self, mode: str = "command") -> str:
        if self._closed:
            return ""

        if not self._listen_lock.acquire(blocking=False):
            if getattr(Config, "DEBUG_MODE", False):
                print(
                    f"[STT] listen(mode='{mode}') skipped — another listen() session is already active."
                )
            return ""

        try:
            cfg = {
                "wake": {"timeout": 3.0, "max_duration": 5.0},
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
            )
            if not audio:
                return ""

            beam_override = (
                int(getattr(Config, "WAKE_WORD_BEAM_SIZE", 1))
                if mode == "wake"
                else None
            )
            return self._transcribe(audio, beam_size_override=beam_override)
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

        text = self.listen(mode="wake")
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
        else:
            Config.STT_LANGUAGE = lang
            Config.LANG_DETECTION_MODE = "manual"
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
            f"FasterWhisper={'✓ (CUDA FP16)' if self._whisper_model else '✗'} | "
            f"VAD={'✓' if _HAS_VAD else '✗'} | "
            f"AEC={'✓ (worker thread)' if (self._aec is not None and getattr(self._aec, 'enabled', False)) else '✗'} | "
            f"WakeWord(model)={'✓' if self._wakeword_model else '✗ (using STT fallback)'} | "
            f"WakeWords={self._wake_variants} | WakeBeam={wake_beam} | "
            f"ForcedLang={self._resolve_forced_language()} | "
            f"threshold={self.energy_threshold:.0f}"
        )
