"""Part 3 / P17: the GC pause histogram records collections and tuning restores cleanly."""
import gc

from sara.core import gc_tuning as G


def test_histogram_records_pauses_and_stats_are_ordered():
    G.reset()
    G.install_histogram()
    for _ in range(5):
        gc.collect()
    s = G.stats()
    assert s["n"] >= 5 and 0 <= s["p50_ms"] <= s["p99_ms"] <= s["max_ms"]
    G.reset()
    assert G.stats() == {"n": 0, "p50_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}


def test_install_is_idempotent():
    G.install_histogram()
    G.install_histogram()
    assert gc.callbacks.count(G._callback) == 1


def test_tune_sets_thresholds_and_freezes(monkeypatch):
    old = gc.get_threshold()
    try:
        G.tune_after_warmup(threshold0=12345, threshold1=7, threshold2=9)
        assert gc.get_threshold() == (12345, 7, 9) and gc.get_freeze_count() > 0
        assert G._installed["tuned"] is True
    finally:
        gc.set_threshold(*old)
        gc.unfreeze()
        G._installed["tuned"] = False


def test_idle_collect_never_raises():
    G.idle_collect()
