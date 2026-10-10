"""End-to-end: real PreferencesDB + fake brain, contradiction becomes superseded history."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from config import Config
from sara.core.memory import PreferencesDB
from sara.core.memory2 import extract, guard
from sara.core.memory2.hashembed import hash_embed
from sara.core.memory2.store import Memory2Store

T1, T2 = "2026-10-01T10:00:00", "2026-10-05T09:00:00"


class MatchingBrain:
    """Returns a lives_in fact for every numbered utterance that mentions a known city."""

    CITIES = (("I live in Ajmer", "Ajmer"), ("I moved to Jaipur", "Jaipur"))

    def __init__(self):
        self.prompts = []

    def is_usable(self):
        return True

    def generate_response(self, prompt):
        self.prompts.append(prompt)
        facts = []
        for line in prompt.splitlines():
            for needle, city in self.CITIES:
                if needle in line and line.startswith("["):
                    facts.append({"turn": int(line[1:line.index("]")]), "subject": "user",
                                  "predicate": "lives_in", "object": city, "confidence": 0.9})
        return json.dumps({"entities": [], "facts": facts, "events": []})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "_scan", lambda t: SimpleNamespace(flagged=False))
    monkeypatch.setattr(guard, "_redact", lambda t: t)
    monkeypatch.setattr(guard, "_contains_secret", lambda t: False)
    monkeypatch.setattr(Config, "MEMORY2_MAX_TURNS_PER_EXTRACT", 40, raising=False)
    db = PreferencesDB(str(tmp_path / "prefs.db"))
    store = Memory2Store(str(tmp_path / "m.sqlite"), embed=hash_embed)
    yield db, store, tmp_path / "prefs.db"
    store.close()
    db.close()


def log(path, role, text, ts):
    conn = sqlite3.connect(str(path))
    conn.execute("INSERT INTO conversation_log (role, message, timestamp) VALUES (?, ?, ?)", (role, text, ts))
    conn.commit()
    conn.close()


def states(store):
    return {r["object_text"]: r for r in store.iter_rows("facts", ("active", "superseded"))}


def test_contradiction_in_one_batch(env):
    db, store, path = env
    log(path, "user", "I live in Ajmer", T1)
    log(path, "assistant", "Nice city!", T1)
    log(path, "user", "I moved to Jaipur", T2)
    res = extract.run_extraction_pass(store, db, MatchingBrain())
    rows = states(store)
    assert res.ok and res.turns == 2 and res.facts == 2
    assert rows["Ajmer"]["status"] == "superseded" and rows["Jaipur"]["status"] == "active"
    assert rows["Ajmer"]["valid_to"] == rows["Jaipur"]["valid_from"] == datetime.fromisoformat(T2).timestamp()
    assert rows["Jaipur"]["source_turn_id"] == "c3" and store.get_meta("extract_watermark") == "3"
    assert extract.run_extraction_pass(store, db, MatchingBrain()).reason == "no_new_turns"


def test_contradiction_across_two_batches(env):
    db, store, path = env
    brain = MatchingBrain()
    log(path, "user", "I live in Ajmer", T1)
    extract.run_extraction_pass(store, db, brain)
    assert states(store)["Ajmer"]["status"] == "active"
    log(path, "user", "I moved to Jaipur", T2)
    extract.run_extraction_pass(store, db, brain)
    rows = states(store)
    assert rows["Ajmer"]["status"] == "superseded" and rows["Jaipur"]["status"] == "active"
    assert len(brain.prompts) == 2 and "Ajmer" not in brain.prompts[1]