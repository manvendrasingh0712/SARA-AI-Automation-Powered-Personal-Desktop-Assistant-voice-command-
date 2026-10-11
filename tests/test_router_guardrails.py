"""Router guardrails: modes, gate, floors, exploration, cascade, cool-down, revert, reset."""
from __future__ import annotations

import json
import random

import pytest

from config import Config
from sara.core.router import features, gate
from sara.core.router import router as router_mod
from sara.core.router.cooldown import Cooldowns
from sara.core.router.features import FEATURE_DIM, extract_features
from sara.core.router.policy import HIGH_COMPLEXITY
from sara.core.router.router import Router
from sara.core.router.store import RouterStore
from sara.core.router.tiers import Tier

GEMINI = [
    Tier("cloud_fast", "gemini", "f", 1.0),
    Tier("cloud_main", "gemini", "m", 2.0),
    Tier("cloud_pro", "gemini", "p", 6.0),
]
OLLAMA = [Tier("local_fast", "ollama", "lf", 0.0), Tier("local_main", "ollama", "lm", 0.5)]
CHAT = "what is the capital of france"
COMPLEX = "compare these two plans and then explain why one is better " + " ".join(["detail"] * 30)


class Clock:
    def __init__(self) -> None:
        self.t = 1_700_000_000.0

    def __call__(self) -> float:
        return self.t


def _ready(tmp_path, store) -> None:
    gate_file = tmp_path / "gate.json"
    gate_file.write_text(json.dumps({"passed": True, "message": "ok", "ts": 1.0}), encoding="utf-8")
    for i in range(3):
        store.add_sample(
            tier="cloud_main", labelled=1, quality=0.7, flags="mode:shadow", decision_id=f"seed{i}"
        )


