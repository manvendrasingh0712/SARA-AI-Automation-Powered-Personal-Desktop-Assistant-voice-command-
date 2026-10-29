"""
Regression tests for D53b: the last turn of a session must reach disk.

WHAT WAS ACTUALLY WRONG (and what was NOT)
    The first draft of this finding claimed turns were only ever written
    when the NEXT turn began, so a 15-command session recorded almost
    nothing. That was WRONG, and measuring it properly disproved it:
    _sweep() runs from the store's idle hook every _FLUSH_S (2s) and
    finalises turns that ended more than _FINALISE_AFTER_END_S (20s) ago,
    so three turns followed by a 24s wait produced three rows on disk.

    The real loss is narrower and still worth fixing: a turn that ends
    within 20s of the process exiting was lost COMPLETELY, because the
    store's writer is a daemon thread killed at interpreter exit with its
    queue still full. Measured with a subprocess that ends a turn and exits
    immediately: no database file was created at all.

    That is always the LAST turn of a session -- frequently the one being
    investigated, and the one a user reaches for after saying "that reply
    was slow".

THE FIX
    atexit handlers run while the interpreter is still alive and before
    daemon threads are torn down. _flush_at_exit() sweeps with the age bar
    at 0.0 and then drains the queue ON THE EXITING THREAD.

WHY ttfa_ms IS NONE IN THOSE ROWS, AND WHY THAT IS CORRECT
    The 20s wait exists so a turn can still pick up ttfa_ms when TTS
    finishes after end_turn. At shutdown there is no such chance left, so
    the row is written without it. A row with an honest null beats no row.
"""
from __future__ import annotations

import inspect

from sara.core.telemetry import store as st
from sara.core.telemetry import trace as tr


def test_an_atexit_flush_is_registered() -> None:
    """
    Without this, the last turn of every session is silently dropped and
    nothing in the logs says so (D53b).
    """
    src = inspect.getsource(tr)
    assert "atexit.register(_flush_at_exit)" in src, (
        "the telemetry exit flush is no longer registered, so the last turn "
        "of every session is lost again (D53b)"
    )
    assert callable(getattr(tr, "_flush_at_exit", None))


def test_exit_flush_ignores_the_age_bar() -> None:
    """
    The whole point: at shutdown there is no more time to wait for
    tts_done, so the 20s bar must be bypassed or the flush finds nothing
    to write.
    """
    src = inspect.getsource(tr._flush_at_exit)
    assert "min_age_s=0.0" in src, (
        "the exit flush still applies the 20s finalise bar, so a turn that "
        "just ended is skipped and lost anyway (D53b)"
    )


def test_exit_flush_drains_on_the_calling_thread() -> None:
    """
    submit() alone is not enough: it hands the record to the daemon writer,
    which is exactly the thread about to be killed. The drain has to happen
    synchronously.
    """
    src = inspect.getsource(tr._flush_at_exit)
    assert "drain_now" in src, (
        "the exit flush only submits to the daemon writer, which dies "
        "before it can write (D53b)"
    )


def test_exit_flush_cannot_raise() -> None:
    """A failure to log must never become a failure to exit."""
    src = inspect.getsource(tr._flush_at_exit)
    assert "except Exception" in src
    tr._flush_at_exit()  # must be safe to call directly, repeatedly
    tr._flush_at_exit()


def test_exit_flush_respects_the_enabled_switch() -> None:
    src = inspect.getsource(tr._flush_at_exit)
    assert "_enabled" in src, (
        "the exit flush writes even when tracing is switched off"
    )


def test_in_flight_turns_are_not_invented() -> None:
    """
    A turn with no end_turn has no outcome. Writing a row for it would be a
    guess, so _sweep must keep requiring ended_at.
    """
    src = inspect.getsource(tr._sweep)
    assert "ended_at is not None" in src, (
        "_sweep no longer requires a turn to have ended, so turns still in "
        "flight get rows with invented outcomes"
    )


def test_drain_now_is_bounded_and_silent() -> None:
    """
    This runs on the shutdown path, so it must not hang on a busy queue nor
    propagate a write error.
    """
    src = inspect.getsource(st.TraceStore.drain_now)
    assert "timeout_s" in src and "deadline" in src, (
        "drain_now is no longer time-bounded; a shutdown could hang on it"
    )
    assert "except Exception" in src, "drain_now can raise during shutdown"


def test_drain_now_skips_the_stop_sentinel() -> None:
    """STOP is a control object, not a record; inserting it would throw."""
    src = inspect.getsource(st.TraceStore.drain_now)
    assert "is STOP" in src


def test_drain_now_returns_zero_when_persistence_is_off() -> None:
    store = st.TraceStore()
    store.ok = False
    assert store.drain_now() == 0


def test_drain_now_on_an_empty_queue_is_a_no_op() -> None:
    store = st.TraceStore()
    assert store.drain_now(timeout_s=0.05) == 0


def test_sweep_still_defaults_to_the_twenty_second_bar() -> None:
    """
    Normal operation must keep the wait, or every turn loses its chance to
    record ttfa_ms.
    """
    sig = inspect.signature(tr._sweep)
    assert sig.parameters["min_age_s"].default is None, (
        "_sweep's default changed; callers that omit min_age_s must still "
        "get _FINALISE_AFTER_END_S"
    )
    src = inspect.getsource(tr._sweep)
    assert "_FINALISE_AFTER_END_S if min_age_s is None" in src
