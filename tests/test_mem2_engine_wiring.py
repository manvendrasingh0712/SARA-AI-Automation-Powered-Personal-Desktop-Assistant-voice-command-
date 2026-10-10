"""Engine wiring tests: Memory 2.0 hits first, abstention line, disabled == unchanged, timeout budget."""
from __future__ import annotations

import sys
import time
import types
from types import SimpleNamespace

import sara.core.memory2 as memory2
from sara.core.llm import engine as engine_module
from sara.core.memory2.types import Mem2Hit
from sara.core.rag import MemoryHit

ABSTAIN = "I have no stored memory that answers this question; say you do not know instead of guessing."


def _engine(budget_ms: int = 400):
    cls = next(v for v in vars(engine_module).values()
               if isinstance(v, type) and hasattr(v, "_merge_memory2_hits"))
    inst = object.__new__(cls)
    inst._cfg = SimpleNamespace(MEMORY2_RETRIEVAL_TIMEOUT_MS=budget_ms)
    return inst


def _stub(monkeypatch, hits, question=False, delay=0.0, enabled=True):
    def retrieve(prompt, **kwargs):
        time.sleep(delay)
        return list(hits)

    module = types.SimpleNamespace(retrieve=retrieve, is_memory_question=lambda prompt: question)
    monkeypatch.setitem(sys.modules, "sara.core.memory2.retrieve", module)
    monkeypatch.setattr(memory2, "is_enabled", lambda: enabled)


def _rag(text="rag text"):
    return [MemoryHit(id=1, text=text, score=0.9, source="chat", timestamp="t")]


def _m2hit(text="user lives in Jaipur", ident=7):
    return Mem2Hit("facts", ident, text, 0.8, "2026-03-02", 1.0, None, "active", False)


def test_memory2_hits_come_first_and_convert(monkeypatch):
    _stub(monkeypatch, [_m2hit()])
    eng = _engine()
    merged = eng._merge_memory2_hits(_rag(), eng._start_memory2_retrieval("where do I live?"))
    assert [h.text for h in merged] == ["user lives in Jaipur", "rag text"]
    assert merged[0].id == -7 and merged[0].source == "memory2" and merged[0].timestamp == "2026-03-02"


def test_abstention_only_when_both_empty_and_memory_question(monkeypatch):
    eng = _engine()
    _stub(monkeypatch, [], question=True)
    merged = eng._merge_memory2_hits([], eng._start_memory2_retrieval("what is my favorite movie?"))
    assert [h.text for h in merged] == [ABSTAIN]
    _stub(monkeypatch, [], question=True)
    assert [h.text for h in eng._merge_memory2_hits(_rag(), eng._start_memory2_retrieval("q"))] == ["rag text"]
    _stub(monkeypatch, [], question=False)
    assert eng._merge_memory2_hits([], eng._start_memory2_retrieval("tell me a joke")) == []


def test_disabled_leaves_context_unchanged(monkeypatch):
    _stub(monkeypatch, [_m2hit()], question=True, enabled=False)
    eng = _engine()
    job = eng._start_memory2_retrieval("what is my favorite movie?")
    original = _rag()
    assert job is None and eng._merge_memory2_hits(original, job) is original


def test_slow_retrieval_respects_budget(monkeypatch):
    _stub(monkeypatch, [_m2hit()], delay=1.0)
    eng = _engine(budget_ms=100)
    begin = time.monotonic()
    original = _rag()
    job = eng._start_memory2_retrieval("where do I live?")
    merged = eng._merge_memory2_hits(original, job)
    assert merged is original and time.monotonic() - begin < 0.5


def test_failing_retrieval_never_raises(monkeypatch):
    def boom(prompt, **kwargs):
        raise RuntimeError("boom")

    module = types.SimpleNamespace(retrieve=boom, is_memory_question=lambda p: True)
    monkeypatch.setitem(sys.modules, "sara.core.memory2.retrieve", module)
    monkeypatch.setattr(memory2, "is_enabled", lambda: True)
    eng = _engine()
    original = _rag()
    assert eng._merge_memory2_hits(original, eng._start_memory2_retrieval("q")) is original