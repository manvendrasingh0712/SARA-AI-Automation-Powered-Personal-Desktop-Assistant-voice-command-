"""
Regression tests for D46: the utterance length handed to the endpoint must
measure AUDIO, not list entries.

THE DEFECT
    _collect_speech passed `len(speech_chunks) - trailing_silence_chunks`
    as the utterance length. But speech_chunks[0] is the drained _pre_buf
    BLOB -- PRE_SPEECH_MS (300ms, ~9 chunks at 512 samples / 16kHz) joined
    into ONE bytes object by _PreBuffer.drain(). So every utterance was
    under-reported by about 8 chunks (~256ms).

    Visible in the target machine's own trace:

        [STT] endpoint rule=base silence_ms=640 utt_ms=896
        listen completed | mode=command | audio=1.15s

    1.15s of audio is 36 chunk-equivalents; 896ms is 28. The difference is
    exactly the 9-chunk pre-buffer collapsing into a single entry.

WHY IT IS A LATENCY BUG AND NOT JUST A WRONG NUMBER
    endpoint.required_silence_ms() refuses to end a turn below `base` while
    utterance_ms < _SHORT_UTTERANCE_MS (500ms) -- a guard meant to stop a
    stray blip being treated as a finished command. Under-reporting by
    256ms pushed genuine 500-760ms commands under that bar, so they waited
    `base` (625ms) instead of ENDPOINT_MIN_MS (350ms).

    Measuring bytes restores the boundary the guard was designed around
    rather than moving it, so this makes the guard MORE accurate, not
    weaker.

WHY THE EXISTING SUITE MISSED IT
    tests/test_stt_endpoint_integration.py's _FakePre.drain() returns None,
    so there is no blob and entry-count and byte-count agree. These tests
    use a pre-buffer that behaves like the real one.
"""
from __future__ import annotations

import inspect
import threading
import time

import pytest

from config import Config
from sara.audio.stt import endpoint as ep

CHUNK = 512          # the real SpeechToText.CHUNK_SIZE
WIDTH = 2
CHUNK_MS = 1000.0 * CHUNK / 16000.0        # 32ms
PRE_CHUNKS = 9                              # 300ms / 32ms, as _PreBuffer sizes it
# `energy_window` in _collect_speech is a 3-chunk rolling mean, so when
# speech stops the average needs 2 more chunks to fall below the gate.
# Those 2 count as voice, so the voiced run legitimately runs 2 chunks past
# the speech itself. Same property as WINDOW_LAG in
# tests/test_stt_endpoint_integration.py.
WINDOW_LAG = 2
VOICE = b"\xc4\x09" * CHUNK                 # RMS 2500
QUIET = b"\x00\x00" * CHUNK
N_SPEECH = 20


class _RealisticPre:
    """_PreBuffer.drain() joins its deque into ONE bytes object."""

    def drain(self) -> bytes:
        return QUIET * PRE_CHUNKS

    def clear(self) -> None:
        pass


class _FakeVAD:
    def is_speech(self, chunk):
        return chunk[:2] != b"\x00\x00"


class _FakeGate:
    silence_limit = 0.8

    def record(self, duration):
        pass


class _FakeRing:
    def __init__(self, owner, chunks, tail=QUIET, extra=300):
        self.owner, self.queue, self.tail, self.extra = owner, list(chunks), tail, extra

    def get_all(self, clear=False):
        if self.queue:
            return [self.queue.pop(0)]
        if self.extra > 0:
            self.extra -= 1
            return [self.tail]
        self.owner._closed = True
        return []

    def wait(self, timeout=None):
        return True


def _make_stt():
    from sara.audio.stt.engine import SpeechToText

    class _Eng(SpeechToText):
        SAMPLE_RATE = 16000
        CHUNK_SIZE = CHUNK
        SAMPLE_WIDTH = WIDTH
        PREVIEW_WINDOW_S = 3.0
        energy_threshold = 600.0

    stt = _Eng.__new__(_Eng)
    stt._closed = False
    stt._is_listening = threading.Event()
    stt._preview_busy = threading.Event()
    stt._preview_generation = 0
    stt._tts_state_lock = threading.Lock()
    stt._barge_in_handoff = []
    stt._vad, stt._pre_buf, stt._silence_gate = _FakeVAD(), _RealisticPre(), _FakeGate()
    stt._ring = _FakeRing(stt, [VOICE] * N_SPEECH)
    stt._spawn_preview_transcribe = lambda snapshot, cb, gen: cb("open chrome")
    return stt


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    for key, val in {
        "VOICE_ENDPOINT_ADAPTIVE": True, "ENDPOINT_MIN_MS": 350,
        "ENDPOINT_MAX_MS": 900, "ENDPOINT_CONTINUATION_EXTRA_MS": 400,
        "ENDPOINT_NOISE_EXTRA_MS": 150, "ENDPOINT_PREVIEW_AT_MS": 150,
        "ENDPOINT_EARLY_CUT_WINDOW_MS": 700, "ENDPOINT_EARLY_CUT_BACKOFF_MS": 100,
        "DEBUG_MODE": True, "SARA_LANGUAGE": "en",
    }.items():
        monkeypatch.setattr(Config, key, val, raising=False)
    monkeypatch.setattr(ep, "is_complete_command", lambda t: t.strip().lower() == "open chrome")


