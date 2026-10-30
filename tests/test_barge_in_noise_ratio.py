"""
Barge-in must not be triggered by the room itself.

E2E user test, 2026-10-29: 73 of 77 LLM voice replies ended as barge_in,
most before first audio. The log showed the room at RMS ~664 against an
effective barge-in gate of BARGE_IN_ENERGY_THRESHOLD (300) x bleed guard
(1.6) = 480, so steady background noise the VAD accepted as speech cut
SARA off. The fix: while SARA speaks, an interruption must also be
BARGE_IN_NOISE_RATIO x louder than the room level measured when TTS began.
The D63 property must survive: real speech must still be able to interrupt.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from config import Config
from sara.audio.stt.engine import SpeechToText

# (noise floor, loudest speech RMS) measured on the target machine.
_MEASURED = [(24, 2202), (25, 3131), (29, 1599), (33, 607),
             (125, 4996), (164, 2337), (203, 3082)]


def _chunk(rms: float) -> bytes:
    n = SpeechToText.CHUNK_SIZE
    return (np.full(n, rms, dtype=np.float64)).clip(-32768, 32767).astype(np.int16).tobytes()


class _Ring:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def peek_latest(self, n):
        return self._chunks[-n:]


def _ears(threshold: float = 300.0, ring=()) -> SpeechToText:
    ears = SpeechToText.__new__(SpeechToText)
    ears._energy_threshold = float(threshold)
    ears._threshold_lock = threading.Lock()
    ears._manual_threshold_until = 0.0
    ears._ring = _Ring(ring)
    return ears


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(Config, "BARGE_IN_ENERGY_THRESHOLD", 300, raising=False)
    monkeypatch.setattr(Config, "TTS_BLEED_GUARD_MULTIPLIER", 1.6, raising=False)
    monkeypatch.setattr(Config, "BARGE_IN_NOISE_RATIO", 3.0, raising=False)


def test_ambient_snapshot_ignores_the_users_words() -> None:
    # 3 s of room at ~660 with 1 s of the user's command at ~3000 in it.
    ring = [_chunk(660)] * 64 + [_chunk(3000)] * 30
    ambient = _ears(ring=ring)._recent_ambient_rms()
    assert 600 < ambient < 720


def test_ambient_snapshot_needs_enough_audio() -> None:
    assert _ears(ring=[_chunk(500)] * 3)._recent_ambient_rms() is None


def test_the_measured_loud_room_no_longer_clears_the_gate() -> None:
    ears = _ears()
    ears._ambient_rms = 664.0
    assert ears._barge_in_effective_threshold() > 664.0 * 2


def test_quiet_room_keeps_the_configured_gate() -> None:
    ears = _ears()
    ears._ambient_rms = 60.0
    assert ears._barge_in_effective_threshold() == pytest.approx(300 * 1.6)


def test_no_snapshot_means_no_change() -> None:
    ears = _ears()
    assert ears._barge_in_effective_threshold() == pytest.approx(300 * 1.6)


def test_ratio_zero_disables_the_room_rule(monkeypatch) -> None:
    monkeypatch.setattr(Config, "BARGE_IN_NOISE_RATIO", 0.0, raising=False)
    ears = _ears()
    ears._ambient_rms = 664.0
    assert ears._barge_in_effective_threshold() == pytest.approx(300 * 1.6)


@pytest.mark.parametrize("floor,speech", _MEASURED)
def test_real_speech_can_still_interrupt(floor, speech) -> None:
    """THE D63 PROPERTY: the room rule must never make interruption impossible."""
    ears = _ears()
    ears._ambient_rms = float(floor)
    assert ears._barge_in_effective_threshold() < speech


def test_set_tts_active_takes_the_snapshot() -> None:
    ears = _ears(ring=[_chunk(400)] * 40)
    ears._tts_active = threading.Event()

    class _Buf:
        def clear(self):
            pass

    ears._pre_buf = _Buf()
    ears.set_tts_active(True)
    assert ears._ambient_rms == pytest.approx(400, rel=0.05)
