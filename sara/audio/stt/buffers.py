"""
sara.audio.stt.buffers
Audio buffering/VAD/noise-floor state machines used while collecting speech.
"""
from __future__ import annotations


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
# Buffers & Endpointing
# ══════════════════════════════════════════════════════════════════════


class _PreBuffer:
    def __init__(self, sample_rate: int, chunk_size: int, pre_ms: int = 300):
        chunks_needed = max(1, int((sample_rate / chunk_size) * (pre_ms / 1000)))
        self._buf: Deque[bytes] = collections.deque(maxlen=chunks_needed)

    def push(self, chunk: bytes) -> None:
        self._buf.append(chunk)

    def drain(self) -> bytes:
        return b"".join(self._buf)

    def clear(self) -> None:
        self._buf.clear()


class _RingBuffer:
    def __init__(self, maxlen: int = 300):
        self._buf: Deque[bytes] = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._event = threading.Event()

    def put(self, chunk: bytes) -> None:
        with self._lock:
            self._buf.append(chunk)
        self._event.set()

    def get_all(self, clear: bool = True) -> List[bytes]:
        with self._lock:
            chunks = list(self._buf)
            if clear:
                self._buf.clear()
        self._event.clear()
        return chunks

    def peek_latest(self, n: int) -> List[bytes]:
        with self._lock:
            items = list(self._buf)
        return items[-n:] if len(items) >= n else items

    def wait(self, timeout: float = 0.1) -> bool:
        return self._event.wait(timeout=timeout)


class _VADFilter:
    FRAME_MS = 32

    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2):
        self._sr = sample_rate
        self._vad_frame_bytes = int(sample_rate * 30 / 1000) * 2
        self._vad = webrtcvad.Vad(aggressiveness) if _HAS_VAD else None

    def is_speech(self, chunk: bytes) -> bool:
        if self._vad is None:
            return False
        fb = self._vad_frame_bytes
        chunk = chunk + b"\x00" * (fb - len(chunk)) if len(chunk) < fb else chunk[:fb]
        try:
            return self._vad.is_speech(chunk, self._sr)
        except Exception:
            return False


class _SilenceGate:
    _FLOOR = 0.5
    _CEIL = 1.2
    _DEFAULT = 0.8

    def __init__(self):
        self._history: Deque[float] = collections.deque(maxlen=10)

    def record(self, duration_s: float) -> None:
        self._history.append(duration_s)

    @property
    def silence_limit(self) -> float:
        if not self._history:
            return self._DEFAULT
        avg = sum(self._history) / len(self._history)
        if avg < 1.5:
            return self._FLOOR
        if avg > 5.0:
            return self._CEIL
        return self._DEFAULT


class _NoiseFloor:
    def __init__(self, window: int = 50):
        self._samples: Deque[float] = collections.deque(maxlen=window)

    def update(self, energy: float, threshold: float) -> None:
        if energy < threshold * 0.4:
            self._samples.append(energy)

    @property
    def floor(self) -> Optional[float]:
        if len(self._samples) < 5:
            return None
        arr = sorted(self._samples)
        return arr[len(arr) // 2]

    def suggested_threshold(self, margin: float = 250.0) -> Optional[float]:
        f = self.floor
        return (f + margin) if f is not None else None


class _CollectState(Enum):
    WAITING = auto()
    SPEAKING = auto()
    DONE = auto()
