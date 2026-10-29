"""
Tests for D60: the real-time capture callbacks must stay non-blocking.

The project's own brief is explicit about this: "Keep audio-device
callbacks minimal and non-blocking. Do not run model inference, disk I/O,
network requests, long-running locks, or expensive processing inside
real-time audio callbacks."

THE DEFECT
    Both callbacks reported stream status with a raw print():

        if status and Config.DEBUG_MODE:
            print(f"[STT] input stream status: {status}")

    `status` is set on overflow/underflow -- exactly when the audio device
    is already struggling. The callback runs ~31 times a second (512
    samples at 16kHz = 32ms), and a print() to a Windows console costs
    milliseconds against that 32ms budget. So a stream in trouble printed
    31 lines per second FROM THE AUDIO THREAD and made the trouble worse:
    the classic self-worsening diagnostic.

    The target machine runs with DEBUG_MODE=True, so this was live there.

THE FIX
    _log_rate_limited() already existed in this module for the AEC error
    paths (30s interval), so the status report reuses it rather than
    introducing a second mechanism. Still gated on DEBUG_MODE, so a normal
    run does nothing here at all.

WHAT IS DELIBERATELY NOT CHANGED
    When AEC is unavailable, _ingest_processed_chunk runs on this thread
    and _passes_barge_in_gate does an RMS and a webrtcvad call during TTS.
    Both are bounded C-level work on a 30ms frame, the code comments say
    so, and on a machine with the AEC wheel the worker thread handles it
    instead. Left alone.
"""
from __future__ import annotations

import inspect

import pytest

from sara.audio.stt.engine import SpeechToText

_CALLBACKS = ("_audio_callback", "_pa_callback")


def _body(name: str) -> str:
    src = inspect.getsource(getattr(SpeechToText, name))
    return "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )


@pytest.mark.parametrize("name", _CALLBACKS)
def test_no_unbounded_print_in_the_callback(name: str) -> None:
    body = _body(name)
    assert "print(" not in body, (
        f"{name} prints directly from the real-time audio thread. `status` "
        f"is set when the device is already struggling, so this runs ~31x/s "
        f"and makes it worse (D60). Use _log_rate_limited()."
    )


@pytest.mark.parametrize("name", _CALLBACKS)
def test_status_reporting_is_rate_limited(name: str) -> None:
    body = _body(name)
    assert "_log_rate_limited(" in body, (
        f"{name} no longer rate-limits its stream-status report (D60)"
    )


@pytest.mark.parametrize("name", _CALLBACKS)
def test_status_reporting_is_still_debug_gated(name: str) -> None:
    """A normal run must do no logging work here whatsoever."""
    body = _body(name)
    assert "DEBUG_MODE" in body, (
        f"{name} reports stream status unconditionally; a healthy run should "
        f"cost nothing in this callback"
    )


@pytest.mark.parametrize("name", _CALLBACKS)
def test_the_callback_never_blocks_on_a_queue(name: str) -> None:
    """
    put() with a timeout, or a plain blocking put(), would stall the audio
    device. Every hand-off out of these callbacks must be put_nowait with
    the Full case handled.
    """
    body = _body(name)
    assert "put_nowait" in body, f"{name} has no non-blocking hand-off"
    assert "queue.Full" in body, (
        f"{name} does not handle a full queue, so audio is either dropped "
        f"silently or the put raises inside the audio thread"
    )
    # Precise check: a BLOCKING put is `put(` NOT followed by `_nowait`.
    # An earlier version of this test used a bare "put(" substring and
    # false-matched put_nowait itself.
    import re

    # self._ring.put() / self._wake_ring.put() are the intended hand-off:
    # a deque append under a short lock in _RingBuffer, microseconds, and
    # the only way the capture path receives audio at all. Anything ELSE
    # blocking is a defect.
    allowed = {"_ring.put(", "_wake_ring.put("}
    for m in re.finditer(r"self\.(_\w+)\.put\(", body):
        assert f"{m.group(1)}.put(" in allowed, (
            f"{name} calls a blocking put on {m.group(1)}; only the ring "
            f"buffers' lock-protected append is acceptable here (D60)"
        )
    for bad in ("time.sleep", ".acquire(", "put(item", "timeout="):
        assert bad not in body, (
            f"{name} contains {bad!r}, which can block the real-time "
            f"audio thread (D60)"
        )


@pytest.mark.parametrize("name", _CALLBACKS)
def test_no_inference_or_io_in_the_callback(name: str) -> None:
    body = _body(name)
    for bad in ("transcribe(", "open(", "requests.", "httpx.", "logger.info"):
        assert bad not in body, (
            f"{name} performs {bad!r} inside the real-time audio callback"
        )


@pytest.mark.parametrize("name", _CALLBACKS)
def test_dropped_chunks_are_counted_not_silently_lost(name: str) -> None:
    """
    "Ensure audio is not silently discarded when queues become full" --
    a drop is acceptable under load, an unobservable drop is not.
    """
    body = _body(name)
    assert "_aec_drop_count" in body, (
        f"{name} drops mic chunks without counting them, so a stalled AEC "
        f"worker is invisible"
    )


def test_the_rate_limiter_has_a_real_interval() -> None:
    """A zero interval would make the limiter a no-op."""
    import sara.audio.stt.engine as E

    assert E._RATE_LIMITED_LOG_INTERVAL_S >= 1.0, (
        "the rate-limit interval is too small to bound logging from the "
        "audio thread"
    )


def test_the_two_callbacks_use_distinct_limiter_keys() -> None:
    """
    _log_rate_limited keys by name. Sharing one key between the
    sounddevice and pyaudio paths would let one backend's report suppress
    the other's.
    """
    sd, pa = _body("_audio_callback"), _body("_pa_callback")
    import re

    sd_keys = set(re.findall(r'_log_rate_limited\(\s*"([^"]+)"', sd))
    pa_keys = set(re.findall(r'_log_rate_limited\(\s*"([^"]+)"', pa))
    assert sd_keys and pa_keys, "a limiter key could not be found"
    assert not (sd_keys & pa_keys), (
        f"both callbacks share limiter key(s) {sd_keys & pa_keys}; one "
        f"backend's status report would suppress the other's"
    )
