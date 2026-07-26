"""
sara.audio.tts.player
Persistent sounddevice/pygame playback worker shared across speak() calls.
"""
from __future__ import annotations

from .synth import _apply_volume


import os
import queue
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator, Optional

import numpy as np

from config import Config

try:
    import sounddevice as sd

    _SD_OK = True
except (ImportError, OSError):
    _SD_OK = False
    sd = None
    print("[TTS] sounddevice not found — pip install sounddevice")

try:
    import pygame

    _PG_OK = True
except ImportError:
    _PG_OK = False
    pygame = None

try:
    from kokoro_onnx import Kokoro

    _KOKORO_OK = True
except ImportError:
    _KOKORO_OK = False
    Kokoro = None
    print("[TTS] kokoro-onnx not found — pip install kokoro-onnx")

try:
    import onnxruntime as _ort

    _ORT_OK = True
except ImportError:
    _ORT_OK = False
    _ort = None

# ── Constants ─────────────────────────────────────────────────────────────────
_SAMPLE_RATE = 24000  # Kokoro v1.0 native output rate
_CHANNELS = 1
_POLL_S = 0.008
_MIN_CHUNK = 8
_MAX_CHUNK = 180
_FIRST_TRIGGER = 5  # lowered from 8 — flush first micro-chunk sooner
_QUEUE_TIMEOUT = 15.0

_PLAY_BUFFER_MS = int(getattr(Config, "TTS_PLAYBACK_BUFFER_MS", 40))
_PLAY_LATENCY = getattr(Config, "TTS_SD_LATENCY", "low")
_BLOCK_SIZE = max(256, int(_SAMPLE_RATE * _PLAY_BUFFER_MS / 1000))

# Sub-chunk size used when feeding PCM into the persistent player's queue —
# keeps individual queued items small so stop()/clear() during playback
# takes effect within a few blocks instead of after one giant array drains.
_ENQUEUE_CHUNK_SAMPLES = _BLOCK_SIZE * 4

# Bounded queue for handing played blocks off to the AEC far-end feeder
# thread. Small and lossy by design — dropping an occasional block just
# means a few ms less far-end reference data, which AEC tolerates fine;
# blocking the real-time callback to guarantee delivery is far worse.
_FAR_END_QUEUE_MAXSIZE = 64
_FAR_END_IDLE_POLL_S = 0.5

_ORT_INTRA_THREADS = int(getattr(Config, "ORT_INTRA_THREADS", os.cpu_count() or 4))
_ORT_INTER_THREADS = int(getattr(Config, "ORT_INTER_THREADS", 1))

_WARMUP_TEXTS_EN = ["Hi.", "This is a warm up sentence for the model."]
_WARMUP_TEXTS_HI = ["नमस्ते।"]
_WARMUP_WAIT_S = float(getattr(Config, "TTS_WARMUP_WAIT_S", 2.0))

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# CUDA availability, decided once at import time — drives adaptive queue sizing.
_CUDA_AVAILABLE = bool(
    _ORT_OK and "CUDAExecutionProvider" in _ort.get_available_providers()
)

# Adaptive queue sizing — GPU path synthesizes faster, so deeper queues keep
# the pipeline fed without wasting memory on CPU-only setups.
_SYNTH_QUEUE_SIZE = int(
    getattr(Config, "TTS_SYNTH_QUEUE_SIZE", 12 if _CUDA_AVAILABLE else 8)
)
_PLAY_QUEUE_SIZE = int(
    getattr(Config, "TTS_PLAY_QUEUE_SIZE", 6 if _CUDA_AVAILABLE else 4)
)

# Short-phrase PCM cache (greetings, acks, wake responses, etc.)
_PHRASE_CACHE_MAX = int(getattr(Config, "TTS_PHRASE_CACHE_SIZE", 64))
_PHRASE_CACHE_MAXLEN = int(getattr(Config, "TTS_PHRASE_CACHE_MAXLEN", 40))


# ══════════════════════════════════════════════════════════════════════════════
#  LANGUAGE DETECTION
# ══════════════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════════════
#  PERSISTENT PLAYER  (v11 — single long-lived OutputStream, non-blocking
#  real-time callback, AEC far-end feed on a dedicated background thread)
# ══════════════════════════════════════════════════════════════════════════════


