"""
Tests for D61: a worker loop must not die on one bad chunk.

THE DEFECT
    _aec_worker_loop's body was unguarded:

        chunk = self._aec_raw_q.get(timeout=...)
        processed = self._apply_aec(chunk)
        self._ingest_processed_chunk(processed)

    When AEC is enabled -- the default, and the configuration the target
    machine runs -- this thread is the ONLY route microphone audio takes
    from the real-time callback into the capture ring. It is a daemon
    thread and nothing supervises it: the mic-disconnect watchdog probes
    the STREAM, not this worker.

    So a single unexpected exception killed the thread and left SARA
    permanently deaf -- with the device still open, the callback still
    firing, the GUI level meter still moving, and NOTHING logged.

    That is the same shape as D34/D35 (deaf, no error), which cost about
    twelve days before it was found.

    _apply_aec() already swallows its own errors, but
    _ingest_processed_chunk() does not: it runs _passes_barge_in_gate()
    (numpy over raw bytes) and the ring/pre-buffer writes, any of which
    could raise on a malformed or short chunk.

THE RULE
    One bad chunk costs one chunk, not the microphone. Rate-limited,
    because if it recurs it recurs ~31 times a second.
"""
from __future__ import annotations

import inspect
import queue
import threading
import time

import sara.audio.stt.engine as E
from sara.audio.stt.engine import SpeechToText


# ── structural ──────────────────────────────────────────────────────────
def test_the_aec_worker_body_is_guarded() -> None:
    src = inspect.getsource(SpeechToText._aec_worker_loop)
    body = src.split("except queue.Empty:", 1)[1]
    assert "except Exception" in body, (
        "the AEC worker body is unguarded again. One unexpected exception "
        "kills the only path microphone audio takes to the capture ring, "
        "and SARA goes permanently deaf with nothing logged (D61)"
    )


def test_the_aec_worker_logs_rather_than_failing_silently() -> None:
    src = inspect.getsource(SpeechToText._aec_worker_loop)
    assert "_log_rate_limited" in src, (
        "the AEC worker swallows errors without logging, so degraded "
        "microphone audio is invisible (D61)"
    )


def test_the_aec_worker_does_not_break_out_of_the_loop() -> None:
    """
    Catching and then `break`ing would be the same bug with extra steps.
    """
    src = inspect.getsource(SpeechToText._aec_worker_loop)
    handler = src[src.rindex("except Exception"):]
    assert "break" not in handler and "return" not in handler, (
        "the AEC worker exits its loop after an error, so the microphone "
        "still stops (D61)"
    )


def test_the_recalibration_loop_is_guarded_too() -> None:
    src = inspect.getsource(SpeechToText._recalib_loop)
    assert src.count("except Exception") >= 2, (
        "the recalibration loop is unguarded; a dead thread freezes the "
        "energy threshold and the gate silently stops following the room "
        "(D61). Both the startup pass and the steady-state call need it."
    )


def test_the_startup_calibration_call_is_guarded() -> None:
    """
    That call was added in this same audit. Unguarded, it would kill the
    thread before the steady-state loop ever ran.
    """
    src = inspect.getsource(SpeechToText._recalib_loop)
    # Comments stripped before slicing: a later comment in this function
    # mentions _RECALIB_FIRST_INTERVAL, and slicing the raw source at that
    # name truncated the window before the code under test.
    code = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    window = code[: code.index("_RECALIB_FIRST_INTERVAL")]
    assert "first=True" in window and "except Exception" in window, (
        "the fast startup calibration is unguarded (D61)"
    )


# ── behavioural: a raising chunk must not stop the worker ───────────────
def _worker(fail_on, calls):
    """A minimal instance whose ingest raises for the chunks in `fail_on`."""
    stt = SpeechToText.__new__(SpeechToText)
    stt._closed = False
    stt._aec_raw_q = queue.Queue()
    stt._aec = object()

    def _apply(c):
        return c

    def _ingest(c):
        calls.append(c)
        if c in fail_on:
            raise RuntimeError(f"synthetic failure on {c!r}")

    stt._apply_aec = _apply
    stt._ingest_processed_chunk = _ingest
    return stt


def test_a_raising_chunk_costs_one_chunk_not_the_microphone() -> None:
    calls: list[bytes] = []
    stt = _worker({b"bad"}, calls)
    for c in (b"a", b"bad", b"b", b"c"):
        stt._aec_raw_q.put(c)

    t = threading.Thread(target=stt._aec_worker_loop, daemon=True)
    t.start()
    deadline = time.monotonic() + 3.0
    while len(calls) < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    stt._closed = True
    t.join(timeout=2.0)

    assert calls == [b"a", b"bad", b"b", b"c"], (
        f"the worker stopped after the failing chunk; it processed {calls!r}. "
        f"Every chunk after the error would be lost and SARA would be deaf "
        f"(D61)"
    )
    assert not t.is_alive(), "the worker thread did not shut down cleanly"


def test_repeated_failures_do_not_spin_the_log() -> None:
    """
    If it recurs it recurs ~31 times a second, so the report must be
    time-limited, not per-occurrence.
    """
    E._rate_limited_last_logged.pop("_aec_worker_loop", None)
    logged: list[str] = []
    real = E.logger.warning
    E.logger.warning = lambda msg, *a, **k: logged.append(msg)
    try:
        calls: list[bytes] = []
        stt = _worker({b"bad"}, calls)
        for _ in range(40):
            stt._aec_raw_q.put(b"bad")
        t = threading.Thread(target=stt._aec_worker_loop, daemon=True)
        t.start()
        deadline = time.monotonic() + 3.0
        while len(calls) < 40 and time.monotonic() < deadline:
            time.sleep(0.01)
        stt._closed = True
        t.join(timeout=2.0)
    finally:
        E.logger.warning = real

    assert len(calls) == 40, f"only {len(calls)} of 40 chunks were processed"
    assert len(logged) <= 2, (
        f"40 consecutive failures produced {len(logged)} log lines; the "
        f"report is not rate-limited (D61)"
    )
    assert logged, "40 failures produced no log line at all"


def test_the_worker_still_exits_when_closed() -> None:
    """The guard must not make shutdown hang."""
    calls: list[bytes] = []
    stt = _worker(set(), calls)
    t = threading.Thread(target=stt._aec_worker_loop, daemon=True)
    t.start()
    time.sleep(0.05)
    stt._closed = True
    t.join(timeout=3.0)
    assert not t.is_alive(), "the AEC worker did not exit on close"


def test_the_level_worker_was_already_guarded() -> None:
    """
    Recorded so it is not 'fixed' twice: _level_worker's body is already
    inside a try/except, and its failure is cosmetic (a level meter), not
    deafness.
    """
    src = inspect.getsource(SpeechToText._level_worker)
    assert "except Exception" in src
