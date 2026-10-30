"""
Voice stopwatch commands must work.

E2E audit 2026-10-30: the router recognised "start the stopwatch" / "lap" /
"stop the stopwatch" but the handlers called system_tools functions that did
not exist -> "Sorry, I ran into a problem" every time.
"""
import pytest

from sara.core.intent import detect_intent
from sara.tools.system import timers
from sara.tools import system as system_tools


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(timers._time, "monotonic", lambda: now[0])
    timers._sw.update(started_at=None, elapsed=0.0, laps=[], last_lap=0.0)
    return now


def test_the_functions_exist_on_the_module_the_handlers_call():
    for name in ("start_stopwatch", "lap_stopwatch", "stop_stopwatch"):
        assert callable(getattr(system_tools, name))


def test_start_lap_stop_flow(clock):
    assert system_tools.start_stopwatch() == "Stopwatch started."
    clock[0] += 12
    assert system_tools.lap_stopwatch() == "Lap 1: 12 seconds. Total 12 seconds."
    clock[0] += 63
    assert system_tools.lap_stopwatch() == "Lap 2: 1 minute 3 seconds. Total 1 minute 15 seconds."
    clock[0] += 5
    assert system_tools.stop_stopwatch() == "Stopwatch stopped at 1 minute 20 seconds with 2 laps."


def test_sensible_answers_when_not_running(clock):
    assert "isn't running" in system_tools.stop_stopwatch()
    assert "isn't running" in system_tools.lap_stopwatch()
    system_tools.start_stopwatch()
    assert "already running" in system_tools.start_stopwatch()
    clock[0] += 3
    system_tools.stop_stopwatch()
    assert "already stopped at 3 seconds" in system_tools.stop_stopwatch()


def test_restart_begins_from_zero(clock):
    system_tools.start_stopwatch(); clock[0] += 50; system_tools.stop_stopwatch()
    system_tools.start_stopwatch(); clock[0] += 2
    assert system_tools.stop_stopwatch() == "Stopwatch stopped at 2 seconds."


@pytest.mark.parametrize("seconds, spoken", [
    (0, "0 seconds"), (1, "1 second"), (60, "1 minute"), (75, "1 minute 15 seconds"), (3725, "1 hour 2 minutes 5 seconds"),
])
def test_spoken_duration(seconds, spoken):
    assert timers._spoken_duration(seconds) == spoken


def test_through_the_dispatcher_with_clock_card_events(clock):
    from sara.orchestrator.handlers.timers import _h_start_stopwatch, _h_stop_stopwatch

    events, spoken = [], []
    ctx = {"ui_update": lambda *a: events.append(a), "tts": type("T", (), {"speak": lambda s, t, **k: spoken.append(t)})()}
    _h_start_stopwatch(None, ctx); clock[0] += 4; _h_stop_stopwatch(None, ctx)
    card = [e for e in events if e[0] == "stopwatch_state"]
    assert card == [("stopwatch_state", {"status": "started"}), ("stopwatch_state", {"status": "paused"})]
    assert detect_intent("start the stopwatch")[0] == "start_stopwatch"
