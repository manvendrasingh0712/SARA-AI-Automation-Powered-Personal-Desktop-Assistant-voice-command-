"""Tests for the consolidation hook that drives Memory 2.0."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import sara.core.memory_consolidation as mc
from config import Config
from sara.core.memory2 import worker


class Brain:
    def is_usable(self):
        return True


def run_loop(rag, stop_after, calls, monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(Config, "MEMORY_CONSOLIDATION_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "MEMORY_CONSOLIDATION_INTERVAL_S", 0.01, raising=False)
    monkeypatch.setattr(mc, "_STOP_POLL_S", 0.01)

    def watch():
        if len(calls) >= stop_after:
            stop.set()

    return stop, watch


@pytest.fixture
def mem2_on(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY2_ENABLED", True, raising=False)


def test_hook_calls_tick_after_old_tick(monkeypatch, mem2_on):
    calls = []
    stop, watch = run_loop(None, 2, calls, monkeypatch)
    monkeypatch.setattr(mc, "_consolidation_tick", lambda *a: calls.append("old"))
    monkeypatch.setattr(worker, "tick", lambda *a, **k: (calls.append("new"), watch()))
    mc._consolidation_loop(object(), Brain(), SimpleNamespace(enabled=True), stop)
    assert calls[:2] == ["old", "new"]


def test_raising_tick_does_not_stop_loop(monkeypatch, mem2_on):
    calls = []
    stop, watch = run_loop(None, 3, calls, monkeypatch)
    monkeypatch.setattr(mc, "_consolidation_tick", lambda *a: calls.append("old"))

    def bad(*_a, **_k):
        calls.append("new")
        watch()
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "tick", bad)
    mc._consolidation_loop(object(), Brain(), SimpleNamespace(enabled=True), stop)
    assert calls.count("old") >= 2 and calls.count("new") >= 2


def test_memory2_ticks_when_rag_disabled(monkeypatch, mem2_on):
    calls = []
    stop, watch = run_loop(None, 2, calls, monkeypatch)
    monkeypatch.setattr(mc, "_consolidation_tick", lambda *a: calls.append("old"))
    monkeypatch.setattr(worker, "tick", lambda *a, **k: (calls.append("new"), watch()))
    mc._consolidation_loop(object(), Brain(), SimpleNamespace(enabled=False), stop)
    assert "old" not in calls and calls.count("new") >= 2


def test_rag_disabled_and_memory2_off_keeps_old_behaviour(monkeypatch):
    monkeypatch.setattr(Config, "MEMORY2_ENABLED", False, raising=False)
    calls = []
    stop = threading.Event()
    monkeypatch.setattr(Config, "MEMORY_CONSOLIDATION_ENABLED", True, raising=False)
    monkeypatch.setattr(mc, "_STOP_POLL_S", 0.01)
    monkeypatch.setattr(mc, "_consolidation_tick", lambda *a: calls.append("old"))
    monkeypatch.setattr(worker, "tick", lambda *a, **k: calls.append("new"))
    threading.Timer(0.1, stop.set).start()
    mc._consolidation_loop(object(), Brain(), SimpleNamespace(enabled=False), stop)
    assert calls == []


def test_unusable_brain_skips_everything(monkeypatch, mem2_on):
    calls = []
    stop, _ = run_loop(None, 1, calls, monkeypatch)
    monkeypatch.setattr(mc, "_consolidation_tick", lambda *a: calls.append("old"))
    monkeypatch.setattr(worker, "tick", lambda *a, **k: calls.append("new"))
    threading.Timer(0.1, stop.set).start()
    brain = SimpleNamespace(is_usable=lambda: False)
    mc._consolidation_loop(object(), brain, SimpleNamespace(enabled=True), stop)
    assert calls == []