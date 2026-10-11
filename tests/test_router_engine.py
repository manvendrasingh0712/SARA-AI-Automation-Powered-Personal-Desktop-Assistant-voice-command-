"""Engine wiring: legacy parity when off or broken, shadow logging, on-mode override."""
from __future__ import annotations

import random
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("google.genai")

from config import Config  # noqa: E402
from sara.core.llm import engine  # noqa: E402
from sara.core.router import features, gate  # noqa: E402
from sara.core.router import router as router_mod  # noqa: E402
from sara.core.router.router import Router  # noqa: E402
from sara.core.router.store import RouterStore  # noqa: E402
from sara.core.router.tiers import Tier  # noqa: E402

TIERS = [
    Tier("cloud_fast", "gemini", "g-fast", 1.0),
    Tier("cloud_main", "gemini", "g-main", 2.0),
    Tier("cloud_pro", "gemini", "g-pro", 6.0),
]
CHAT = "what is the capital of france"
SIMPLE = "set volume to 50"


class FakeGemini:
    def __init__(self, fail_first=None) -> None:
        self.models = self
        self.calls = []
        self._fail = fail_first

    def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs["model"])
        if self._fail is not None and len(self.calls) == 1:
            raise RuntimeError(self._fail)
        return iter([SimpleNamespace(text="Hello there. "), SimpleNamespace(text="How can I help?")])


class FakeOllama:
    def __init__(self) -> None:
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs["model"])
        return iter([SimpleNamespace(message=SimpleNamespace(content="hi"))])


@pytest.fixture
def env(tmp_path, monkeypatch):
    routers = []

    def _setup(mode, fail_first=None):
        for key, value in (
            ("ROUTER_MODE", mode), ("ROUTER_EPSILON", 0.0),
            ("ROUTER_MIN_SAMPLES", 1), ("LLM_MAX_RETRIES", 0),
        ):
            monkeypatch.setattr(Config, key, value, raising=False)
        monkeypatch.setattr(features, "_detect_fn", None)
        monkeypatch.setattr(features, "_tool_fn", lambda text: False)
        monkeypatch.setattr(features, "_resolved", True)
        monkeypatch.setattr(gate, "GATE_PATH", tmp_path / "gate.json")
        (tmp_path / "gate.json").write_text('{"passed": true}', encoding="utf-8")
        store = RouterStore(path=str(tmp_path / "r.sqlite"))
        if mode == "on":
            store.add_sample(
                tier="cloud_main", labelled=1, quality=0.7, flags="mode:shadow", decision_id="seed"
            )
        router = Router(store=store, tiers=TIERS, rng=random.Random(0))
        routers.append(router)
        monkeypatch.setattr(router_mod, "_ROUTER", router)
        client = FakeGemini(fail_first)
        monkeypatch.setattr(engine, "_get_gemini_client", lambda cfg: client)
        monkeypatch.setattr(engine, "_get_ollama_client", lambda cfg: None)
        brain = engine.SaraLLM()
        brain._primary_backend = "gemini"
        brain._fallback_enabled = False
        brain._gemini_model_name = "g-main"
        brain._gemini_fast_model_name = "g-fast"
        return brain, client, router, store

    yield _setup
    for router in routers:
        router.shutdown()


def _run(brain, text):
    return list(brain.generate_response_stream(text))


def test_off_uses_legacy_models(env):
    brain, client, _, store = env("off")
    assert _run(brain, CHAT)
    assert _run(brain, SIMPLE)
    assert client.calls == ["g-main", "g-fast"]
    assert store.count() == 0


def test_router_import_failure_leaves_reply_path_unchanged(env, monkeypatch):
    brain, client, _, _ = env("shadow")
    with_router = _run(brain, CHAT)
    monkeypatch.setitem(sys.modules, "sara.core.router", None)
    without_router = _run(brain, CHAT)
    assert with_router and with_router == without_router
    assert client.calls == ["g-main", "g-main"]


def test_shadow_keeps_model_args_and_logs_samples(env):
    brain, client, router, store = env("shadow")
    assert _run(brain, CHAT)
    assert _run(brain, SIMPLE)
    assert client.calls == ["g-main", "g-fast"]
    assert router.drain(5.0)
    assert store.count() == 2
    assert {r["static_tier"] for r in store.recent(5)} == {"cloud_main", "cloud_fast"}


def test_on_override_is_used_for_the_primary_stage(env):
    brain, client, _, _ = env("on")
    assert _run(brain, CHAT)
    assert client.calls == ["g-fast"]


def test_on_cascades_once_and_notes_rate_limit(env):
    brain, client, router, store = env("on", fail_first="429 RESOURCE_EXHAUSTED quota exceeded")
    out = _run(brain, CHAT)
    assert client.calls == ["g-fast", "g-main"]
    assert out and not any(p in engine._STREAM_FAIL_MESSAGES.values() for p in out)
    assert router._cool.active("cloud_fast")
    router.note_user_turn("something different")
    assert router.drain(5.0)
    assert "escalated" in " ".join(str(r["flags"]) for r in store.recent(5))


def test_openers_accept_model_override(env, monkeypatch):
    brain, client, _, _ = env("off")
    brain._open_gemini_stream("tell me a story", [], model_override="zzz")
    assert client.calls[-1] == "zzz"
    brain._open_gemini_stream("tell me a story", [])
    assert client.calls[-1] == "g-main"
    ollama = FakeOllama()
    monkeypatch.setattr(engine, "_get_ollama_client", lambda cfg: ollama)
    brain._ollama_model_name = "o-main"
    brain._ollama_fast_model_name = "o-fast"
    brain._open_ollama_stream("tell me a story", [], model_override="yyy")
    brain._open_ollama_stream("tell me a story", [])
    brain._open_ollama_stream("set volume to 5", [])
    assert ollama.calls == ["yyy", "o-main", "o-fast"]