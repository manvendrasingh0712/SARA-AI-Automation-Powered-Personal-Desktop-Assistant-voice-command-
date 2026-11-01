"""Part 3 / P16: process tuning helpers are safe everywhere and keep-warm obeys idle / battery."""
import sys

from sara.core import proc_tuning as P


def test_disable_power_throttling_never_raises():
    assert P.disable_power_throttling() in (True, False)


def test_non_windows_is_a_noop(monkeypatch):
    monkeypatch.setattr(P.sys, "platform", "linux")
    assert P.disable_power_throttling() is False


def test_on_battery_values(monkeypatch):
    import psutil

    class B:
        power_plugged = False

    monkeypatch.setattr(psutil, "sensors_battery", lambda: B())
    assert P.on_battery() is True
    B.power_plugged = True
    assert P.on_battery() is False
    monkeypatch.setattr(psutil, "sensors_battery", lambda: None)
    assert P.on_battery() is None


def test_keep_warm_only_when_idle_and_plugged():
    hits = []
    state = {"idle": False, "bat": False}
    kw = P.KeepWarm(lambda: hits.append(1), 60, idle=lambda: state["idle"], battery=lambda: state["bat"])
    assert kw.tick() is False                    # busy
    state["idle"] = True
    assert kw.tick() is True and hits == [1]
    state["bat"] = True
    assert kw.tick() is False                    # on battery
    kw.skip_on_battery = False
    assert kw.tick() is True and kw.pings == 2


def test_keep_warm_survives_a_failing_ping_and_zero_interval_is_off():
    def boom():
        raise RuntimeError("ollama down")

    kw = P.KeepWarm(boom, 60, battery=lambda: False)
    assert kw.tick() is False
    assert P.KeepWarm(lambda: None, 0).start() is False


def test_keep_warm_thread_starts_and_stops():
    kw = P.KeepWarm(lambda: None, 3600)
    assert kw.start() is True and kw.start() is False
    kw.stop()
