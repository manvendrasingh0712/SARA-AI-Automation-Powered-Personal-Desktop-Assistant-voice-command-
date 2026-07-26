"""
sara.audio.tts.synth
Kokoro ONNX synthesis call + post-synthesis volume shaping.
"""
from __future__ import annotations

from .voice_params import _VoiceParams
from .cache import _phrase_cache_get, _phrase_cache_put


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
#  KOKORO SYNTHESIS  (in-process, no subprocess, no temp files)
# ══════════════════════════════════════════════════════════════════════════════

_OUT_SCRATCH = threading.local()
_VOL_SCRATCH = threading.local()


def _synth_kokoro(
    text: str,
    kokoro_model: "Kokoro",
    params: _VoiceParams,
    lock: threading.Lock,
) -> np.ndarray | None:
    """
    Synthesize text via the persistent Kokoro ONNX session.
    Checks the short-phrase LRU cache first. Converts Kokoro's float32
    [-1,1] output to int16 using a thread-local scratch buffer to minimize
    per-call allocations.
    """
    cache_key = None
    if len(text) <= _PHRASE_CACHE_MAXLEN:
        cache_key = (text, params.voice, params.lang_code, params.speed)
        cached = _phrase_cache_get(cache_key)
        if cached is not None:
            return np.frombuffer(cached, dtype=np.int16).copy()

    try:
        with lock:  # serialize calls into the shared ONNX session
            samples, _sr = kokoro_model.create(
                text,
                voice=params.voice,
                speed=params.speed,
                lang=params.lang_code,
            )
        if samples is None or len(samples) == 0:
            if getattr(Config, "DEBUG_MODE", False):
                print(f'[TTS] kokoro returned empty audio for: "{text[:50]}"')
            return None

        arr = np.asarray(samples, dtype=np.float32)
        np.clip(arr, -1.0, 1.0, out=arr)

        buf = getattr(_OUT_SCRATCH, "buf", None)
        if buf is None or buf.shape[0] < arr.shape[0]:
            buf = np.empty(arr.shape[0], dtype=np.int16)
            _OUT_SCRATCH.buf = buf
        out = buf[: arr.shape[0]]
        np.multiply(arr, 32767.0, out=arr)
        np.copyto(out, arr.astype(np.int16, copy=False))
        result = out.copy()

        if cache_key is not None:
            _phrase_cache_put(cache_key, result.tobytes())

        return result
    except Exception as e:
        if getattr(Config, "DEBUG_MODE", False):
            print(f"[TTS] kokoro synth error: {e}")
        return None


def _apply_volume(pcm: np.ndarray, volume: float) -> np.ndarray:
    """Scale int16 PCM by `volume` using a thread-local scratch buffer to
    avoid a fresh allocation on every call. Returns `pcm` unchanged if
    volume is already 1.0 (the common case)."""
    if volume == 1.0 or pcm is None or len(pcm) == 0:
        return pcm
    buf = getattr(_VOL_SCRATCH, "buf", None)
    if buf is None or buf.shape[0] < pcm.shape[0]:
        buf = np.empty(pcm.shape[0], dtype=np.float32)
        _VOL_SCRATCH.buf = buf
    scratch = buf[: pcm.shape[0]]
    np.multiply(pcm, volume, out=scratch, casting="unsafe")
    np.clip(scratch, -32768, 32767, out=scratch)
    return scratch.astype(np.int16, copy=False)
