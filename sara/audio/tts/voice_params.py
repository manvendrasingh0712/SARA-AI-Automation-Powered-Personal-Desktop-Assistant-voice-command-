"""
sara.audio.tts.voice_params
Per-language voice parameter tuning (speed/pitch presets, fast-mode variant).
"""
from __future__ import annotations



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


def _detect_lang(text: str) -> str:
    """Return 'hi' if text contains Devanagari script, else 'en'."""
    return "hi" if _DEVANAGARI_RE.search(text) else "en"


# ══════════════════════════════════════════════════════════════════════════════
#  VOICE / SYNTHESIS PARAMS — per language (Kokoro voice routing)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _VoiceParams:
    voice: str
    lang_code: str
    speed: float


@lru_cache(maxsize=4)
def _build_params(lang: str) -> _VoiceParams:
    """Cached per-language voice routing — avoids repeated Config lookups per sentence."""
    if lang == "hi":
        return _VoiceParams(
            voice=getattr(Config, "KOKORO_VOICE_HI", "hf_alpha"),
            lang_code=getattr(Config, "KOKORO_LANG_HI", "hi"),
            # v12: default bumped 1.0 -> 1.15 for a more fluent/less halting
            # Hindi cadence. Config.KOKORO_SPEED_HI (if set) still overrides this.
            speed=float(getattr(Config, "KOKORO_SPEED_HI", 1.15)),
        )
    return _VoiceParams(
        voice=getattr(Config, "KOKORO_VOICE_EN", "af_heart"),
        lang_code=getattr(Config, "KOKORO_LANG_EN", "en-us"),
        speed=float(getattr(Config, "KOKORO_SPEED_EN", 1.0)),
    )


def _fast_variant(params: _VoiceParams) -> _VoiceParams:
    return _VoiceParams(
        voice=params.voice,
        lang_code=params.lang_code,
        speed=min(1.4, params.speed + 0.2),
    )