class _PersistentPlayer:
    """
    One sounddevice OutputStream, opened once and kept alive for the whole
    TextToSpeech object lifetime, fed through a queue instead of being
    opened/closed per spoken segment.

    AEC far-end feed (v11): the real-time `_callback` does ONLY a
    queue.put_nowait() of the exact block it just wrote — no locks, no
    resampling, no native calls. A separate `_far_end_worker` thread
    drains that queue and does the actual `aec.feed_far_end()` work
    (resample + WebRTC APM call), completely off the real-time audio
    path. This is what "output underflow" in earlier logs was actually
    caused by — that work running directly inside the callback.
    """

    def __init__(self, aec=None) -> None:
        self._aec = aec
        self._chunk_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=256)
        self._current: Optional[np.ndarray] = None
        self._current_pos: int = 0
        self._clear_flag = threading.Event()
        self._stream = None
        self._closed = False

        self._far_end_q: "queue.Queue[np.ndarray]" = queue.Queue(
            maxsize=_FAR_END_QUEUE_MAXSIZE
        )
        self._far_end_stop = threading.Event()
        self._far_end_thread: Optional[threading.Thread] = None
        if self._aec is not None:
            self._far_end_thread = threading.Thread(
                target=self._far_end_worker, daemon=True, name="TTS-AEC-FarEnd"
            )
            self._far_end_thread.start()

        if _SD_OK and sd is not None:
            self._open_stream()
        elif getattr(Config, "DEBUG_MODE", False):
            print(
                "[TTS] sounddevice unavailable — persistent player will use "
                "pygame fallback (per-call, no continuous AEC reference)."
            )

    def _open_stream(self) -> None:
        try:
            self._stream = sd.OutputStream(
                samplerate=_SAMPLE_RATE,
                channels=_CHANNELS,
                dtype="int16",
                blocksize=_BLOCK_SIZE,
                latency=_PLAY_LATENCY,
                callback=self._callback,
            )
            self._stream.start()
            if getattr(Config, "DEBUG_MODE", False):
                print("[TTS] Persistent OutputStream opened.")
        except Exception as e:
            print(f"[TTS] Persistent OutputStream open failed: {e}")
            self._stream = None

    def _far_end_worker(self) -> None:
        """Runs off the real-time thread. Does the actual resample +
        WebRTC APM far-end feed work that used to live inside _callback."""
        while not self._far_end_stop.is_set():
            try:
                block = self._far_end_q.get(timeout=_FAR_END_IDLE_POLL_S)
            except queue.Empty:
                continue
            try:
                self._aec.feed_far_end(block, _SAMPLE_RATE)
            except Exception:
                pass

    def _callback(self, outdata, frames, time_info, status) -> None:
        if status and getattr(Config, "DEBUG_MODE", False):
            print(f"[TTS] output stream status: {status}")

        if self._clear_flag.is_set():
            self._current = None
            self._current_pos = 0
            self._clear_flag.clear()

        block = np.zeros(frames, dtype=np.int16)
        filled = 0
        while filled < frames:
            if self._current is None or self._current_pos >= len(self._current):
                try:
                    self._current = self._chunk_q.get_nowait()
                    self._current_pos = 0
                except queue.Empty:
                    break
            remaining = len(self._current) - self._current_pos
            take = min(frames - filled, remaining)
            block[filled : filled + take] = self._current[
                self._current_pos : self._current_pos + take
            ]
            self._current_pos += take
            filled += take

        outdata[:, 0] = block

        # v11: hand off to the background thread instead of processing
        # here — this line must stay a cheap, non-blocking, lock-free
        # enqueue, since it runs on the real-time audio thread.
        if self._aec is not None:
            try:
                self._far_end_q.put_nowait(block)
            except queue.Full:
                pass  # dropping one far-end block is harmless; blocking here is not

    def enqueue(self, pcm: np.ndarray) -> None:
        for i in range(0, len(pcm), _ENQUEUE_CHUNK_SAMPLES):
            self._chunk_q.put(pcm[i : i + _ENQUEUE_CHUNK_SAMPLES])

    def clear(self) -> None:
        while True:
            try:
                self._chunk_q.get_nowait()
            except queue.Empty:
                break
        self._clear_flag.set()

    def _pygame_play_and_wait(
        self, pcm: np.ndarray, stop_event: threading.Event
    ) -> bool:
        if not (_PG_OK and pygame is not None):
            return False
        if self._aec is not None:
            try:
                self._far_end_q.put_nowait(pcm)
            except queue.Full:
                pass
        try:
            stereo = np.column_stack([pcm, pcm])
            sound = pygame.sndarray.make_sound(stereo)
            channel = sound.play()
            if channel:
                while channel.get_busy():
                    if stop_event.is_set():
                        channel.stop()
                        return True
                    time.sleep(_POLL_S)
        except Exception as e:
            if getattr(Config, "DEBUG_MODE", False):
                print(f"[TTS] pygame fallback error: {e}")
        return False

    def play_and_wait(
        self, pcm: np.ndarray, stop_event: threading.Event, volume: float = 1.0
    ) -> bool:
        """Plays `pcm` (already-synthesized int16 audio) and blocks the
        CALLING thread until it finishes or `stop_event` is set. Returns
        True if interrupted. The actual audio hardware I/O happens on the
        persistent stream's own callback thread — this call just enqueues
        and polls, so it's safe to run this from a worker thread while
        other work (e.g. synthesizing the next segment) proceeds
        concurrently on the caller side."""
        if pcm is None or len(pcm) == 0:
            return False
        pcm = _apply_volume(pcm, volume)

        if self._stream is None:
            return self._pygame_play_and_wait(pcm, stop_event)

        self.enqueue(pcm)
        while True:
            if stop_event.is_set():
                self.clear()
                return True
            if self._chunk_q.empty() and (
                self._current is None or self._current_pos >= len(self._current)
            ):
                return False
            time.sleep(_POLL_S)

    def close(self) -> None:
        self._closed = True
        self._far_end_stop.set()
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass


def _drain(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break
