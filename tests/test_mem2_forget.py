"""Tests for Memory 2.0 forgetting: matching, preview, retract, wipe and the handler flow."""
from __future__ import annotations

import re
from datetime import datetime
from importlib import util
from pathlib import Path

import pytest

import sara.core.memory2 as memory2
from sara.core.memory2 import forget
from sara.core.memory2 import retrieve as R
from sara.core.memory2.hashembed import hash_embed
from sara.core.memory2.store import Memory2Store
from sara.core.memory2.types import Mem2Hit
from sara.orchestrator.handlers import memory_intents as mi

AT = datetime(2026, 4, 1, 10, 0).timestamp()
BEFORE = AT - 30 * 86400


@pytest.fixture(autouse=True)
def _no_touch(monkeypatch):
    monkeypatch.setattr(R, "_touch_async", lambda *a, **k: None)


@pytest.fixture
def store(tmp_path):
    s = Memory2Store(str(tmp_path / "m.sqlite"), embed=hash_embed, clock=lambda: AT)
    s.upsert_fact("user", "name_is", "Riya", valid_from=BEFORE, now=BEFORE)
    s.upsert_fact("user", "likes", "tea", valid_from=BEFORE, now=BEFORE)
    s.upsert_fact("user", "lives_in", "Jaipur", valid_from=BEFORE, now=BEFORE)
    s.upsert_fact("user", "note", "phone number nine eight seven six", valid_from=BEFORE, now=BEFORE)
    yield s
    s.close()


def texts(hits):
    return [h.text for h in hits]


def test_find_matches_precision(store):
    assert any("phone number" in t for t in texts(forget.find_matches("my phone number", store=store)))
    assert not any("tea" in t for t in texts(forget.find_matches("my phone number", store=store)))
    assert texts(forget.find_matches("my address", store=store)) == ["user lives in Jaipur"]
    assert texts(forget.find_matches("that I like tea", store=store)) == ["user likes tea"]
    assert texts(forget.find_matches("mera naam", store=store)) == ["user's name is Riya"]
    assert forget.find_matches("my favourite spaceship", store=store) == []
    assert forget.find_matches("", store=store) == []


def test_preview_text_languages():
    hits = [Mem2Hit("facts", i, f"fact {i}", 1.0, "", 1.0, None, "active", False) for i in range(1, 6)]
    english = forget.preview_text(hits)
    assert english.startswith("I found 5 memories") and "fact 3" in english and "fact 4" not in english
    assert english.endswith("Delete them? Say yes or cancel.") and "2 more" in english
    hinglish = forget.preview_text(hits[:1], "hinglish")
    assert hinglish.startswith("Mujhe 1 yaadein mili") and "cancel" in hinglish
    assert forget.preview_text([]) == ""


def test_retract_hits_removes_from_retrieval_and_vectors(store):
    hits = forget.find_matches("my phone number", store=store)
    assert forget.retract_hits(hits, store=store) == len(hits) >= 1
    assert not any("phone" in h.text for h in R.retrieve("what is my phone number?", store=store,
                                                          now=AT, embed=hash_embed))
    vec = hash_embed("phone number nine eight")
    found = store.vector_candidates(vec, ("facts",), 20, ("active", "retracted"))
    assert hits[0].id not in {item[1] for item in found}


def test_forget_all_wipes_store(store):
    forget.forget_all(store=store)
    assert store.iter_rows("facts", ("active", "superseded", "archived", "retracted")) == []
    forget.forget_all(store=None)


def _ctx():
    return {"confirm_state": {}, "ui_update": lambda *a, **k: None, "notes_memory": None, "db": None}


@pytest.fixture
def handler(monkeypatch, store):
    monkeypatch.setattr(mi, "_quick", lambda ctx, text: text)
    monkeypatch.setattr(mi, "_ack", lambda ctx: None)
    monkeypatch.setattr(memory2, "get_store", lambda: store)
    return store


def test_handler_arm_then_confirm_retracts(handler):
    ctx = _ctx()
    spoken = mi._h_memory_forget_specific(re.match(r"forget (.*)", "forget my phone number"), ctx)
    pending = ctx["confirm_state"]["pending"]
    assert pending["action"] == "forget_memory_items" and pending["mem2_ids"]
    assert pending["expires_at"] > 0 and spoken.startswith("I found")
    assert any("phone" in t for t in texts(forget.find_matches("my phone number", store=handler)))
    reply = mi.apply_forget_items(pending, ctx)
    assert reply.startswith("Okay, I've forgotten 1 thing")
    assert forget.find_matches("my phone number", store=handler) == []


def test_handler_arm_then_cancel_leaves_memory(handler):
    ctx = _ctx()
    mi._h_memory_forget_specific(re.match(r"forget (.*)", "forget that I like tea"), ctx)
    ctx["confirm_state"].pop("pending")
    assert texts(forget.find_matches("that I like tea", store=handler)) == ["user likes tea"]


def test_handler_nothing_found_uses_old_reply(handler):
    ctx = _ctx()
    reply = mi._h_memory_forget_specific(re.match(r"forget (.*)", "forget my spaceship"), ctx)
    assert "pending" not in ctx["confirm_state"] and "long-term memory" in reply


def test_handler_memory2_disabled_keeps_old_flow(monkeypatch):
    monkeypatch.setattr(mi, "_quick", lambda ctx, text: text)
    monkeypatch.setattr(memory2, "get_store", lambda: None)
    ctx = _ctx()
    reply = mi._h_memory_forget_specific(re.match(r"forget (.*)", "forget my phone number"), ctx)
    assert "pending" not in ctx["confirm_state"] and "long-term memory" in reply


def test_recall_prefers_memory2(handler, monkeypatch):
    monkeypatch.setattr(mi, "_quick", lambda ctx, text: text)
    spoken = mi._h_memory_recall(None, _ctx())
    assert spoken.startswith("Here's what I remember:") and "since" in spoken


def test_forget_all_branch_is_wired():
    spec = util.find_spec("sara.orchestrator.dispatcher")
    source = Path(spec.origin).read_text(encoding="utf-8")
    assert "forget_memory_items" in source and "forget_all()" in source