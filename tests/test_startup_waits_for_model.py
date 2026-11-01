"""Live-test finding: the "systems are online" greeting came ~20 s before the local model could answer."""
import inspect
from types import SimpleNamespace

from sara.orchestrator import core_wiring as cw


class Brain:
    def __init__(self, ok=True, boom=False):
        self.ok, self.boom, self.timeouts = ok, boom, []

    def wait_until_warm(self, timeout=30.0):
        self.timeouts.append(timeout)
        if self.boom:
            raise RuntimeError("brain failed to build")
        return SimpleNamespace(ok=self.ok)


def test_waits_for_the_model_and_shows_a_progress_message():
    sent, b = [], Brain()
    assert cw._wait_for_model(b, lambda *a: sent.append(a), 45.0) is True
    assert b.timeouts == [45.0]
    assert sent and sent[0][0] == "boot_progress" and "language model" in sent[0][1]


def test_zero_timeout_means_no_wait_at_all():
    sent, b = [], Brain()
    assert cw._wait_for_model(b, lambda *a: sent.append(a), 0) is True
    assert b.timeouts == [] and sent == []


def test_a_slow_or_broken_model_never_blocks_start_up():
    assert cw._wait_for_model(Brain(ok=False), lambda *a: None, 5.0) is False      # warm-up timed out
    assert cw._wait_for_model(Brain(boom=True), lambda *a: None, 5.0) is False     # brain could not be built


def test_greeting_comes_after_the_wait_and_before_ready():
    src = inspect.getsource(cw.run_sara_logic)
    wait, ready, greet = src.index("_wait_for_model("), src.index('"Ready!", 100'), src.index("tts.speak(greeting")
    assert wait < ready < greet
