"""Part 3 / P18: Ollama watchdog restarts a dead server, but only after it was seen up, with a restart cap."""
from sara.orchestrator.ollama_watchdog import OllamaWatchdog


class Mgr:
    def __init__(self):
        self.ready = True
        self.starts = 0
        self.start_result = True

    def is_ready(self):
        return self.ready

    def start(self):
        self.starts += 1
        self.ready = self.start_result
        return self.start_result


def test_healthy_server_is_left_alone():
    m = Mgr()
    w = OllamaWatchdog(m)
    assert [w.check_once() for _ in range(5)] == ["up"] * 5 and m.starts == 0


def test_restart_only_after_threshold_consecutive_failures():
    m = Mgr()
    w = OllamaWatchdog(m, fail_threshold=2)
    w.check_once()
    m.ready = False
    assert w.check_once() == "down" and m.starts == 0           # one blip is not enough
    m.ready = True
    assert w.check_once() == "up"                                # a recovered blip resets the count
    m.ready = False
    w.check_once()
    assert w.check_once() == "restarted" and m.starts == 1


def test_never_interferes_before_the_server_was_first_seen_up():
    m = Mgr()
    m.ready = False
    w = OllamaWatchdog(m, fail_threshold=1)
    assert [w.check_once() for _ in range(4)] == ["down"] * 4 and m.starts == 0


def test_restart_cap_stops_a_crash_loop():
    m = Mgr()
    events = []
    w = OllamaWatchdog(m, fail_threshold=1, max_restarts=2, on_event=lambda k, t: events.append(k))
    w.check_once()
    results = []
    for _ in range(4):
        m.ready = False
        m.start_result = False
        results.append(w.check_once())
    assert results == ["restart_failed", "restart_failed", "gave_up", "gave_up"] and m.starts == 2
    assert events == ["restart_failed", "restart_failed"]


def test_exceptions_never_escape_and_zero_interval_is_off():
    class Boom:
        def is_ready(self):
            raise RuntimeError("x")

    assert OllamaWatchdog(Boom()).check_once() == "down"
    assert OllamaWatchdog(Mgr(), interval_s=0).start() is False
    w = OllamaWatchdog(Mgr(), interval_s=3600)
    assert w.start() is True and w.start() is False
    w.stop()
