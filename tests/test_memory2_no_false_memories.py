"""
Retrieval must not answer with somebody else's fact or a different kind
of fact (live E2E and memory bench, 2026-10-30):

* "where do I work?" answered "Rohan works at Google" (the friend's job);
* "mera favorite khana kya hai?" answered "favorite color is blue".

And the relation hop must work: "where does my sister live?" ->
"Priya lives in Pune".
"""
import time

import pytest

from sara.core.memory2.hashembed import hash_embed
from sara.core.memory2.retrieve import retrieve
from sara.core.memory2.store import Memory2Store


@pytest.fixture
def store(tmp_path):
    now = time.time()
    st = Memory2Store(db_path=tmp_path / "m.db", embed=hash_embed, clock=lambda: now)
    for subject, predicate, obj in [
        ("user", "favorite_color", "blue"),
        ("user", "has_sister", "Priya"),
        ("Priya", "lives_in", "Pune"),
        ("user", "has_friend", "Rohan"),
        ("Rohan", "works_at", "Google"),
    ]:
        st.upsert_fact(subject, predicate, obj, valid_from=now, confidence=0.9,
                       importance=0.6, source_turn_id="t", now=now)
    yield st, now
    st.close()


def _ask(store, q):
    st, now = store
    return [h.text for h in retrieve(q, store=st, now=now, embed=hash_embed)]


def test_first_person_question_never_gets_someone_elses_fact(store):
    assert not any("Rohan" in t for t in _ask(store, "where do I work?"))


def test_a_different_kind_of_fact_is_not_a_stand_in(store):
    assert not any("color" in t for t in _ask(store, "mera favorite khana kya hai?"))


def test_relation_hops_still_work(store):
    assert "Priya lives in Pune" in _ask(store, "where does my sister live?")
    assert "Rohan works at Google" in _ask(store, "where does my friend work?")
    assert "Rohan works at Google" in _ask(store, "mere dost ki job kahan hai?")


def test_the_users_own_fact_is_still_found(store):
    assert "user's favorite color is blue" in _ask(store, "what is my favorite color?")
