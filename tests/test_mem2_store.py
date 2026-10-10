"""Tests for sara.core.memory2 storage (schema, entities, facts, events, vectors)."""
from __future__ import annotations

import sqlite3
import threading

import numpy as np
import pytest

from sara.core.memory2.entities import normalize_name, resolve_entity
from sara.core.memory2.hashembed import hash_embed
from sara.core.memory2.schema import SCHEMA_VERSION, classify_predicate, open_db
from sara.core.memory2.store import Memory2Store

NOW = 1_000_000.0


@pytest.fixture
def store(tmp_path):
    s = Memory2Store(str(tmp_path / "m.sqlite"), embed=hash_embed, clock=lambda: NOW)
    yield s
    s.close()


def test_schema_created_and_idempotent(tmp_path):
    path = tmp_path / "m.sqlite"
    for _ in range(2):
        conn = open_db(path)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert {"mem2_entities", "mem2_facts", "mem2_events", "mem2_meta"} <= names
    s = Memory2Store(str(path), embed=hash_embed)
    assert s.get_meta("schema_version") == str(SCHEMA_VERSION)
    s.close()


def test_corrupt_file_recovers(tmp_path):
    path = tmp_path / "m.sqlite"
    path.write_bytes(b"garbage" * 700)
    s = Memory2Store(str(path), embed=hash_embed)
    assert list(tmp_path.glob("m.sqlite.bad-*"))
    assert s.upsert_fact("user", "lives_in", "Jaipur").action == "inserted"
    s.close()


def test_functional_supersede_keeps_history(store):
    store.upsert_fact("user", "lives_in", "Ajmer", valid_from=100.0)
    res = store.upsert_fact("user", "lives_in", "Jaipur", valid_from=200.0)
    assert res.action == "superseded_old" and len(res.superseded_ids) == 1
    old = store.get_item(res.superseded_ids[0], "facts")
    assert old["status"] == "superseded" and old["valid_to"] == 200.0
    active = store.iter_rows("facts")
    assert [r["object_text"] for r in active] == ["Jaipur"]
    assert len(store.iter_rows("facts", ("active", "superseded"))) == 2


def test_functional_reinforce(store):
    first = store.upsert_fact("user", "lives_in", "Jaipur", confidence=0.6)
    again = store.upsert_fact("user", "lives_in", "jaipur.", confidence=0.9)
    assert again.action == "reinforced" and again.fact_id == first.fact_id
    row = store.get_item(first.fact_id, "facts")
    assert row["use_count"] == 1 and row["confidence"] == 0.9


def test_multi_duplicate_by_text_and_cosine(tmp_path):
    s = Memory2Store(str(tmp_path / "a.sqlite"), embed=hash_embed)
    assert s.upsert_fact("user", "likes", "Cricket").action == "inserted"
    assert s.upsert_fact("user", "likes", "cricket").action == "duplicate"
    assert s.upsert_fact("user", "likes", "chess").action == "inserted"
    s.close()
    same = Memory2Store(str(tmp_path / "b.sqlite"), embed=lambda _t: np.ones(8, dtype=np.float32))
    assert same.upsert_fact("user", "likes", "tea").action == "inserted"
    assert same.upsert_fact("user", "likes", "coffee").action == "duplicate"
    same.close()


def test_unknown_predicate_becomes_note(store):
    store.upsert_fact("user", "brother", "Rahul")
    row = store.iter_rows("facts")[0]
    assert row["predicate"] == "note" and classify_predicate("Lives In") == ("lives_in", "functional")
    assert classify_predicate("interestedin") == ("interested_in", "multi")


def test_embed_failure_leaves_null_blob(tmp_path):
    def boom(_text):
        raise RuntimeError("no model")

    s = Memory2Store(str(tmp_path / "m.sqlite"), embed=boom)
    res = s.upsert_fact("user", "likes", "tea")
    assert res.action == "inserted"
    assert s.vector_candidates(hash_embed("tea")) == []
    s.close()


def test_entity_resolution(store):
    first = store.upsert_entity("Dr. Sharma", "person")
    assert normalize_name("Dr. Sharma ji") == "sharma"
    assert store.upsert_entity("sharma ji") == first
    priya = store.upsert_entity("Priya", "person")
    assert store.upsert_entity("Priyaa") == priya
    entity = store.get_item(priya, "entities")
    assert "Priyaa" in entity["aliases"]
    assert store.upsert_entity("Rahul") not in (first, priya)
    conn = open_db(":memory:")
    assert resolve_entity(conn, "Ajmer", "place") == resolve_entity(conn, "ajmer")
    with pytest.raises(ValueError):
        resolve_entity(conn, "  ")


