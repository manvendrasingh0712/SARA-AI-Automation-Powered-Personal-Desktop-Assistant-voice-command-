"""Tests for sara.gui.app.memory2_api and the Memory 2.0 part of export_memory."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE_TS = 1_780_000_000.0


def _load_api_module():
    path = ROOT / "sara" / "gui" / "app" / "memory2_api.py"
    spec = importlib.util.spec_from_file_location("memory2_api_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_mod = _load_api_module()


def _seed(store) -> None:
    rows = (
        ("lives_in", "Ajmer", 0.0),
        ("lives_in", "Jaipur", 100.0),
        ("likes", "cricket", 200.0),
        ("works_at", "Acme", 300.0),
    )
    for number, (predicate, obj, offset) in enumerate(rows, start=1):
        stamp = BASE_TS + offset
        store.upsert_fact(
            "user", predicate, obj, valid_from=stamp, confidence=0.9, importance=0.6,
            source_turn_id=f"t{number}", now=stamp,
        )
    store.add_event(
        "Trip to Goa", BASE_TS + 400, entities=[], importance=0.5,
        source_turn_id="t5", now=BASE_TS + 400,
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    from sara.core.memory2.hashembed import hash_embed
    from sara.core.memory2.store import Memory2Store

    created = Memory2Store(db_path=tmp_path / "mem2.db", embed=lambda text: hash_embed(text))
    _seed(created)
    monkeypatch.setattr("sara.core.memory2.get_store", lambda: created)
    yield created
    created.close()


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr("sara.core.memory2.get_store", lambda: None)


@pytest.fixture
def api():
    return api_mod.ApiMemory2Mixin()


def _items(api, kind="facts", **kwargs):
    result = api.mem2_list(kind, **kwargs)
    assert result["ok"] is True
    return result["data"]["items"]


def _find(api, kind, part):
    return next(item for item in _items(api, kind) if part in item["text"])


def test_list_shape_and_status_split(api, store):
    result = api.mem2_list("facts")
    assert result["ok"] is True and result["data"]["enabled"] is True
    texts = [item["text"] for item in result["data"]["items"]]
    assert len(texts) == 3 and not any("Ajmer" in text for text in texts)
    item = result["data"]["items"][0]
    assert {"id", "kind", "text", "status", "confidence", "importance", "pinned",
            "inferred", "when", "source_turn_id"} <= set(item)
    assert len(_items(api, "events")) == 1
    assert len(_items(api, "entities")) >= 1


def test_list_clamps_and_paging(api, store):
    assert len(_items(api, "facts", limit=0, offset=-5)) == 1
    assert len(_items(api, "facts", limit=10**9, offset=0)) == 3
    assert len(_items(api, "facts", limit="abc", offset="x")) == 3
    first = api.mem2_list("facts", "", 1, 0)["data"]
    assert first["has_more"] is True and first["total"] == 1
    last = api.mem2_list("facts", "", 1, 2)["data"]
    assert last["has_more"] is False and last["total"] == 3
    assert _items(api, "facts", limit=5, offset=10**9) == []


def test_list_query_and_validation(api, store):
    assert [i["text"] for i in _items(api, "facts", query="jaipur")] == ["user lives in Jaipur"]
    assert api.mem2_list("bogus") == {"ok": False, "error": "invalid_kind"}
    assert api.mem2_list("facts", "x" * 81) == {"ok": False, "error": "invalid_query"}
    assert api.mem2_list("facts", 5) == {"ok": False, "error": "invalid_query"}


def test_disabled_shapes(api, off):
    assert api.mem2_list("facts") == {
        "ok": True, "data": {"enabled": False, "items": [], "total": 0, "has_more": False}}
    assert api.mem2_stats() == {"ok": True, "data": {"enabled": False}}
    for result in (
        api.mem2_update(1, "facts", {"importance": 0.5}),
        api.mem2_forget(1, "facts"),
        api.mem2_pin(1, "facts", True),
    ):
        assert result == {"ok": False, "error": "disabled"}


@pytest.mark.parametrize("bad", [0, -3, True, "1", 1.5, None])
def test_invalid_ids(api, store, bad):
    assert api.mem2_forget(bad, "facts") == {"ok": False, "error": "invalid_id"}
    assert api.mem2_pin(bad, "facts", True) == {"ok": False, "error": "invalid_id"}
    assert api.mem2_update(bad, "facts", {"importance": 0.5}) == {"ok": False, "error": "invalid_id"}


def test_invalid_kind_and_patch(api, store):
    assert api.mem2_forget(1, "entities") == {"ok": False, "error": "invalid_kind"}
    assert api.mem2_pin(1, "archived", True) == {"ok": False, "error": "invalid_kind"}
    fact_id = _find(api, "facts", "cricket")["id"]
    for patch in ({}, "text", {"foo": 1}, {"summary": "x"}, {"object_text": "x" * 201},
                  {"object_text": "   "}, {"importance": "high"}, {"pinned": "maybe"}):
        assert api.mem2_update(fact_id, "facts", patch) == {"ok": False, "error": "invalid_patch"}
    assert api.mem2_pin(fact_id, "facts", "maybe") == {"ok": False, "error": "invalid_pinned"}


def test_not_found(api, store):
    assert api.mem2_update(99999, "facts", {"importance": 0.5}) == {"ok": False, "error": "not_found"}
    assert api.mem2_forget(99999, "facts") == {"ok": False, "error": "not_found"}
    assert api.mem2_pin(99999, "facts", True) == {"ok": False, "error": "not_found"}


def test_update_pin_forget_round_trip(api, store):
    fact_id = _find(api, "facts", "cricket")["id"]
    assert api.mem2_update(fact_id, "facts", {"object_text": "football", "importance": 7})["ok"] is True
    assert "football" in store.get_item(fact_id, "facts")["text"]
    assert api.mem2_pin(fact_id, "facts", True)["ok"] is True
    assert store.get_item(fact_id, "facts")["pinned"] is True
    assert api.mem2_forget(fact_id, "facts")["ok"] is True
    assert store.get_item(fact_id, "facts")["status"] == "retracted"
    assert all(item["id"] != fact_id for item in _items(api, "facts"))


def test_update_event_summary(api, store):
    event_id = _find(api, "events", "Goa")["id"]
    assert api.mem2_update(event_id, "events", {"summary": "Trip to Manali"})["ok"] is True
    assert "Manali" in store.get_item(event_id, "events")["text"]


def test_stats(api, store):
    result = api.mem2_stats()
    assert result["ok"] is True and result["data"]["enabled"] is True
    assert result["data"]["facts"]["active"] == 3 and result["data"]["events"]["active"] == 1


def test_store_errors_never_raise(api, monkeypatch):
    class _Broken:
        def list_items(self, *args, **kwargs):
            raise RuntimeError("boom")

        def stats(self):
            raise RuntimeError("boom")

    monkeypatch.setattr("sara.core.memory2.get_store", lambda: _Broken())
    assert api.mem2_list("facts") == {"ok": False, "error": "failed"}
    assert api.mem2_stats() == {"ok": False, "error": "failed"}


def _run_export(monkeypatch, tmp_path):
    try:
        notes = importlib.import_module("sara.gui.app.notes")
    except Exception:
        pytest.skip("sara.gui.app.notes needs GUI dependencies that are not installed")

    class _Inline:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(notes, "app_data_subdir", lambda name: tmp_path)
    monkeypatch.setattr(notes, "_push", lambda *args: None)
    monkeypatch.setattr(notes, "threading", types.SimpleNamespace(Thread=_Inline))
    host = types.SimpleNamespace(db=types.SimpleNamespace(get_recent_messages=lambda limit=500: []))
    assert notes.ApiNotesMixin.export_memory(host)["ok"] is True
    return json.loads((tmp_path / "memory_export.json").read_text(encoding="utf-8"))


def test_export_includes_memory2_key(store, tmp_path, monkeypatch):
    data = _run_export(monkeypatch, tmp_path)
    assert set(data) == {"messages", "memory2"}
    assert data["messages"] == []
    memory2 = data["memory2"]
    assert any(fact["status"] == "superseded" for fact in memory2["facts"])
    assert len(memory2["events"]) == 1 and len(memory2["entities"]) >= 1


def test_export_unchanged_when_disabled(off, tmp_path, monkeypatch):
    assert _run_export(monkeypatch, tmp_path) == []