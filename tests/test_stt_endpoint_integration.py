"""Adaptive endpointing inside SpeechToText._collect_speech (synthetic chunks, no hardware)."""
import threading
import time

import pytest

from config import Config
from sara.audio.stt import endpoint as ep

CHUNK = 480
VOICE = b"\x01\x00" * CHUNK
QUIET = b"\x00\x00" * CHUNK
N_SPEECH = 40
LEGACY_SILENCE = 26  # int(0.8 s * 16000 / 480)


class _FakeVAD:
    def is_speech(self, chunk):
        return chunk[:1] == b"\x01"


class _FakePre:
    def drain(self):
        return None

    def clear(self):
        pass


class _FakeGate:
    def record(self, duration):
        pass


class _FakeRing:
    """Hands out one chunk per get_all(); closes the engine when exhausted."""

    def __init__(self, owner, chunks, tail=QUIET, extra=300, sleep=0.0):
        self.owner, self.queue, self.tail = owner, list(chunks), tail
        self.extra, self.sleep, self.consumed = extra, sleep, 0

    def get_all(self, clear=False):
        if self.queue:
            chunk = self.queue.pop(0)
        elif self.extra > 0:
            self.extra -= 1
            chunk = self.tail
        else:
            self.owner._closed = True
            return []
        self.consumed += 1
        return [chunk]

    def wait(self, timeout=None):
        time.sleep(self.sleep)


def _make_stt(chunks=None, **ring_kw):
    from sara.audio.stt.engine import SpeechToText

    class _Eng(SpeechToText):
        SAMPLE_RATE = 16000
        CHUNK_SIZE = CHUNK
        SAMPLE_WIDTH = 2
        PREVIEW_WINDOW_S = 3.0
        energy_threshold = 1e9

    stt = _Eng.__new__(_Eng)
    stt._closed = False
    stt._is_listening = threading.Event()
    stt._preview_busy = threading.Event()
    stt._preview_generation = 0
    stt._tts_state_lock = threading.Lock()
    stt._barge_in_handoff = []
    stt._vad, stt._pre_buf, stt._silence_gate = _FakeVAD(), _FakePre(), _FakeGate()
    stt._ring = _FakeRing(stt, [VOICE] * N_SPEECH if chunks is None else chunks, **ring_kw)
    return stt


def _previews(stt, text):
    calls = []

    def spawn(snapshot, cb, generation):
        calls.append(len(snapshot))
        if text is not None:
            cb(text)

    stt._spawn_preview_transcribe = spawn
    return calls


def _run(stt, cb=None, max_duration=12.0):
    return stt._collect_speech(
        timeout=1.0, max_duration=max_duration, silence_limit=0.8, on_partial_transcript=cb
    )


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    for key, val in {
        "VOICE_ENDPOINT_ADAPTIVE": True, "ENDPOINT_MIN_MS": 350, "ENDPOINT_MAX_MS": 900,
        "ENDPOINT_CONTINUATION_EXTRA_MS": 400, "ENDPOINT_NOISE_EXTRA_MS": 150,
        "ENDPOINT_PREVIEW_AT_MS": 150, "ENDPOINT_EARLY_CUT_WINDOW_MS": 700,
        "ENDPOINT_EARLY_CUT_BACKOFF_MS": 100, "DEBUG_MODE": False, "SARA_LANGUAGE": "en",
    }.items():
        monkeypatch.setattr(Config, key, val, raising=False)
    monkeypatch.setattr(ep, "is_complete_command", lambda t: t.strip().lower() == "open chrome")


def test_flag_off_is_legacy(monkeypatch):
    monkeypatch.setattr(Config, "VOICE_ENDPOINT_ADAPTIVE", False)
    stt = _make_stt()
    calls = _previews(stt, "open chrome")
    audio = _run(stt)
    assert stt._ring.consumed == N_SPEECH + LEGACY_SILENCE
    assert len(audio) == N_SPEECH * len(VOICE)
    assert calls == [] and not hasattr(stt, "_endpoint_guard")
    assert stt.endpoint_stats() == {"endpoints": 0, "early_cuts": 0, "rules": {}}


