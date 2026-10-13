"""Tests for the pinned phrase cache."""
import numpy as np
import pytest

from sara.audio.tts import cache as cache_mod
from sara.audio.voice_flags import reset_flags


@pytest.fixture(autouse=True)
def _clean_state():
    reset_flags()
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    cache_mod.unpin_all()
    yield
    reset_flags()
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    cache_mod.unpin_all()


# ── pinned cache ─────────────────────────────────────────────────────────────

def _put(i):
    cache_mod._phrase_cache_put(("k%d" % i, "en", False, None), np.ones(4, dtype=np.float32))


def test_legacy_lru_unchanged_without_pins(monkeypatch):
    monkeypatch.setattr(cache_mod, "_PHRASE_CACHE_MAX", 8)
    for i in range(20):
        _put(i)
    assert len(cache_mod._phrase_cache) == 8
    assert list(cache_mod._phrase_cache) == [("k%d" % i, "en", False, None) for i in range(12, 20)]
    assert cache_mod.effective_capacity() == 8


def test_lru_get_refreshes_recency_without_pins(monkeypatch):
    monkeypatch.setattr(cache_mod, "_PHRASE_CACHE_MAX", 3)
    for i in range(3):
        _put(i)
    cache_mod._phrase_cache_get(("k0", "en", False, None))
    _put(3)
    assert ("k0", "en", False, None) in cache_mod._phrase_cache
    assert ("k1", "en", False, None) not in cache_mod._phrase_cache


def test_pinned_entries_survive_eviction_pressure(monkeypatch):
    monkeypatch.setattr(cache_mod, "_PHRASE_CACHE_MAX", 8)
    pinned = [("k%d" % i, "en", False, None) for i in range(5)]
    for i in range(5):
        cache_mod.pin(pinned[i])
        _put(i)
    for i in range(100, 300):
        _put(i)
    assert all(k in cache_mod._phrase_cache for k in pinned)
    assert len(cache_mod._phrase_cache) <= cache_mod.effective_capacity()


def test_capacity_formula(monkeypatch):
    monkeypatch.setattr(cache_mod, "_PHRASE_CACHE_MAX", 8)
    assert cache_mod.effective_capacity() == 8
    for i in range(5):
        cache_mod.pin(("p%d" % i,))
    assert cache_mod.effective_capacity() == max(8, 5 + 32)
    monkeypatch.setattr(cache_mod, "_PHRASE_CACHE_MAX", 200)
    assert cache_mod.effective_capacity() == 200
    cache_mod.unpin_all()
    assert cache_mod.effective_capacity() == 200


def test_unpin_all_makes_entries_evictable_again(monkeypatch):
    monkeypatch.setattr(cache_mod, "_PHRASE_CACHE_MAX", 4)
    keys = [("k%d" % i, "en", False, None) for i in range(4)]
    for i, k in enumerate(keys):
        cache_mod.pin(k)
        _put(i)
    cache_mod.unpin_all()
    for i in range(10, 30):
        _put(i)
    assert not any(k in cache_mod._phrase_cache for k in keys)
    assert len(cache_mod._phrase_cache) == 4


def test_pin_set_is_bounded():
    for i in range(1000):
        cache_mod.pin(("p%d" % i,))
    assert len(cache_mod._pinned) == cache_mod._PIN_MAX


def test_eviction_never_loops_when_everything_is_pinned(monkeypatch):
    monkeypatch.setattr(cache_mod, "_PHRASE_CACHE_MAX", 1)
    for i in range(3):
        cache_mod.pin(("k%d" % i, "en", False, None))
        _put(i)
    assert len(cache_mod._phrase_cache) == 3