"""EarlyCutGuard tests with an injected clock."""
import threading

import pytest

from sara.audio.stt.endpoint import EarlyCutGuard


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(window=500.0, step=100.0):
    clock = Clock()
    return EarlyCutGuard(window, step, clock=clock), clock


def cut(guard, clock, text="open chrome and"):
    guard.note_endpoint(text, "continuation")
    clock.t += 0.25
    return guard.note_next_speech_start(None)


def clean(guard, n=1):
    for _ in range(n):
        guard.note_endpoint("open chrome", "complete")


@pytest.mark.parametrize(
    "prev,hint,want",
    [
        ("open chrome and", None, True),
        ("open chrome", None, False),
        ("open chrome", "and play music", True),
        ("open chrome", "play music", False),
        (None, None, False),
        (None, "aur gaana", True),
        ("mujhe weather ke liye", None, True),
        ("मुझे मौसम के", None, True),
        ("मौसम बताओ", "फिर गाना", True),
    ],
)
def test_detection(prev, hint, want):
    guard, clock = make()
    guard.note_endpoint(prev, "base")
    clock.t += 0.25
    assert guard.note_next_speech_start(hint) is want


@pytest.mark.parametrize("delta,want", [(0.0, True), (0.5, True), (0.5078125, False), (3.0, False)])
def test_window_edges(delta, want):
    guard, clock = make()
    guard.note_endpoint("open chrome and", "continuation")
    clock.t += delta
    assert guard.note_next_speech_start(None) is want


def test_no_pending_and_single_use():
    guard, clock = make()
    assert guard.note_next_speech_start(None) is False
    guard.note_endpoint("open chrome and", "continuation")
    assert guard.note_next_speech_start(None) is True
    assert guard.note_next_speech_start(None) is False


def test_backoff_growth_and_cap():
    guard, clock = make()
    got = []
    for _ in range(7):
        cut(guard, clock)
        got.append(guard.backoff_ms())
    assert got == [0.0, 0.0, 100.0, 200.0, 300.0, 300.0, 300.0]


def test_decay_after_twenty_clean():
    guard, clock = make()
    for _ in range(5):
        cut(guard, clock)
    assert guard.backoff_ms() == 300.0
    clean(guard, 19)
    assert guard.backoff_ms() == 300.0
    clean(guard)
    assert guard.backoff_ms() == 0.0
    cut(guard, clock)
    assert guard.backoff_ms() == 0.0


def test_old_cuts_roll_out_of_window():
    guard, clock = make()
    cut(guard, clock)
    cut(guard, clock)
    clean(guard, 20)
    cut(guard, clock)
    assert guard.backoff_ms() == 0.0


def test_zero_step_never_backs_off():
    guard, clock = make(step=0.0)
    for _ in range(5):
        cut(guard, clock)
    assert guard.backoff_ms() == 0.0


def test_outcomes_bounded_and_stats():
    guard, clock = make()
    cut(guard, clock)
    clean(guard, 49)
    assert len(guard._outcomes) == 20
    assert guard.stats() == {"endpoints": 50, "early_cuts": 1, "rules": {"continuation": 1, "complete": 49}}


def test_stats_is_a_copy():
    guard, _ = make()
    guard.stats()["rules"]["x"] = 5
    assert guard.stats()["rules"] == {}


def test_thread_safety():
    guard, _ = make()
    threads = [threading.Thread(target=clean, args=(guard, 100)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert guard.stats()["endpoints"] == 800