"""Live-test finding: volume / brightness commands touched COM pointers on arbitrary threads; the process died with an
access violation in _ctypes.pyd about every 80 commands. All such calls now run on ONE COM thread."""
import threading

import pytest

from sara.core import com_thread


def test_every_job_runs_on_the_same_named_thread_not_the_caller():
    names = {com_thread.run_on_com_thread(lambda: threading.current_thread().name) for _ in range(5)}
    assert len(names) == 1 and names.pop().startswith("sara-com")
    assert threading.current_thread().name != "sara-com_0"


def test_result_and_exception_travel_back_to_the_caller():
    assert com_thread.run_on_com_thread(lambda: 41 + 1) == 42
    with pytest.raises(ZeroDivisionError):
        com_thread.run_on_com_thread(lambda: 1 / 0)


def test_concurrent_callers_are_serialised():
    seen, lock, out = [], threading.Lock(), []

    def job(i):
        def fn():
            with lock:
                seen.append(i)
            return i
        out.append(com_thread.run_on_com_thread(fn))

    ts = [threading.Thread(target=job, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert sorted(out) == list(range(8))


def test_volume_and_brightness_helpers_use_the_com_thread(monkeypatch):
    from sara.tools.system import audio_display as a

    calls = []
    def spy(fn, **k):
        calls.append(1)
        return fn()

    monkeypatch.setattr(a, "run_on_com_thread", spy)
    import types, sys

    sbc = types.SimpleNamespace(get_brightness=lambda: [40], set_brightness=lambda v: None)
    monkeypatch.setitem(sys.modules, "screen_brightness_control", sbc)
    assert "40%" in a.get_brightness_status()
    assert "55%" in a.adjust_brightness(15)
    assert "20%" in a.set_brightness(20)
    assert len(calls) == 4          # get, get+set (adjust), set
