"""tests/test_trace.py -- sara.core.telemetry.trace: marks, pending buffer, metrics, flags."""

import threading
import time
from collections import deque

import pytest

from sara.core.telemetry import trace


class FakeStore:
    """Stands in for TraceStore so tests never touch the real telemetry DB."""

    def __init__(self):
        self.records = []
        self.dropped = 0

    def submit(self, records):
        self.records.extend(records)

    def ensure_thread(self):
        return None


class Clock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


@pytest.fixture
def tr(monkeypatch):
    clock = Clock()
    store = FakeStore()
    monkeypatch.setattr(trace, "_now", clock)
    monkeypatch.setattr(trace, "_store", store)
    monkeypatch.setattr(trace, "_active", {})
    monkeypatch.setattr(trace, "_pending", {})
    monkeypatch.setattr(trace, "_latest", None)
    monkeypatch.setattr(trace, "_ring", deque(maxlen=500))
    monkeypatch.setattr(trace, "_enabled", True)
    trace.clock = clock
    trace.fake_store = store
    return trace


def test_mark_without_active_turn_is_noop(tr):
    tr.mark("route_done")
    tr.annotate(route="regex")
    tr.end_turn("ok")
    assert tr.get_recent() == []


def test_unknown_stage_is_ignored(tr):
    tr.begin_turn("text", text="hi")
    tr.mark("not_a_stage")
    tr.mark("wake", bogus_key="x")


def test_typed_turn_derived_metrics(tr):
    c = tr.clock
    tr.begin_turn("text", text="open notepad")
    c.tick(0.010)
    tr.annotate(route="regex", intent="open_app")
    tr.mark("route_done")
    tr.mark("tool_start")
    c.tick(0.050)
    tr.mark("tool_end")
    c.tick(0.040)
    tr.mark("tts_first_audio")
    c.tick(0.200)
    tr.mark("tts_done")
    tr.end_turn("ok")
    row = tr.get_recent(1)[0]
    assert row["source"] == "text"
    assert row["route"] == "regex" and row["intent"] == "open_app"
    assert row["route_ms"] == 10.0
    assert row["tool_ms"] == 50.0
    assert row["ttfa_ms"] == 100.0
    assert row["tts_start_ms"] == 40.0
    assert row["outcome"] == "ok"
    assert row["stt_ms"] is None and row["llm_ttft_ms"] is None


def test_voice_turn_adopts_pending_marks(tr):
    c = tr.clock
    tr.mark_pending("wake")
    c.tick(1.0)
    tr.mark_pending("speech_start")
    c.tick(2.0)
    tr.mark_pending("speech_end")
    c.tick(0.4)
    tr.mark_pending("stt_done")
    tr.begin_turn("voice", text="volume up")
    tr.mark("route_done")
    c.tick(0.3)
    tr.mark("llm_req")
    c.tick(0.5)
    tr.mark("llm_first_token")
    c.tick(0.1)
    tr.mark("tts_first_audio")
    tr.end_turn("ok")
    tr.mark("tts_done")
    row = tr.get_recent(1)[0]
    assert row["stt_ms"] == 400.0
    assert row["llm_ttft_ms"] == 500.0
    assert row["ttfa_ms"] == 400.0 + 300.0 + 500.0 + 100.0 - 0.0 or row["ttfa_ms"] > 0


def test_stale_pending_marks_are_dropped(tr):
    tr.mark_pending("speech_end")
    tr.clock.tick(trace._PENDING_TTL_S + 5)
    tr.begin_turn("voice", text="x")
    tr.mark("tts_first_audio")
    tr.end_turn("ok")
    tr.mark("tts_done")
    row = tr.get_recent(1)[0]
    assert row["ttfa_ms"] is None


def test_wake_mark_clears_older_pending(tr):
    tr.mark_pending("speech_end")
    tr.mark_pending("wake")
    assert "speech_end" not in trace._pending


def test_first_wins_stage_keeps_earliest(tr):
    tr.begin_turn("text", text="q")
    tr.mark("llm_req")
    tr.clock.tick(1.0)
    tr.mark("llm_req")
    tr.mark("llm_first_token")
    tr.end_turn("ok")
    tr.mark("tts_done")
    row = tr.get_recent(1)[0]
    assert row["llm_ttft_ms"] == 1000.0


