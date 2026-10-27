import os
import sys
import threading


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from sara.audio.stt import barge_decode
from sara.audio.stt.engine import SpeechToText


class _Ring:
    def __init__(self, chunks):
        self.chunks = chunks
        self.peeks = []
        self.drained = False

    def peek_latest(self, n=1):
        self.peeks.append(n)
        return self.chunks[-n:]

    def get_all(self, clear=False):
        self.drained = True
        return []


class _Boom:
    def __enter__(self):
        raise AssertionError("listen lock taken")

    def __exit__(self, *a):
        return False

    def acquire(self, *a, **k):
        raise AssertionError("listen lock taken")


def _stt(chunks=(b"ab", b"cd"), model=object(), transcribe=None):
    stt = SpeechToText.__new__(SpeechToText)
    stt._closed = False
    stt._ring = _Ring(list(chunks))
    stt._wake_whisper_model = model
    stt._listen_lock = _Boom()
    stt._transcribe = transcribe or (lambda snap, **kw: " hello ")
    return stt


def test_none_without_model():
    assert _stt(model=None).decode_barge_ring() is None


def test_decodes_processed_ring_without_listen_lock():
    seen = {}

    def fake(snap, beam_size_override=None, model_override=None):
        seen["snap"], seen["beam"] = snap, beam_size_override
        return " hello "

    stt = _stt(transcribe=fake)
    assert stt.decode_barge_ring(700) == "hello"
    assert seen == {"snap": b"abcd", "beam": 1}
    assert stt._ring.peeks and not stt._ring.drained


def test_window_size_scales_with_max_ms():
    stt = _stt()
    stt.decode_barge_ring(700)
    stt.decode_barge_ring(100)
    assert stt._ring.peeks[0] > stt._ring.peeks[1] >= 1


def test_empty_ring_and_blank_result_return_none():
    assert _stt(chunks=()).decode_barge_ring() is None
    assert _stt(transcribe=lambda s, **k: "  ").decode_barge_ring() is None


def test_transcribe_error_returns_none():
    def boom(s, **k):
        raise RuntimeError("x")

    assert _stt(transcribe=boom).decode_barge_ring() is None


def test_timeout_and_single_flight(monkeypatch):
    monkeypatch.setattr(barge_decode, "DECODE_TIMEOUT_S", 0.05)
    gate = threading.Event()
    stt = _stt(transcribe=lambda s, **k: gate.wait(5) and "late")
    try:
        assert stt.decode_barge_ring() is None
        assert stt.decode_barge_ring() is None
    finally:
        gate.set()
    for _ in range(100):
        if barge_decode._BUSY.acquire(blocking=False):
            barge_decode._BUSY.release()
            break
        threading.Event().wait(0.02)
    assert _stt().decode_barge_ring() == "hello"


def test_closed_returns_none():
    stt = _stt()
    stt._closed = True
    assert stt.decode_barge_ring() is None