def test_events(store):
    eid = store.add_event("Went to the doctor", ts=500.0, entities=["Dr Mehta", "Ajmer"])
    assert store.add_event("went to the doctor", ts=600.0) == eid
    event = store.get_item(eid, "events")
    assert event["entities"] == ["Dr Mehta", "Ajmer"] and event["text"] == "Went to the doctor"
    with pytest.raises(ValueError):
        store.add_event("   ")


def test_list_get_update_validation(store):
    fid = store.upsert_fact("user", "lives_in", "Jaipur").fact_id
    items = store.list_items("facts")
    assert items[0]["text"] == "user lives in Jaipur" and items[0]["inferred"] is False
    assert store.list_items("facts", query="jaipur") and not store.list_items("facts", query="mumbai")
    assert store.list_items("bogus") == [] and store.get_item(fid, "bogus") is None
    assert store.get_item(0, "facts") is None and store.get_item(True, "facts") is None
    assert store.update_item(fid, "facts", {"object_text": "Jaipur city", "pinned": True})
    assert store.get_item(fid, "facts")["pinned"] is True
    assert not store.update_item(fid, "facts", {"object_text": "x" * 201})
    assert not store.update_item(fid, "facts", {"bogus": 1})
    assert not store.update_item(fid, "facts", {"importance": 3})
    assert not store.update_item(fid, "facts", {"summary": "x"})
    assert not store.update_item(999, "facts", {"pinned": True})
    assert not store.update_item(fid, "nope", {"pinned": True})


def test_pin_retract_delete_wipe(store):
    fid = store.upsert_fact("user", "likes", "tea").fact_id
    eid = store.add_event("Bought a laptop", ts=10.0)
    assert store.set_pinned(fid, "facts", True) and store.get_item(fid, "facts")["pinned"]
    assert store.set_status(fid, "facts", "archived") and store.list_items("archived")
    assert not store.set_status(fid, "facts", "weird")
    assert store.set_status(fid, "facts", "active")
    assert store.retract(fid, "facts")
    assert store.get_item(fid, "facts")["status"] == "retracted"
    assert store.vector_candidates(hash_embed("user likes tea"), kinds=("facts",)) == []
    assert store.delete_row(eid, "events") and not store.delete_row(eid, "events")
    store.set_meta("extract_watermark", "9")
    store.wipe_all()
    assert store.stats()["facts"]["total"] == 0 and store.stats()["entities"] == 0
    assert store.get_meta("extract_watermark") == "9"


def test_touch(store):
    fid = store.upsert_fact("user", "likes", "tea").fact_id
    store.touch([("facts", fid), ("facts", 999), ("bogus", 1)], now=5.0)
    row = store.get_item(fid, "facts")
    assert row["use_count"] == 1 and row["last_used_at"] == 5.0


def test_vector_candidates_and_dimension_mismatch(tmp_path):
    def embed(text):
        return hash_embed(text, dim=16) if "legacy" in text else hash_embed(text)

    s = Memory2Store(str(tmp_path / "m.sqlite"), embed=embed)
    near = s.upsert_fact("user", "lives_in", "Jaipur").fact_id
    s.upsert_fact("user", "likes", "legacy vector")
    hits = s.vector_candidates(hash_embed("user lives in Jaipur"))
    assert hits[0][:2] == ("facts", near) and hits[0][2] > 0.9
    assert all(h[1] == near or h[2] < 0.5 for h in hits)
    assert len(s.vector_candidates(hash_embed("legacy vector", dim=16))) == 1
    assert s.vector_candidates(np.zeros(4)) == [] and s.vector_candidates("bad") == []
    s.close()


def test_stats(store):
    store.upsert_fact("user", "lives_in", "Ajmer")
    store.upsert_fact("user", "lives_in", "Jaipur")
    info = store.stats()
    assert info["facts"]["active"] == 1 and info["facts"]["superseded"] == 1
    assert info["schema_version"] == SCHEMA_VERSION and info["db_bytes"] > 0
    assert info["last_extract_ts"] is None


def test_thread_safety(tmp_path):
    store = Memory2Store(str(tmp_path / "t.sqlite"), embed=lambda _t: None)
    errors = []

    def work(i):
        try:
            for j in range(15):
                store.upsert_fact("user", "likes", f"item {i} {j}")
                store.add_event(f"event {i} {j}", ts=float(j))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert store.stats()["facts"]["active"] == 120 and store.stats()["events"]["active"] == 120
    store.close()


def test_rollback_on_error(store):
    with pytest.raises(sqlite3.OperationalError):
        with store.transaction():
            store.upsert_fact("user", "likes", "tea")
            store._conn.execute("SELECT * FROM missing_table")
    assert store.stats()["facts"]["total"] == 0