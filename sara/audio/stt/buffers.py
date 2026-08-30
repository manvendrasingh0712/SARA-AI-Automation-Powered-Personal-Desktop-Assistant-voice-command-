"""
sara.audio.stt.buffers
Audio buffering/VAD/noise-floor state machines used while collecting speech.
"""
from __future__ import annotations


import os
import collections
import threading
from enum import Enum, auto
from typing import Deque, List, Optional

from config import Config


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
        # Change 4: buf mutation + event signaling now happen under the
        # SAME lock as get_all()'s buf mutation + event clear below, so
        # the two can never interleave. Previously put()'s event.set()
        # happened outside the lock, so it could race with a concurrent
        # get_all() that had already read+cleared the buffer but not yet
        # cleared the event -- get_all() would then wipe the event that
        # put() had just set for genuinely new data, delaying (not
        # losing) that data until the next poll.
        with self._lock:
            self._buf.append(chunk)
            self._event.set()

    def get_all(self, clear: bool = True) -> List[bytes]:
        with self._lock:
            chunks = list(self._buf)
            if clear:
                self._buf.clear()
            # Only clear the event if the buffer is genuinely empty after
            # this read -- if a put() landed between our read and here it
            # already re-set the event under this same lock, and we must
            # not wipe that signal.
            if not self._buf:
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

    # BUGFIX (was hardcoded): aggressiveness now defaults to
    # Config.VAD_AGGRESSIVENESS (0-3, WebRTC VAD's own scale) instead of
    # a fixed 2, so it's tunable via .env without a code edit. The
    # explicit `aggressiveness` param is kept (not removed) so a caller
    # can still override it directly if ever needed; the class-level
    # clamp below is a defensive floor/ceiling independent of
    # config.py's own validate() clamp, since this class has no
    # guarantee it's only ever constructed after Config.validate() runs.
    def __init__(
        self,
        sample_rate: int = 16000,
        aggressiveness: "int | None" = None,
    ):
        if aggressiveness is None:
            aggressiveness = int(getattr(Config, "VAD_AGGRESSIVENESS", 2))
        aggressiveness = max(0, min(3, aggressiveness))
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
    # v-latency-audit: floor tightened 0.5s -> 0.45s ONLY. This branch is
    # evidence-gated — it only engages once _history shows avg speech
    # duration < 1.5s, meaning the gate has ALREADY observed several fast,
    # short utterances from this session. So the extra 50ms shave applies
    # only where a fast-speech pattern is proven, not blindly on turn 1.
    # CEIL and DEFAULT are deliberately left untouched:
    #   - DEFAULT covers the very first commands after boot with zero
    #     history — no evidence yet that tightening is safe.
    #   - CEIL protects naturally longer pauses (thinking, Hinglish
    #     code-switching mid-sentence) — tightening this risks cutting
    #     real speech mid-utterance, which violates the accuracy goal.
    _FLOOR = 0.45
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