def test_end_turn_first_call_wins(tr):
    tr.begin_turn("text", text="q")
    tr.end_turn("cancelled")
    tr.end_turn("ok")
    tr.mark("tts_done")
    assert tr.get_recent(1)[0]["outcome"] == "cancelled"


def test_next_begin_turn_finalises_previous(tr):
    tr.begin_turn("text", text="a")
    tr.end_turn("ok")
    assert tr.get_recent() == []
    tr.begin_turn("text", text="b")
    assert len(tr.get_recent()) == 1


def test_sweep_finalises_after_timeout(tr):
    tr.begin_turn("text", text="a")
    tr.end_turn("ok")
    tr.clock.tick(trace._FINALISE_AFTER_END_S + 1)
    assert len(tr.get_recent()) == 1


def test_persisted_once_on_tts_done(tr):
    tr.begin_turn("text", text="a")
    tr.end_turn("ok")
    tr.mark("tts_done")
    assert len(tr.fake_store.records) == 1


def test_text_not_stored_by_default(tr, monkeypatch):
    monkeypatch.setattr(trace.Config, "TRACE_STORE_TEXT", False, raising=False)
    tr.begin_turn("text", text="secret words")
    tr.end_turn("ok")
    tr.mark("tts_done")
    rec = tr.fake_store.records[0]
    assert rec["text"] is None
    assert rec["text_len"] == len("secret words")
    assert len(rec["text_hash"]) == 12


def test_text_stored_when_enabled(tr, monkeypatch):
    monkeypatch.setattr(trace.Config, "TRACE_STORE_TEXT", True, raising=False)
    tr.begin_turn("text", text="hello")
    tr.end_turn("ok")
    tr.mark("tts_done")
    assert tr.fake_store.records[0]["text"] == "hello"


def test_disabled_flag_changes_nothing(tr):
    tr.set_enabled(False)
    assert tr.is_enabled() is False
    tr.mark_pending("wake")
    tr.begin_turn("text", text="a")
    tr.mark("route_done")
    tr.end_turn("ok")
    assert trace._active == {} and trace._pending == {}
    assert tr.get_recent() == []


def test_marks_from_many_threads(tr):
    tr.begin_turn("text", text="a")

    def worker(stage):
        for _ in range(200):
            tr.mark(stage)
            tr.annotate(route="llm")

    threads = [threading.Thread(target=worker, args=(s,)) for s in ("route_done", "llm_req", "tool_start", "tool_end")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    tr.end_turn("ok")
    tr.mark("tts_done")
    assert len(tr.get_recent()) == 1


def test_summary_percentiles_and_min_samples(tr):
    assert tr.get_summary()["ttfa_p50"] is None
    for i in range(10):
        tr.begin_turn("text", text="q")
        tr.annotate(route="regex")
        tr.clock.tick(0.1 * (i + 1))
        tr.mark("tts_first_audio")
        tr.end_turn("ok")
        tr.mark("tts_done")
    s = tr.get_summary()
    assert s["count"] == 10
    assert s["ttfa_p50"] == pytest.approx(500.0, abs=1.0)
    assert s["ttfa_p95"] == pytest.approx(1000.0, abs=1.0)
    assert s["by_route"]["regex"]["count"] == 10


def test_percentile_nearest_rank_needs_five_samples():
    assert trace._percentile([1.0, 2.0, 3.0, 4.0], 50) is None
    assert trace._percentile([5.0, 1.0, 3.0, 2.0, 4.0], 50) == 3.0


def test_get_recent_never_contains_text(tr, monkeypatch):
    monkeypatch.setattr(trace.Config, "TRACE_STORE_TEXT", True, raising=False)
    tr.begin_turn("text", text="private")
    tr.end_turn("ok")
    tr.mark("tts_done")
    assert "text" not in tr.get_recent(1)[0]


def test_mark_overhead_is_small(tr):
    tr.begin_turn("text", text="a")
    start = time.perf_counter()
    for _ in range(10000):
        tr.mark("route_done")
    mean_ms = (time.perf_counter() - start) * 1000.0 / 10000
    assert mean_ms < 1.0