# ── the structural guard ────────────────────────────────────────────────
def test_utterance_length_is_measured_in_bytes_not_entries() -> None:
    src = inspect.getsource(
        __import__("sara.audio.stt.engine", fromlist=["x"]).SpeechToText._collect_speech
    )
    code = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    call = code[code.index("ended = self._endpoint_reached("):]
    call = call[: call.index(")")]
    assert "len(speech_chunks) - trailing_silence_chunks" not in call, (
        "the utterance length is a count of list entries again, so the "
        "pre-buffer blob counts as one chunk and every utterance is "
        "under-reported by ~256ms (D46)"
    )
    assert "_voiced_bytes" in code and "_bytes_per_chunk" in code, (
        "the utterance length is no longer derived from audio bytes (D46)"
    )


def test_bytes_per_chunk_comes_from_the_class_not_a_literal() -> None:
    """
    Hardcoding 1024 here would silently break if CHUNK_SIZE or
    SAMPLE_WIDTH ever changed.
    """
    src = inspect.getsource(
        __import__("sara.audio.stt.engine", fromlist=["x"]).SpeechToText._collect_speech
    )
    assert "self.CHUNK_SIZE * self.SAMPLE_WIDTH" in src


# ── the behavioural proof ───────────────────────────────────────────────
def test_the_prebuffer_blob_is_counted_as_its_real_length(capsys) -> None:
    """
    With a 9-chunk pre-buffer and N_SPEECH voiced chunks, the reported
    utterance must reflect all of them. Counting entries would report
    1 + N_SPEECH chunks instead of PRE_CHUNKS + N_SPEECH.
    """
    stt = _make_stt()
    stt._collect_speech(
        timeout=1.0, max_duration=12.0, silence_limit=0.8,
        on_partial_transcript=lambda t: None,
    )
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if "endpoint rule=" in l]
    assert line, f"no endpoint debug line was printed; got:\n{out}"
    utt_ms = int(line[-1].split("utt_ms=")[1].split()[0])

    entries_would_report = round((1 + N_SPEECH) * CHUNK_MS)
    assert utt_ms > entries_would_report, (
        f"utt_ms={utt_ms} matches an ENTRY count ({entries_would_report}), "
        f"so the pre-buffer blob is still collapsing to one chunk (D46)"
    )
    # The voiced run is the blob, plus the speech chunks, plus WINDOW_LAG
    # chunks of trailing quiet that the rolling mean still reads as voice.
    full = (PRE_CHUNKS + N_SPEECH + WINDOW_LAG) * CHUNK_MS
    assert utt_ms == round(full), (
        f"utt_ms={utt_ms} but the voiced run is {full:.0f}ms "
        f"({PRE_CHUNKS} pre-buffer + {N_SPEECH} speech + {WINDOW_LAG} lag "
        f"chunks x {CHUNK_MS:.0f}ms)"
    )


def test_a_short_command_now_clears_the_short_utterance_guard() -> None:
    """
    The latency payoff, checked against endpoint.py directly rather than
    through the capture loop.

    A ~750ms real utterance was reported as ~494ms by the entry count,
    which is under _SHORT_UTTERANCE_MS (500) -- so required_silence_ms()
    forced `base` and the 350ms "complete" rule could never apply.
    """
    true_ms = (PRE_CHUNKS + 14) * CHUNK_MS            # ~736ms of real audio
    entry_ms = (1 + 14) * CHUNK_MS                    # ~480ms as entries

    need_true, rule_true = ep.required_silence_ms(
        "open chrome", 0.0, "en", utterance_ms=true_ms, base_ms=625.0
    )
    need_entry, rule_entry = ep.required_silence_ms(
        "open chrome", 0.0, "en", utterance_ms=entry_ms, base_ms=625.0
    )

    assert entry_ms < 500 <= true_ms, (
        "this test's premise is stale: the two measurements must straddle "
        "_SHORT_UTTERANCE_MS for the guard to be the thing under test"
    )
    assert rule_entry == "base", (
        "the under-reported length was expected to trip the short-utterance "
        "guard; if it no longer does, this test no longer shows the cost"
    )
    assert rule_true == "complete", (
        "with the true utterance length a recognised command must reach the "
        "complete rule (D46 + D51)"
    )
    assert need_true < need_entry, (
        f"the fix did not reduce the required silence "
        f"({need_true:.0f}ms vs {need_entry:.0f}ms)"
    )


def test_genuine_blips_are_still_protected() -> None:
    """
    The guard must keep doing its job: a real sub-500ms utterance must
    still refuse to endpoint below base, even measured correctly.
    """
    blip_ms = 8 * CHUNK_MS  # 256ms of audio, genuinely short
    _, rule = ep.required_silence_ms(
        "open chrome", 0.0, "en", utterance_ms=blip_ms, base_ms=625.0
    )
    assert rule == "base", (
        "a 256ms utterance now endpoints on the fast path; D46 was about "
        "measuring the length correctly, not removing the guard"
    )
