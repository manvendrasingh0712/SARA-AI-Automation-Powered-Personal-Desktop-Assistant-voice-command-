"""Part 3 / P18: a mic stream that says 'active' but delivers no callbacks is treated as dead (stalled device)."""
import threading
import time

from sara.audio.stt import engine as E


class _Stream:
    def __init__(self, active=True):
        self.active = active


def _stt(stream):
    s = E.SpeechToText.__new__(E.SpeechToText)
    s._stream_lock = threading.RLock()
    s._stream = stream
    return s


def test_active_stream_with_fresh_callbacks_is_healthy(monkeypatch):
    monkeypatch.setattr(E, "_HAS_SD", True)
    s = _stt(_Stream())
    s._last_cb = time.monotonic()
    assert s._stream_is_healthy() is True


def test_active_stream_without_callbacks_is_a_stall(monkeypatch):
    monkeypatch.setattr(E, "_HAS_SD", True)
    s = _stt(_Stream())
    s._last_cb = time.monotonic() - (E.SpeechToText._STALL_S + 1)
    assert s._stream_is_healthy() is False


def test_no_callback_yet_or_detector_off_is_not_a_stall(monkeypatch):
    monkeypatch.setattr(E, "_HAS_SD", True)
    s = _stt(_Stream())
    assert s._last_cb == 0.0 and s._stream_is_healthy() is True      # just opened, nothing arrived yet
    s._last_cb = time.monotonic() - 999
    s._STALL_S = 0
    assert s._stream_is_healthy() is True                              # detector switched off


def test_inactive_or_missing_stream_is_still_unhealthy(monkeypatch):
    monkeypatch.setattr(E, "_HAS_SD", True)
    assert _stt(_Stream(active=False))._stream_is_healthy() is False
    assert _stt(None)._stream_is_healthy() is False


def test_callback_sets_the_heartbeat():
    s = E.SpeechToText.__new__(E.SpeechToText)
    s._closed = True                       # a closed engine ignores audio and must not touch the heartbeat
    s._audio_callback(b"", 0, None, None)
    assert s._last_cb == 0.0
