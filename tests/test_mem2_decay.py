"""Tests for Memory 2.0 strength decay and the daily pass (temp store, injected clock)."""
from __future__ import annotations

import math

import pytest

from sara.core.memory2 import decay
from sara.core.memory2.hashembed import hash_embed
from sara.core.memory2.store import Memory2Store

DAY = 86400.0
NOW = 2_000_000_000.0


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    values = {"MEMORY2_ARCHIVE_THRESHOLD": 0.05, "MEMORY2_PURGE_DAYS": 90, "MEMORY2_DECAY_TAU_DAYS": 60}
    monkeypatch.setattr(decay, "cfg_value", lambda name, default: values.get(name, default))


@pytest.fixture
def store(tmp_path):
    s = Memory2Store(str(tmp_path / "m.sqlite"), embed=hash_embed, clock=lambda: NOW)
    yield s
    s.close()


def test_strength_formula():
    assert decay.strength(0.8, NOW, 0, False, NOW, 60) == pytest.approx(0.8)
    expected = 0.8 * math.exp(-30 / 60)
    assert decay.strength(0.8, NOW - 30 * DAY, 0, False, NOW, 60) == pytest.approx(expected)
    slower = decay.strength(0.8, NOW - 30 * DAY, 10, False, NOW, 60)
    assert slower > expected
    assert decay.strength(0.1, NOW - 999 * DAY, 0, True, NOW, 60) == 1.0
    assert decay.strength(0.5, NOW + DAY, 0, False, NOW, 60) == pytest.approx(0.5)


def test_daily_pass_archives_purges_and_respects_pinned(store):
    fresh = store.upsert_fact("user", "likes", "tea", valid_from=NOW - DAY, now=NOW - DAY).fact_id
    weak = store.upsert_fact("user", "likes", "jazz", valid_from=NOW - 400 * DAY, importance=0.3,
                             now=NOW - 400 * DAY).fact_id
    pinned = store.upsert_fact("user", "likes", "chess", valid_from=NOW - 400 * DAY, importance=0.3,
                               now=NOW - 400 * DAY).fact_id
    store.set_pinned(pinned, "facts", True)
    gone = store.upsert_fact("user", "likes", "golf", valid_from=NOW - 400 * DAY, importance=0.3,
                             now=NOW - 400 * DAY).fact_id
    store.set_status(gone, "facts", "archived")
    result = decay.daily_pass(store, now=NOW)
    assert result == {"archived": 1, "purged": 1, "ran": True}
    status = {r["id"]: r["status"] for r in store.iter_rows("facts", ("active", "archived"))}
    assert status[fresh] == "active" and status[pinned] == "active" and status[weak] == "archived"
    assert gone not in status


def test_daily_pass_runs_once_per_day(store):
    assert decay.daily_pass(store, now=NOW)["ran"] is True
    again = decay.daily_pass(store, now=NOW + 3600)
    assert again == {"archived": 0, "purged": 0, "ran": False}
    assert decay.daily_pass(store, now=NOW + DAY + 60)["ran"] is True


def test_daily_pass_never_raises():
    class Broken:
        def get_meta(self, *a, **k):
            raise RuntimeError("boom")

    assert decay.daily_pass(Broken(), now=NOW)["ran"] is False