def test_complete_command_ends_sooner():
    stt = _make_stt()
    calls = _previews(stt, "open chrome")
    audio = _run(stt)
    assert stt._ring.consumed == N_SPEECH + 12
    assert len(audio) == N_SPEECH * len(VOICE)
    assert len(calls) == 1
    assert stt.endpoint_stats() == {"endpoints": 1, "early_cuts": 0, "rules": {"complete": 1}}


def test_continuation_ends_later():
    stt = _make_stt()
    _previews(stt, "open chrome and")
    _run(stt)
    assert stt._ring.consumed == N_SPEECH + 30
    assert stt.endpoint_stats()["rules"] == {"continuation": 1}


def test_no_partial_uses_base():
    stt = _make_stt()
    calls = _previews(stt, None)
    _run(stt)
    assert stt._ring.consumed == N_SPEECH + 27
    assert len(calls) == 1
    assert stt.endpoint_stats()["rules"] == {"base": 1}


def test_extra_preview_once_and_user_callback_still_called():
    stt = _make_stt()
    calls = _previews(stt, "open chrome")
    seen = []
    _run(stt, cb=seen.append)
    assert len(calls) == 1 and seen == ["open chrome"]


def test_busy_preview_is_retried_not_stacked():
    stt = _make_stt()
    calls = _previews(stt, "open chrome")
    stt._preview_busy.set()
    _run(stt)
    assert calls == []
    assert stt._ring.consumed == N_SPEECH + 27


def test_stale_partial_is_ignored():
    stt = _make_stt(chunks=[VOICE] * N_SPEECH + [QUIET] * 6 + [VOICE] * 5)
    calls = _previews(stt, "open chrome")
    _run(stt)
    assert len(calls) == 2  # one per silence run
    assert stt._ring.consumed > N_SPEECH + 6 + 5 + 11


def test_decide_exception_falls_back_to_legacy(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("secret text")

    monkeypatch.setattr(ep, "decide_endpoint", boom)
    stt = _make_stt()
    _previews(stt, "open chrome")
    _run(stt)
    out = capsys.readouterr().out
    assert stt._ring.consumed == N_SPEECH + LEGACY_SILENCE
    assert out.count("adaptive endpoint failed") == 1 and "secret" not in out
    assert stt.endpoint_stats()["endpoints"] == 0


def test_max_duration_still_enforced():
    stt = _make_stt(chunks=[], tail=VOICE, extra=10_000, sleep=0.002)
    _previews(stt, "open chrome")
    audio = _run(stt, max_duration=0.05)
    assert audio and stt._ring.consumed < 10_000


def test_debug_line_has_no_text(monkeypatch, capsys):
    monkeypatch.setattr(Config, "DEBUG_MODE", True)
    stt = _make_stt()
    _previews(stt, "open chrome")
    _run(stt)
    out = capsys.readouterr().out
    assert "[STT] endpoint rule=complete silence_ms=360 utt_ms=1200" in out
    assert "chrome" not in out


def test_early_cut_detected_on_quick_resume():
    stt = _make_stt()
    _previews(stt, "open chrome and")
    _run(stt)
    stt._ring = _FakeRing(stt, [VOICE] * N_SPEECH)
    stt._closed = False
    _run(stt)
    assert stt.endpoint_stats()["early_cuts"] == 1


@pytest.mark.parametrize(
    "raw,want",
    [("hinglish", "hinglish"), ("Hinglish", "hinglish"), ("hi", "hi"), ("hi-IN", "hi"),
     ("hindi", "hi"), ("en", "en"), ("", "en"), (None, "en"), ("fr", "en")],
)
def test_language_mapping(monkeypatch, raw, want):
    from sara.audio.stt.engine import SpeechToText

    monkeypatch.setattr(Config, "SARA_LANGUAGE", raw)
    assert SpeechToText._endpoint_lang() == want