"""
Memory 2.0 fixes from the live E2E test (2026-10-30).

Saying 7 clear facts saved ZERO: extraction went through the chat path
(generate_response), whose 300-token spoken-reply cap cut the JSON, and
parse_json() then returned an inner object ({"name": "Jaipur"}) as if it
were the payload. Relations ("my sister is Riya") had no predicate, so
multi-hop questions could never resolve; "do din pehle" had no window;
a first-person question could be answered with someone else's fact.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from sara.core.memory2 import extract
from sara.core.memory2.extract_validate import validate
from sara.core.memory2.relations import relation_facts
from sara.core.memory2.schema import classify_predicate
from sara.core.memory2.timeparse import parse_window
from sara.core.memory2.util import fact_text


# ── extraction call path ────────────────────────────────────────────────


def test_extraction_prefers_the_stateless_task_call():
    brain = SimpleNamespace(
        generate_task=mock.Mock(return_value='{"entities":[],"facts":[],"events":[]}'),
        generate_response=mock.Mock(side_effect=AssertionError("chat path used")),
    )
    payload, reason = extract._ask(brain, "prompt")
    assert reason == "ok" and payload == {"entities": [], "facts": [], "events": []}


def test_a_truncated_reply_is_not_an_empty_success():
    cut = '{"entities":[{"name":"Jaipur","type":"place"}],"facts":[{"turn":1,"subj'
    brain = SimpleNamespace(generate_task=mock.Mock(return_value=cut))
    payload, reason = extract._ask(brain, "prompt")
    assert payload is None and reason == "bad_json"


def test_generate_task_writes_no_history(monkeypatch):
    from sara.core.llm import engine, provider_runtime

    b = engine.SaraLLM.__new__(engine.SaraLLM)
    b._cfg = SimpleNamespace()
    b._append_history = mock.Mock(side_effect=AssertionError("history written"))
    b.primary_breaker_active = lambda: False
    monkeypatch.setattr(
        provider_runtime, "generate_short",
        lambda *a, **k: SimpleNamespace(text='{"facts":[]}\nline2', failure="success"),
    )
    assert b.generate_task("x") == '{"facts":[]}\nline2'  # newlines kept


def test_generate_task_failure_names_the_kind(monkeypatch):
    from sara.core.llm import engine, provider_runtime

    b = engine.SaraLLM.__new__(engine.SaraLLM)
    b._cfg = SimpleNamespace()
    monkeypatch.setattr(
        provider_runtime, "generate_short",
        lambda *a, **k: SimpleNamespace(text=None, failure="quota"),
    )
    with pytest.raises(RuntimeError, match="quota"):
        b.generate_task("x")


# ── relations ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, canon", [
    ("has_sister", "has_sister"), ("has_dost", "has_friend"), ("has_behen", "has_sister"),
    ("has_mummy", "has_mother"), ("hasroommate", "has_roommate"),
])
def test_relation_predicates_are_first_class(raw, canon):
    assert classify_predicate(raw) == (canon, "multi")


def test_relation_fact_reads_as_a_sentence():
    assert fact_text("user", "has_sister", "Riya") == "user's sister is Riya"
    assert fact_text("user", "has_pet", "Bruno") == "user has a pet: Bruno"


@pytest.mark.parametrize("text, facts", [
    ("my sister's name is Priya and she lives in Pune",
     [("user", "has_sister", "Priya"), ("Priya", "lives_in", "Pune")]),
    ("mere dost ka naam Rohan hai", [("user", "has_friend", "Rohan")]),
    ("mere dost Rohan ki job Google mein hai", [("user", "has_friend", "Rohan")]),
    ("My mother is Sunita", [("user", "has_mother", "Sunita")]),
    ("my roommate Dev studies at IIT Delhi",
     [("user", "has_roommate", "Dev"), ("Dev", "studies_at", "IIT Delhi")]),
    ("my sister's name is Priya and I live in Jaipur", [("user", "has_sister", "Priya")]),
])
def test_relation_extractor(text, facts):
    got = [(f["subject"], f["predicate"], f["object"]) for f in relation_facts(text)]
    assert got == facts


@pytest.mark.parametrize("text", [
    "my sister is sick today", "My Sister is Sick", "my friend is busy",
    "my mother is a teacher", "my brother bought a car",
])
def test_relation_extractor_never_invents_names(text):
    assert relation_facts(text) == []


def test_a_description_is_not_a_relative_name():
    payload = {"facts": [
        {"turn": 1, "subject": "user", "predicate": "has_mother", "object": "a teacher in Delhi",
         "confidence": 0.9},
        {"turn": 1, "subject": "user", "predicate": "has_mother", "object": "Sunita", "confidence": 0.9},
    ]}
    facts = validate(payload, min_confidence=0.5, skip_categories=set())["facts"]
    assert [f["object"] for f in facts] == ["Sunita"]


# ── time words ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, days_back", [
    ("do din pehle maine kya bataya tha?", 2), ("दो दिन पहले", 2), ("char din pehle", 4),
])
def test_spelled_numbers_make_a_window(text, days_back):
    now = datetime(2026, 4, 6, 9, 0).timestamp()
    start, _ = parse_window(text, now)
    assert datetime.fromtimestamp(start).day == 6 - days_back


@pytest.mark.parametrize("text", ["do you remember my name", "what do I do", "I have to do this"])
def test_english_do_is_not_a_number(text):
    assert parse_window(text, datetime(2026, 4, 6, 9).timestamp()) is None
