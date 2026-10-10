"""Retrieval tests for Memory 2.0 (hash embeddings, temp DB, injected clock, no network)."""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from sara.core.memory2 import retrieve as R
from sara.core.memory2.hashembed import hash_embed
from sara.core.memory2.store import Memory2Store
from sara.core.memory2.types import Mem2Hit


def ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


AT = ts("2026-04-02T10:00:00")


@pytest.fixture(autouse=True)
def _no_touch(monkeypatch):
    monkeypatch.setattr(R, "_touch_async", lambda *a, **k: None)


@pytest.fixture
def store(tmp_path):
    s = Memory2Store(str(tmp_path / "m.sqlite"), embed=hash_embed, clock=lambda: AT)
    yield s
    s.close()


def load(store, facts=(), events=()):
    ids = []
    for when, subject, predicate, obj in facts:
        ids.append(store.upsert_fact(subject, predicate, obj, valid_from=ts(when), now=ts(when)).fact_id)
    for when, summary in events:
        store.add_event(summary, ts(when), now=ts(when))
    return ids


def ask(store, query, at=AT, **kw):
    return R.retrieve(query, store=store, now=at, embed=hash_embed, **kw)


def joined(hits):
    return " | ".join(h.text for h in hits)


def test_single_recall_english(store):
    load(store, [("2026-03-02T09:00:00", "user", "name_is", "Riya Sharma"),
                 ("2026-03-02T10:00:00", "user", "age", "21"),
                 ("2026-03-02T11:00:00", "user", "has_pet", "dog Bruno"),
                 ("2026-03-02T12:00:00", "user", "birthday", "14 August")])
    assert "Riya" in joined(ask(store, "what is my name?"))
    assert "21" in joined(ask(store, "how old am I?"))
    assert "Bruno" in joined(ask(store, "do I have a pet?"))
    assert "14 August" in joined(ask(store, "when is my birthday?"))


def test_single_recall_hinglish_and_hindi(store):
    load(store, [("2026-03-03T09:00:00", "user", "name_is", "Aman Verma"),
                 ("2026-03-03T10:00:00", "user", "likes", "cricket"),
                 ("2026-03-03T11:00:00", "user", "studies_at", "NIELIT"),
                 ("2026-03-03T12:00:00", "user", "lives_in", "पुणे")])
    assert "Aman" in joined(ask(store, "mera naam kya hai?"))
    assert "cricket" in joined(ask(store, "mujhe kaunsa khel pasand hai?"))
    assert "NIELIT" in joined(ask(store, "main kahan padhta hoon?"))
    assert "पुणे" in joined(ask(store, "मैं अभी कहाँ रहता हूँ?"))


def test_contradiction_current_wins_and_history_cue(store):
    load(store, [("2026-02-02T10:00:00", "user", "lives_in", "Ajmer"),
                 ("2026-03-11T18:30:00", "user", "lives_in", "Jaipur")])
    now = ts("2026-03-25T09:00:00")
    current = joined(ask(store, "where do I live now?", at=now))
    assert "Jaipur" in current and "Ajmer" not in current
    both = ask(store, "where did I live before?", at=now)
    assert "Ajmer" in joined(both) and "Jaipur" in joined(both)
    assert both[0].text.endswith("Jaipur")
    assert any(h.status == "superseded" and h.when.startswith("earlier") for h in both)


def test_temporal_windows(store):
    load(store, events=[("2026-03-02T19:00:00", "watched Interstellar with cousin"),
                        ("2026-03-09T11:00:00", "interview at Infosys"),
                        ("2026-03-16T17:30:00", "cricket match with friends"),
                        ("2026-03-17T21:00:00", "bought a new phone"),
                        ("2026-03-16T20:00:00", "called grandmother")])
    now = ts("2026-03-18T10:00:00")
    assert "cricket" in joined(ask(store, "what did I tell you last Monday?", at=now))
    assert "Infosys" in joined(ask(store, "what happened on Monday last week?", at=now))
    assert "Interstellar" in joined(ask(store, "what did I do two Mondays ago?", at=now))
    assert "phone" in joined(ask(store, "yesterday what did I buy?", at=now))
    assert "phone" in joined(ask(store, "kal maine kya kharida tha?", at=now))
    assert "grandmother" in joined(ask(store, "parso maine kisko call kiya tha?", at=now))


def test_multihop_neighbour_expansion(store):
    load(store, [("2026-03-03T10:00:00", "user", "note", "has sister: Riya"),
                 ("2026-03-03T10:05:00", "Riya", "works_at", "Infosys"),
                 ("2026-03-05T10:00:00", "user", "note", "has dost: Rohan"),
                 ("2026-03-05T10:05:00", "Rohan", "lives_in", "Bangalore")])
    now = ts("2026-04-01T10:00:00")
    assert "Infosys" in joined(ask(store, "where does my sister work?", at=now))
    assert "Infosys" in joined(ask(store, "where does Riya work?", at=now))
    assert "Bangalore" in joined(ask(store, "mere dost ka ghar kahan hai?", at=now))
    assert "Bangalore" in joined(ask(store, "Rohan kahan rehta hai?", at=now))


