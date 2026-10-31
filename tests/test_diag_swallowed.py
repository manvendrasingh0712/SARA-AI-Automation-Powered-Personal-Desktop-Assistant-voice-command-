"""Audit Task 6: swallowed exceptions are counted and logged at most once per 30 s per place."""
import logging

from sara.core import diag


def test_counts_every_time_logs_once_per_window(caplog):
    diag.reset()
    log = logging.getLogger("sara.test_swallow")
    with caplog.at_level(logging.DEBUG, logger="sara.test_swallow"):
        for _ in range(5):
            diag.swallowed(log, "unit.place", ValueError("boom"))
        diag.swallowed(log, "unit.other", OSError("x"))
    assert diag.counts() == {"unit.place": 5, "unit.other": 1}
    lines = [r.getMessage() for r in caplog.records if "[swallowed]" in r.getMessage()]
    assert len(lines) == 2 and "ValueError" in lines[0] and "boom" in lines[0]


def test_never_raises_even_with_a_broken_logger():
    class Bad:
        def debug(self, *a, **k):
            raise RuntimeError("logger exploded")

    diag.reset()
    diag.swallowed(Bad(), "unit.bad", KeyError("k"))  # must not raise
    assert diag.counts()["unit.bad"] == 1


def test_hot_files_no_longer_have_silent_pass_handlers_except_deliberate_ones():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("count_swallowed", root / "scripts" / "count_swallowed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    llm = [p for p in (root / "sara" / "core" / "llm").glob("*.py")]
    tot, _ = mod.scan(llm)
    assert tot["pass"] <= 3, tot  # cleanup / shutdown handlers keep their `pass`
