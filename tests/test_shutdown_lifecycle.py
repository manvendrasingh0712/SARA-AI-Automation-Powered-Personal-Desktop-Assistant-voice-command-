"""
Regression tests for the logic thread's shutdown sequence (audit batch 6b(2)).

THE DEFECT
----------
run_sara_logic()'s `finally:` block tore down six components in sequence, but
only `reminders.shutdown()` was wrapped in try/except. The rest ran unguarded,
so ONE raising step silently skipped every step after it.

That ordering mattered: `db_writer.shutdown()` sat second-to-last, and it is
the step that flushes queued conversation-log writes
(AsyncDBWriter._flush_pending drains the SimpleQueue into the DB). The writer
thread is a daemon with a non-persistent queue, so anything still queued when
the process exits is gone.

The realistic trigger is `tts.shutdown()`, which runs immediately before it and
reaches TTSWorker._release_duck(), the chunker/synth thread pools and the
sounddevice output stream. TextToSpeech.__del__ wraps its own call to
shutdown() in try/except precisely because it can raise.

Net effect: an audio-teardown hiccup on exit silently dropped the user's last
messages from their conversation history, with no error anywhere.

sara/gui/app/bootstrap.py already does this correctly for the process-level
teardown -- "each wrapped so one failing cleanup step can never block the rest
of shutdown". These tests pin the same guarantee onto the logic thread's
teardown.
"""
from __future__ import annotations

import pytest

from sara.orchestrator.core_wiring import shutdown_logic_components


class _Recorder:
    """Records which teardown steps ran, in order."""

    def __init__(self):
        self.calls: list[str] = []

    def _make(self, name, raises=None):
        def fn():
            self.calls.append(name)
            if raises is not None:
                raise raises
        return fn


class _Component:
    def __init__(self, rec, name, method, raises=None):
        self._fn = rec._make(name, raises)
        setattr(self, method, self._fn)


def _components(rec, failing=None):
    """Build a full set of fake components; `failing` names one that raises."""
    def exc(name):
        return RuntimeError(f"{name} blew up") if failing == name else None

    return dict(
        thread_supervisor=_Component(rec, "thread_supervisor", "stop", exc("thread_supervisor")),
        reminders=_Component(rec, "reminders", "shutdown", exc("reminders")),
        proactive_engine=_Component(rec, "proactive_engine", "shutdown", exc("proactive_engine")),
        tts=_Component(rec, "tts", "shutdown", exc("tts")),
        notes_memory=_Component(rec, "notes_memory", "close", exc("notes_memory")),
        db_writer=_Component(rec, "db_writer", "shutdown", exc("db_writer")),
    )


def test_clean_shutdown_runs_every_step() -> None:
    rec = _Recorder()
    failures = shutdown_logic_components(**_components(rec))
    assert failures == []
    for step in ("thread_supervisor", "reminders", "proactive_engine",
                 "tts", "notes_memory", "db_writer"):
        assert step in rec.calls, f"{step} was never torn down"


@pytest.mark.parametrize(
    "failing",
    ["thread_supervisor", "reminders", "proactive_engine", "tts", "notes_memory"],
)
def test_a_failing_step_never_blocks_the_conversation_log_flush(failing: str) -> None:
    """
    THE regression guard. Whichever earlier step explodes,
    db_writer.shutdown() -- the conversation-log flush -- must still run.
    """
    rec = _Recorder()
    failures = shutdown_logic_components(**_components(rec, failing=failing))

    assert "db_writer" in rec.calls, (
        f"{failing}.shutdown() raised and db_writer.shutdown() was skipped -- "
        f"queued conversation-log writes would be lost on exit"
    )
    # the step label is '<component>.<method>', e.g. 'tts.shutdown'
    assert [n.split('.')[0] for n, _ in failures] == [failing]
    assert isinstance(failures[0][1], RuntimeError)


@pytest.mark.parametrize(
    "failing",
    ["thread_supervisor", "reminders", "proactive_engine", "tts", "notes_memory", "db_writer"],
)
def test_every_other_step_still_runs_when_one_fails(failing: str) -> None:
    """One bad step must cost exactly one step, never the tail of the sequence."""
    rec = _Recorder()
    shutdown_logic_components(**_components(rec, failing=failing))
    expected = {"thread_supervisor", "reminders", "proactive_engine",
                "tts", "notes_memory", "db_writer"}
    assert expected.issubset(set(rec.calls)), (
        f"with {failing} failing, these steps never ran: "
        f"{sorted(expected - set(rec.calls))}"
    )


def test_shutdown_tolerates_missing_components() -> None:
    """
    Boot can fail before every component exists (bootstrap.py None-checks the
    same way), so the teardown must accept None for any of them.
    """
    assert shutdown_logic_components() == []
    rec = _Recorder()
    only_db = _Component(rec, "db_writer", "shutdown")
    assert shutdown_logic_components(db_writer=only_db) == []
    assert rec.calls == ["db_writer"]


def test_order_matches_the_original_teardown_sequence() -> None:
    """
    This fix changed the GUARDING, not the order. The sequence below is the
    one the original inline finally: block used; db_writer stays second-to-last
    so the conversation-log flush is reachable whatever failed above it.
    """
    rec = _Recorder()
    shutdown_logic_components(**_components(rec))
    assert rec.calls == [
        "thread_supervisor",
        "reminders",
        "proactive_engine",
        "notes_memory",
        "tts",
        "db_writer",
    ], "teardown order drifted from the original sequence"


def test_all_failures_are_reported_not_swallowed() -> None:
    """A silent cleanup failure is how the original defect stayed invisible."""
    rec = _Recorder()
    comps = _components(rec)
    comps["tts"] = _Component(rec, "tts", "shutdown", RuntimeError("audio"))
    comps["notes_memory"] = _Component(rec, "notes_memory", "close", OSError("disk"))

    failures = shutdown_logic_components(**comps)
    names = [n.split('.')[0] for n, _ in failures]
    # original sequence puts notes_memory before tts
    assert names == ["notes_memory", "tts"]
    assert "db_writer" in rec.calls