def test_abstention_returns_nothing(store):
    load(store, [("2026-03-04T09:00:00", "user", "lives_in", "Jaipur"),
                 ("2026-03-04T10:00:00", "user", "likes", "tea")])
    for query in ("what is my favorite movie?", "what is my mother's name?",
                  "do you remember my phone number?", "what is my favourite sport?",
                  "mere pita ka naam kya hai?", "meri gaadi ka rang kya hai?"):
        assert ask(store, query, at=ts("2026-04-01T10:00:00")) == []


def test_pinned_item_stays_strong(store):
    old = [("2025-01-01T10:00:00", "user", "likes", "tea"), ("2025-01-01T10:00:00", "user", "likes", "jazz")]
    first, _ = load(store, old)
    store.set_pinned(first, "facts", True)
    hits = {h.text.split()[-1]: h for h in ask(store, "what do I like?", min_score=0.0)}
    assert hits["tea"].pinned and hits["tea"].score > hits["jazz"].score


def test_scoring_monotonic_in_importance_and_recency(store):
    a = store.upsert_fact("user", "likes", "tea", valid_from=AT - 86400, importance=0.9, now=AT - 86400).fact_id
    b = store.upsert_fact("user", "likes", "jazz", valid_from=AT - 86400, importance=0.2, now=AT - 86400).fact_id
    c = store.upsert_fact("user", "likes", "chess", valid_from=AT - 90 * 86400, importance=0.9, now=AT).fact_id
    scores = {h.id: h.score for h in ask(store, "what do I like?", min_score=0.0)}
    assert scores[a] >= scores[b] and scores[a] >= scores[c]


def test_decay_changes_results_over_time(store):
    ids = load(store, [("2026-03-01T10:00:00", "user", "likes", "tea"),
                       ("2026-03-01T10:00:00", "user", "likes", "jazz")])
    store.set_pinned(ids[1], "facts", True)
    soon = {h.id: h.score for h in ask(store, "what do I like?", at=ts("2026-03-05T10:00:00"))}
    late = {h.id: h.score for h in ask(store, "what do I like?", at=ts("2030-03-05T10:00:00"))}
    assert soon[ids[0]] > late[ids[0]]
    assert late[ids[1]] > late[ids[0]]
    assert ask(store, "what do I like?", at=ts("2030-03-05T10:00:00"))[0].id == ids[1]


def test_k_and_min_score_respected(store):
    load(store, [("2026-03-02T10:00:00", "user", "likes", f"thing{i}") for i in range(6)])
    assert len(ask(store, "what do I like?", k=3)) == 3
    assert ask(store, "what do I like?", min_score=0.99) == []
    assert all(h.score >= 0.4 for h in ask(store, "what do I like?", min_score=0.4))


def test_timeout_fails_open(store):
    load(store, [("2026-03-02T10:00:00", "user", "likes", "tea")])

    def slow(_text):
        time.sleep(0.6)
        return hash_embed("x")

    begin = time.monotonic()
    out = R.retrieve("tell me something interesting", store=store, now=AT, embed=slow, timeout_ms=50)
    assert out == [] and time.monotonic() - begin < 0.4


def test_no_store_and_store_errors(store, monkeypatch):
    monkeypatch.setattr(R, "get_store", lambda: None)
    assert R.retrieve("where do I live?") == []

    class Broken:
        def iter_rows(self, *a, **k):
            raise RuntimeError("boom")

    assert R.retrieve("where do I live?", store=Broken(), embed=hash_embed) == []
    assert R.retrieve("   ", store=store) == []


def test_dimension_mismatch_is_skipped(store):
    load(store, [("2026-03-02T10:00:00", "user", "lives_in", "Jaipur")])
    hits = R.retrieve("where do I live?", store=store, now=AT, embed=lambda t: hash_embed(t, dim=8))
    assert "Jaipur" in joined(hits)


def test_last_hits_and_format_block(store):
    load(store, [("2026-03-02T10:00:00", "user", "lives_in", "Jaipur")])
    hits = ask(store, "where do I live?")
    copy = R.last_hits()
    assert copy == hits and copy is not R.last_hits()
    block = R.format_block(hits)
    assert block.startswith("- user lives in Jaipur (2026-03-02)")
    many = [Mem2Hit("facts", i, "x" * 50, 0.5, "d", 1.0, None, "active", False) for i in range(9)]
    assert len(R.format_block(many, max_chars=120, max_items=5)) <= 120
    assert R.format_block(many, max_items=2).count("\n") == 1


def test_is_memory_question():
    for q in ("do you remember my phone number?", "maine kya bataya tha?", "mera naam kya hai?",
              "what is my favorite movie?", "तुम्हें याद है मैंने क्या कहा"):
        assert R.is_memory_question(q)
    for q in ("write a poem about rain", "what is the capital of France?", "do I have to use Python?"):
        assert not R.is_memory_question(q)