@pytest.fixture
def make(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(features, "_detect_fn", None)
    monkeypatch.setattr(features, "_tool_fn", lambda text: False)
    monkeypatch.setattr(features, "_resolved", True)
    monkeypatch.setattr(gate, "GATE_PATH", tmp_path / "gate.json")
    monkeypatch.setattr(Config, "ROUTER_MIN_SAMPLES", 3, raising=False)
    monkeypatch.setattr(Config, "ROUTER_COOLDOWN_BASE_S", 30.0, raising=False)

    def _make(mode="shadow", tiers=None, eps=0.0, ready=False, clock=None):
        monkeypatch.setattr(Config, "ROUTER_MODE", mode, raising=False)
        monkeypatch.setattr(Config, "ROUTER_EPSILON", eps, raising=False)
        clk = clock or Clock()
        store = RouterStore(path=str(tmp_path / f"r{len(made)}.sqlite"), clock=clk)
        router = Router(
            store=store, tiers=tiers or GEMINI, clock=clk, rng=random.Random(1)
        )
        made.append(router)
        if ready:
            _ready(tmp_path, store)
        return router, store

    yield _make
    for router in made:
        router.shutdown()


def test_off_logs_nothing(make):
    router, store = make(mode="off")
    choice = router.choose(CHAT)
    assert choice.tier is None and choice.applied is False and choice.decision_id == ""
    router.note_user_turn(CHAT)
    router.drain()
    assert store.count() == 0
    assert router.active() is False


def test_shadow_logs_learner_next_to_static(make):
    router, store = make()
    choice = router.choose(CHAT)
    assert choice.applied is False and choice.mode == "shadow"
    assert choice.static_tier == "cloud_main" and choice.propensity == 1.0
    assert choice.decision_id
    assert router.model_for(choice, "gemini") is None
    assert router.choose("set volume to 50").static_tier == "cloud_fast"
    router.drain()
    rows = store.recent(5)
    row = [r for r in rows if r["decision_id"] == choice.decision_id][0]
    assert row["static_tier"] == "cloud_main" and row["tier"] == "cloud_main"
    assert "mode:shadow" in row["flags"]
    assert len(json.loads(row["x_json"])) == FEATURE_DIM
    assert CHAT not in str(row)


def test_on_is_refused_without_gate_and_samples(make):
    router, _ = make()
    ok, message = router.set_mode("on")
    assert ok is False and "benchmark" in message and "labelled" in message
    assert Config.ROUTER_MODE == "shadow"
    assert router.set_mode("turbo")[0] is False


def test_on_behaves_like_shadow_until_gate_passes(make):
    router, _ = make(mode="on")
    choice = router.choose(CHAT)
    assert choice.mode == "shadow" and choice.applied is False
    assert choice.reason.startswith("gate")


def test_on_applies_once_ready(make):
    router, _ = make(ready=True)
    assert router.set_mode("on")[0] is True
    choice = router.choose(CHAT)
    assert choice.applied is True and choice.mode == "on"
    assert router.model_for(choice, "gemini") == choice.tier.model
    assert router.model_for(choice, "ollama") is None


def test_never_cheaper_for_tools(make, monkeypatch):
    router, _ = make(mode="on", eps=1.0, ready=True)
    monkeypatch.setattr(features, "_tool_fn", lambda text: True)
    for i in range(50):
        choice = router.choose(f"{CHAT} {i}")
        assert choice.applied and choice.tier.name != "cloud_fast"


def test_never_cheaper_for_high_complexity(make):
    assert extract_features(COMPLEX).complexity >= HIGH_COMPLEXITY
    router, _ = make(mode="on", eps=1.0, ready=True)
    for i in range(50):
        assert router.choose(f"{COMPLEX} {i}").tier.name != "cloud_fast"


def test_hindi_floor_on_local_family(make):
    router, _ = make(mode="on", tiers=OLLAMA, eps=1.0, ready=True)
    for _ in range(30):
        assert router.choose("मुझे बताओ क्या है").tier.name == "local_main"


def test_epsilon_explores_adjacent_tier_only(make):
    router, _ = make(mode="on", eps=1.0, ready=True)
    for _ in range(50):
        choice = router.choose(CHAT)
        assert choice.explore is True and choice.tier.name == "cloud_main"
        assert choice.propensity == pytest.approx(1.0)


def test_no_exploration_when_epsilon_zero(make):
    router, _ = make(mode="on", eps=0.0, ready=True)
    choice = router.choose(CHAT)
    assert choice.explore is False and choice.propensity == 1.0


def test_cascade_is_one_step_and_respects_cooldown(make, monkeypatch):
    clock = Clock()
    router, _ = make(mode="on", ready=True, clock=clock)
    choice = router.choose(CHAT)
    assert choice.tier.name == "cloud_fast"
    assert router.cascade(choice).name == "cloud_main"
    router.note_rate_limited("cloud_main")
    assert router.cascade(choice) is None
    clock.t += 31
    assert router.cascade(choice).name == "cloud_main"
    monkeypatch.setattr(Config, "ROUTER_MODE", "shadow")
    assert router.cascade(choice) is None


def test_cooldown_grows_expires_and_resets():
    clock = Clock()
    cool = Cooldowns(clock, lambda: 30.0)
    assert [cool.note("a") for _ in range(3)] == [30.0, 60.0, 120.0]
    assert cool.active("a") and cool.remaining("a") == pytest.approx(120.0)
    clock.t += 121
    assert not cool.active("a")
    cool.success("a")
    assert cool.note("a") == 30.0
    last = 0.0
    for _ in range(20):
        last = cool.note("b")
    assert last == 3600.0
    cool.clear()
    assert not cool.active("a") and not cool.active("b")


def test_auto_revert_to_shadow(make):
    router, store = make(ready=True)
    assert router.set_mode("on")[0] is True
    for i in range(50):
        store.add_sample(
            tier="cloud_main", labelled=1, quality=0.2, flags="mode:on,applied", decision_id=f"a{i}"
        )
    for i in range(10):
        store.add_sample(
            tier="cloud_main", labelled=1, quality=0.8, flags="mode:shadow", decision_id=f"b{i}"
        )
    choice = router.choose(CHAT)
    router.report_done(choice.decision_id, ok=True, latency_ms=300.0)
    router.note_user_turn("something completely different")
    assert router.drain(5.0)
    assert Config.ROUTER_MODE == "shadow"
    assert router.stats()["reverted"] is True
    assert router.set_mode("shadow")[0] is True
    assert router.stats()["reverted"] is False


def test_learns_from_outcome(make):
    router, store = make()
    choice = router.choose(CHAT)
    router.report_done(choice.decision_id, ok=True, latency_ms=400.0)
    router.note_user_turn("play some jazz")
    assert router.drain(5.0)
    row = store.recent(1)[0]
    assert row["labelled"] == 1 and row["quality"] == pytest.approx(0.7)
    assert router._learn.get().counts["cloud_main"] == 1


def test_rephrase_lowers_quality(make):
    router, store = make()
    choice = router.choose(CHAT)
    router.report_done(choice.decision_id, ok=True, latency_ms=400.0)
    router.note_user_turn(CHAT + "?")
    assert router.drain(5.0)
    row = store.recent(1)[0]
    assert row["quality"] == pytest.approx(0.3) and "rephrased" in row["flags"]


def test_empty_reply_lowers_quality(make):
    router, store = make()
    choice = router.choose(CHAT)
    router.report_done(choice.decision_id, ok=False, latency_ms=400.0, empty=True)
    router.note_user_turn("play some jazz")
    assert router.drain(5.0)
    assert store.recent(1)[0]["quality"] == pytest.approx(0.1)


def test_feedback_pending_and_late(make):
    router, store = make()
    choice = router.choose(CHAT)
    router.bind_turn(choice.decision_id, "t1")
    assert router.feedback("t1", -1) is True
    router.report_done(choice.decision_id, ok=True, latency_ms=400.0)
    router.note_user_turn("zzz top")
    assert router.drain(5.0)
    assert store.get_by_turn("t1")["quality"] == pytest.approx(0.0, abs=1e-9)
    assert router.feedback("nope", 1) is False
    assert router.feedback("t1", 0) is False
    assert router.feedback("t1", 1) is True
    assert router.drain(5.0)
    assert store.get_by_turn("t1")["quality"] == pytest.approx(1.0)


def test_stats_shape_and_savings(make):
    router, _ = make()
    router.choose("set volume to 50")
    router.drain()
    stats = router.stats()
    for key in (
        "mode", "tiers", "samples", "labelled", "usage", "avg_latency_ms",
        "calls_saved_vs_main", "shown", "gate", "reverted",
    ):
        assert key in stats
    assert stats["usage"] == {"cloud_fast": 1}
    assert stats["calls_saved_vs_main"] == 1
    assert stats["shown"] is False
    assert stats["gate"]["ok"] is False


def test_reset_forgets_everything(make):
    router, store = make()
    choice = router.choose(CHAT)
    router.report_done(choice.decision_id, ok=True, latency_ms=100.0)
    router.note_user_turn("zzz")
    router.drain()
    assert store.count() == 1
    router.reset()
    assert store.count() == 0
    assert router.stats()["reverted"] is False


def test_single_tier_is_a_noop(make):
    router, store = make(tiers=[GEMINI[1]])
    assert router.active() is False
    choice = router.choose(CHAT)
    assert choice.tier is None and choice.applied is False and choice.decision_id == ""
    router.drain()
    assert store.count() == 0


def test_choose_never_raises(make, monkeypatch):
    router, _ = make()

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(router_mod, "extract_features", boom)
    choice = router.choose(CHAT)
    assert choice.tier is None and choice.applied is False