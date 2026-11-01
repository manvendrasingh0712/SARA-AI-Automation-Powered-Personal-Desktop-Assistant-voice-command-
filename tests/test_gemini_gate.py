"""Part 3 / P15: quota gate, breaker, failover, backoff, cancel, coalescing."""
import threading
import time

import pytest

from sara.core.llm.gemini_gate import GeminiGate


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_rpm_bucket_refills_after_a_minute():
    c = Clock()
    g = GeminiGate(rpm=3, rpd=0, clock=c)
    for _ in range(3):
        assert g.allow("m")
        g.note_request("m")
    assert not g.allow("m")
    c.t += 61
    assert g.allow("m")


def test_rpd_cap_blocks_until_the_day_changes():
    day = [1_700_000_000.0]
    g = GeminiGate(rpm=0, rpd=2, wall=lambda: day[0])
    g.note_request("m")
    g.note_request("m")
    assert not g.allow("m")
    day[0] += 86400 * 2
    assert g.allow("m")
    assert g.usage()["m"][0] == 0


def test_breaker_trips_after_consecutive_failures_and_recovers():
    c = Clock()
    g = GeminiGate(fail_threshold=3, cooldown_s=30, clock=c)
    g.note_result("m", False)
    g.note_result("m", False)
    assert g.allow("m")
    g.note_result("m", False)
    assert not g.allow("m")
    c.t += 31
    assert g.allow("m")
    g.note_result("m", True)
    g.note_result("m", False)
    assert g.allow("m")  # the success reset the streak


def test_retry_after_and_hard_block_apply_at_once():
    c = Clock()
    g = GeminiGate(clock=c)
    g.note_result("m", False, retry_after_s=20)
    assert not g.allow("m")
    c.t += 21
    assert g.allow("m")
    g.note_result("m", False, retry_after_s=7200, hard=True)
    c.t += 3600
    assert not g.allow("m")


def test_failover_chain_and_all_blocked():
    g = GeminiGate()
    g.note_result("pro", False, retry_after_s=60)
    assert g.choose("pro", ["flash", "lite"]) == "flash"
    g.note_result("flash", False, retry_after_s=60)
    assert g.choose("pro", ["flash", "lite"]) == "lite"
    g.note_result("lite", False, retry_after_s=60)
    assert g.choose("pro", ["flash", "lite"]) is None


def test_429_storm_falls_back_in_one_call_not_after_retries():
    """After the breaker opens, 200 further turns cost zero Gemini calls."""
    g = GeminiGate(fail_threshold=3, cooldown_s=60)
    calls = 0
    for _ in range(203):
        if g.choose("m") is None:
            continue
        calls += 1
        g.note_request("m")
        g.note_result("m", False)
    assert calls == 3


def test_backoff_is_jittered_capped_and_honours_retry_after():
    vals = {GeminiGate.backoff(3, base=1.0, cap=8.0, rng=lambda r=r: r) for r in (0.0, 0.25, 0.5, 1.0)}
    assert len(vals) == 4 and max(vals) <= 8.0 and min(vals) >= 0.0
    assert GeminiGate.backoff(0, retry_after_s=2.5) == 2.5
    assert GeminiGate.backoff(0, cap=8.0, retry_after_s=500) == 8.0


def test_sleep_is_interrupted_by_cancel():
    ev = threading.Event()
    threading.Timer(0.15, ev.set).start()
    t0 = time.monotonic()
    assert GeminiGate.interruptible_sleep(5.0, ev) is False
    assert time.monotonic() - t0 < 1.0
    assert GeminiGate.interruptible_sleep(0.05, threading.Event()) is True


def test_identical_inflight_requests_share_one_call():
    g = GeminiGate()
    n = []
    gate = threading.Event()

    def slow():
        n.append(1)
        gate.wait(2)
        return "answer"

    out = []
    ts = [threading.Thread(target=lambda: out.append(g.coalesce("k", slow))) for _ in range(5)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ts:
        t.join(3)
    assert out == ["answer"] * 5 and len(n) == 1
    assert g.coalesce("k", lambda: "fresh") == "fresh"  # finished flights are not cached


def test_coalesced_error_reaches_every_waiter():
    g = GeminiGate()
    gate = threading.Event()

    def boom():
        gate.wait(2)
        raise RuntimeError("429")

    errs = []

    def run():
        try:
            g.coalesce("k", boom)
        except RuntimeError as e:
            errs.append(str(e))

    ts = [threading.Thread(target=run) for _ in range(3)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ts:
        t.join(3)
    assert errs == ["429"] * 3


def test_meter_counts_requests():
    g = GeminiGate(rpd=500)
    for _ in range(3):
        g.note_request("m")
    assert g.usage() == {"m": (3, 500)}


def test_engine_open_stream_uses_the_gate_and_falls_back_when_closed(monkeypatch):
    from sara.core.llm import engine as E

    b = E.SaraLLM.__new__(E.SaraLLM)
    b._gate = GeminiGate()
    b._gemini_model_name = b._gemini_fast_model_name = "m"
    b._gate.note_result("m", False, retry_after_s=60)
    monkeypatch.setattr(E, "_get_gemini_client", lambda cfg: object())
    b._cfg = object()
    b._secure_reference = lambda r: r
    b._build_contents_gemini = lambda *a, **k: []
    import types as _t, sys

    fake = _t.ModuleType("google.genai")
    fake.types = object()
    monkeypatch.setitem(sys.modules, "google", _t.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    with pytest.raises(E._ClientUnavailableError):
        b._open_gemini_stream("hello", [])
