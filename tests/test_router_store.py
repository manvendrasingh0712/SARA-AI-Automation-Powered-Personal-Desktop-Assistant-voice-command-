"""RouterStore: schema, queries, pruning, reset, bounded queue, failure isolation."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from sara.core.router.store import RouterStore

DAY = 86400.0
COLUMNS = [
    "id", "ts", "turn_id", "tier", "static_tier", "propensity", "x_json", "quality",
    "latency_ms", "cost", "reward", "flags", "decision_id", "labelled",
]


class Clock:
    def __init__(self) -> None:
        self.t = 1_700_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def made(tmp_path):
    clock = Clock()
    store = RouterStore(path=str(tmp_path / "r.sqlite"), clock=clock)
    yield store, clock, tmp_path
    store.close()


def test_schema_matches_contract(made):
    store, _, tmp = made
    assert store.add_sample(tier="a", decision_id="d1") >= 1
    conn = sqlite3.connect(str(tmp / "r.sqlite"))
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(router_samples)")]
    finally:
        conn.close()
    assert cols == COLUMNS


def test_add_update_get_recent_count(made):
    store, _, _ = made
    store.add_sample(tier="a", decision_id="d1", turn_id="t1")
    store.add_sample(tier="b", decision_id="d2", labelled=1)
    assert store.count() == 2
    assert store.count(labelled_only=True) == 1
    assert [r["decision_id"] for r in store.recent(5)] == ["d2", "d1"]
    assert store.get_by_turn("t1")["decision_id"] == "d1"
    assert store.get_by_turn("zzz") is None
    assert store.update_sample("d1", quality=0.9, labelled=1) is True
    assert store.update_sample("nope", quality=0.9) is False
    assert store.update_sample("d1", bogus=1) is False
    assert store.get_by_turn("t1")["quality"] == pytest.approx(0.9)
    assert store.count(labelled_only=True) == 2


def test_usage_and_avg_latency_window(made):
    store, clock, _ = made
    assert store.avg_latency() is None
    store.add_sample(tier="a", decision_id="old", ts=clock.t - 8 * DAY, latency_ms=9000.0)
    store.add_sample(tier="b", decision_id="n1", ts=clock.t - 10, latency_ms=100.0)
    store.add_sample(tier="b", decision_id="n2", ts=clock.t - 5, latency_ms=300.0)
    assert store.usage(7) == {"b": 2}
    assert store.avg_latency(7) == pytest.approx(200.0)


def test_avg_quality_split_by_applied(made):
    store, _, _ = made
    store.add_sample(tier="a", decision_id="x1", flags="mode:on,applied", labelled=1, quality=0.4)
    store.add_sample(tier="a", decision_id="x2", flags="mode:shadow", labelled=1, quality=0.8)
    assert store.avg_quality(True, 50) == (pytest.approx(0.4), 1)
    assert store.avg_quality(False, 50) == (pytest.approx(0.8), 1)


def test_prune_and_reset(made):
    store, clock, _ = made
    store.add_sample(tier="a", decision_id="old", ts=clock.t - 70 * DAY)
    store.add_sample(tier="a", decision_id="new", ts=clock.t - DAY)
    assert store.prune(60) == 1
    assert store.count() == 1
    store.reset()
    assert store.count() == 0


def test_async_path_keeps_order(made):
    store, _, _ = made
    assert store.submit_add({"tier": "a", "decision_id": "d1", "labelled": 0})
    assert store.submit_update("d1", quality=0.5, labelled=1)
    assert store.flush(3.0)
    row = store.recent(1)[0]
    assert row["labelled"] == 1 and row["quality"] == pytest.approx(0.5)


def test_queue_full_drops_and_counts(tmp_path, monkeypatch):
    store = RouterStore(path=str(tmp_path / "q.sqlite"), queue_max=1)
    release = threading.Event()
    monkeypatch.setattr(store, "add_sample", lambda **row: release.wait(5.0))
    try:
        results = [store.submit_add({"decision_id": f"d{i}"}) for i in range(10)]
        assert False in results
        assert store.dropped >= 1
    finally:
        release.set()
        store.flush(3.0)
        store.close()


def test_failure_never_raises(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    store = RouterStore(path=str(blocker / "r.sqlite"))
    assert store.add_sample(tier="a") == -1
    assert store.ok is False
    assert store.count() == 0
    assert store.recent() == []
    assert store.usage() == {}
    assert store.avg_latency() is None
    assert store.update_sample("d", quality=1.0) is False
    store.close()


def test_close_then_reuse(made):
    store, _, _ = made
    store.add_sample(tier="a", decision_id="d1")
    store.close()
    assert store.count() == 1