"""Tests for sara.core.security.events (temp dirs only, stubbed redaction)."""
from __future__ import annotations

import re
import sqlite3
import threading
import time

import pytest

from config import Config
from sara.core.security import events


def _fake_redact(text: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9]+", "[redacted]", text)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "_redact", _fake_redact)
    monkeypatch.setattr(Config, "SECURITY_LOG_RETENTION_DAYS", 90, raising=False)
    db = tmp_path / "security.sqlite"
    events.configure(db)
    yield db
    events.configure(None)


def _insert_old(path, ts: float) -> None:
    conn = events.open_db(path)
    with conn:
        conn.execute(
            "INSERT INTO security_event (ts, turn_id, kind, tool, tier, origin, source, score, details)"
            " VALUES (?, '', 'blocked', '', NULL, '', '', NULL, '')",
            (ts,),
        )
    conn.close()


def _db_count(path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT COUNT(*) FROM security_event").fetchone()[0]
    finally:
        conn.close()


def test_write_and_read(_env):
    events.log_event(
        "blocked", tool="lock_pc", tier=2, origin="llm_tool",
        source="web:x", score=0.9, details="hello", turn_id="t1",
    )
    events.flush(5.0)
    rows = events.get_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] > 0
    assert (row["kind"], row["tool"], row["tier"], row["origin"]) == ("blocked", "lock_pc", 2, "llm_tool")
    assert (row["source"], row["score"], row["details"], row["turn_id"]) == ("web:x", 0.9, "hello", "t1")
    assert set(row) == {"id", "ts", "turn_id", "kind", "tool", "tier", "origin", "source", "score", "details"}
    assert _db_count(_env) == 1


def test_unknown_kind_is_other():
    events.log_event("mystery")
    events.log_event("blocked")
    events.flush(5.0)
    assert [r["kind"] for r in events.get_events(kind="other")] == ["other"]
    assert len(events.get_events(kind="blocked")) == 1


def test_newest_first_and_limit():
    for i in range(10):
        events.log_event("blocked", details=f"d{i}")
    events.flush(5.0)
    rows = events.get_events(limit=3)
    assert [r["details"] for r in rows] == ["d9", "d8", "d7"]


def test_counts():
    for kind in ("blocked", "blocked", "arg_rejected", "injection_detected", "confirmed", "confirmed"):
        events.log_event(kind)
    events.flush(5.0)
    counts = events.get_counts()
    assert counts["total"] == 6 and counts["blocked"] == 4
    assert counts["by_kind"] == {"blocked": 2, "arg_rejected": 1, "injection_detected": 1, "confirmed": 2}


def test_counts_window(_env):
    _insert_old(_env, time.time() - 10 * 86400)
    events.log_event("blocked")
    events.flush(5.0)
    assert events.get_counts(days=7)["total"] == 1
    assert events.get_counts(days=30)["total"] == 2


def test_prune_removes_old_rows(_env, monkeypatch):
    monkeypatch.setattr(Config, "SECURITY_LOG_RETENTION_DAYS", 30, raising=False)
    _insert_old(_env, time.time() - 200 * 86400)
    events.log_event("blocked")
    events.flush(5.0)
    assert _db_count(_env) == 1
    assert events.get_counts(days=3650)["total"] == 1


def test_queue_full_drops_and_ring_keeps(tmp_path, monkeypatch):
    store = events.EventStore(path_fn=lambda: tmp_path / "q.sqlite", queue_max=2)
    monkeypatch.setattr(store, "ensure_thread", lambda: None)
    monkeypatch.setattr(events, "_STORE", store)
    for i in range(5):
        events.log_event("blocked", details=f"d{i}")
    assert store.dropped == 3
    assert len(events.get_events(limit=50)) == 5
    assert events.get_counts()["total"] == 5


def test_db_failure_falls_back_to_ring(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    events.configure(blocker / "sec.sqlite")
    for i in range(3):
        events.log_event("blocked", details=f"d{i}")
    events.flush(5.0)
    assert events._STORE.ok is False
    assert len(events.get_events()) == 3
    counts = events.get_counts()
    assert counts["total"] == 3 and counts["blocked"] == 3


def test_details_redacted_and_truncated():
    events.log_event("redacted", details="key sk-ABCDEF123456 " + "x" * 1000)
    events.log_event("redacted", details="a\nb\x00c")
    events.log_event("redacted", tool="t" * 500, origin="o" * 500, source="s" * 500)
    events.flush(5.0)
    rows = events.get_events(limit=10)
    long_row = next(r for r in rows if r["details"].startswith("key"))
    assert len(long_row["details"]) <= 300
    assert "sk-ABC" not in long_row["details"] and "[redacted]" in long_row["details"]
    ctrl_row = next(r for r in rows if r["details"].startswith("a"))
    assert "\n" not in ctrl_row["details"] and "\x00" not in ctrl_row["details"]
    cut_row = next(r for r in rows if r["tool"])
    assert len(cut_row["tool"]) <= 80 and len(cut_row["origin"]) <= 40 and len(cut_row["source"]) <= 120


def test_details_dropped_when_redact_missing(monkeypatch):
    monkeypatch.setattr(events, "_redact", None)
    events.log_event("blocked", details="secret sk-ABCDEF123456")
    events.flush(5.0)
    assert events.get_events()[0]["details"] == ""


def test_log_event_never_raises_on_bad_values():
    events.log_event("blocked", tier="x", score="y", tool=None, details=object())  # type: ignore[arg-type]
    events.flush(5.0)
    row = events.get_events()[0]
    assert row["tier"] is None and row["score"] is None


def test_thread_safety():
    def worker() -> None:
        for i in range(25):
            events.log_event("denied_by_user", tool="lock_pc", details=f"d{i}")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events.flush(10.0)
    assert events._STORE.dropped == 0
    assert events.get_counts()["by_kind"] == {"denied_by_user": 200}
    assert len(events.get_events(limit=500)) == 200


def test_configure_resets_state_and_db_persists(_env, tmp_path):
    events.log_event("blocked")
    events.flush(5.0)
    events.configure(tmp_path / "other.sqlite")
    assert events.get_events() == []
    events.configure(_env)
    assert len(events.get_events()) == 1


def test_flush_with_nothing_pending_returns_quickly():
    start = time.perf_counter()
    events.flush(2.0)
    assert time.perf_counter() - start < 0.5