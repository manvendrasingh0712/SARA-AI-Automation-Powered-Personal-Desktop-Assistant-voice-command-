"""
A daily-quota 429 must stop SARA from trying Gemini until the quota resets,
and the tool router must answer from local Ollama meanwhile.

THE OBSERVED WASTE (E2E user test, 2026-10-29)
    Gemini free tier: 20 requests/day, retryDelay ~13 h. The chat breaker
    only tripped after 3 failures and re-probed every 15 min, so a doomed
    Gemini round-trip kept landing on real turns. The tool router had no
    Ollama path at all: a quota error dropped "open chrome for me" to the
    keyword heuristic, which answered "unknown".
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from sara.core import tool_router
from sara.core.llm import provider_runtime as pr
from sara.core.llm.engine import SaraLLM

_QUOTA_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded "
    "your current quota', 'details': [{'@type': "
    "'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '48871s'}]}}"
)


class _QuotaErr(Exception):
    code = 429


def _brain(threshold: int = 3) -> SaraLLM:
    b = SaraLLM.__new__(SaraLLM)
    b._cfg = SimpleNamespace(LLM_CIRCUIT_BREAKER_THRESHOLD=threshold)
    b._cb_lock = threading.Lock()
    b._cb_consecutive_failures = 0
    b._cb_cooldown_until = 0.0
    return b


# ── SaraLLM breaker ─────────────────────────────────────────────────────


def test_trip_for_opens_the_breaker_immediately() -> None:
    b = _brain()
    b.trip_primary_for(48871)
    assert b.primary_breaker_active()
    assert b._cb_cooldown_until - time.monotonic() > 48000


def test_trip_for_never_shortens_a_longer_trip() -> None:
    b = _brain()
    b.trip_primary_for(5000)
    until = b._cb_cooldown_until
    b.trip_primary_for(10)
    assert b._cb_cooldown_until == until


def test_trip_for_is_capped_at_one_day() -> None:
    b = _brain()
    b.trip_primary_for(10**9)
    assert b._cb_cooldown_until - time.monotonic() <= 86400.0


@pytest.mark.parametrize("junk", [0, -5, None, "soon"])
def test_trip_for_ignores_junk(junk) -> None:
    b = _brain()
    b.trip_primary_for(junk)
    assert not b.primary_breaker_active()


def test_a_success_still_clears_a_quota_trip() -> None:
    b = _brain()
    b.trip_primary_for(48871)
    b.note_primary_result(True)
    assert not b.primary_breaker_active()


def test_stream_loop_trips_breaker_on_long_server_delay() -> None:
    import inspect

    src = inspect.getsource(SaraLLM)
    window = src[src.index("if asked is not None and asked > max_delay:"):]
    window = window[: window.index("if attempt <")]
    assert "_circuit_breaker_trip_for(asked)" in window


# ── short generators (reminders / proactive) ────────────────────────────


class _TripBreaker:
    def __init__(self):
        self.records, self.trips = [], []

    def active(self):
        return False

    def record(self, ok):
        self.records.append(ok)

    def trip_for(self, seconds):
        self.trips.append(seconds)


def test_generate_short_trips_the_shared_breaker_on_quota() -> None:
    err = _QuotaErr(_QUOTA_429)
    b = _TripBreaker()
    cfg = SimpleNamespace(LLM_BACKEND="gemini", LLM_FALLBACK_ENABLED=True)
    with mock.patch.dict(
        pr._CALLS,
        {"gemini": mock.Mock(side_effect=err), "ollama": mock.Mock(return_value="ok")},
    ):
        r = pr.generate_short("p", "s", cfg, breaker=b)
    assert r.provider == "ollama"
    assert b.trips == [48871.0]


def test_generate_short_accepts_a_breaker_without_trip_for() -> None:
    class _Old:
        def active(self):
            return False

        def record(self, ok):
            pass

    cfg = SimpleNamespace(LLM_BACKEND="gemini", LLM_FALLBACK_ENABLED=True)
    with mock.patch.dict(
        pr._CALLS,
        {"gemini": mock.Mock(side_effect=_QuotaErr(_QUOTA_429)),
         "ollama": mock.Mock(return_value="ok")},
    ):
        assert pr.generate_short("p", "s", cfg, breaker=_Old()).text == "ok"


# ── tool router ─────────────────────────────────────────────────────────


@pytest.fixture
def fresh_router(monkeypatch):
    monkeypatch.setattr(tool_router, "_gemini_blocked_until", 0.0)
    return tool_router


def _patch_clients(monkeypatch, gemini_client, ollama_client):
    from sara.core.llm import clients

    monkeypatch.setattr(clients, "_get_gemini_client", lambda cfg: gemini_client)
    monkeypatch.setattr(clients, "_get_ollama_client", lambda cfg: ollama_client)


def _ollama_answering(name, args):
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))
    client = mock.Mock()
    client.chat.return_value = SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))
    return client


_CFG = SimpleNamespace(LLM_BACKEND="gemini", LLM_FALLBACK_ENABLED=True,
                       OLLAMA_MODEL="qwen3:4b", OLLAMA_FAST_MODEL="", OLLAMA_KEEP_ALIVE="5m",
                       TOOL_ROUTER_LLM_TIMEOUT_S=12.0)


def test_tool_router_falls_back_to_ollama_on_quota(fresh_router, monkeypatch) -> None:
    gemini = mock.Mock()
    gemini.models.generate_content.side_effect = _QuotaErr(_QUOTA_429)
    ollama = _ollama_answering("open_app", {"target": "chrome"})
    _patch_clients(monkeypatch, gemini, ollama)
    monkeypatch.setattr(tool_router, "_get_gemini_tools", lambda: [], raising=False)

    out = fresh_router._resolve_tool_call_llm("hey can you open chrome for me", "gemini-x", _CFG)

    assert out == {"name": "open_app", "arguments": {"target": "chrome"}}
    assert ollama.chat.call_args.kwargs["model"] == "qwen3:4b"
    assert fresh_router._gemini_blocked_until - time.monotonic() > 48000


def test_tool_router_skips_gemini_while_blocked(fresh_router, monkeypatch) -> None:
    gemini = mock.Mock()
    ollama = _ollama_answering("weather", {"location": "delhi"})
    _patch_clients(monkeypatch, gemini, ollama)
    monkeypatch.setattr(tool_router, "_gemini_blocked_until", time.monotonic() + 600)

    out = fresh_router._resolve_tool_call_llm("weather in delhi", "gemini-x", _CFG)

    assert out["name"] == "weather"
    gemini.models.generate_content.assert_not_called()


def test_tool_router_respects_disabled_fallback(fresh_router, monkeypatch) -> None:
    gemini = mock.Mock()
    gemini.models.generate_content.side_effect = _QuotaErr(_QUOTA_429)
    ollama = _ollama_answering("open_app", {"target": "chrome"})
    _patch_clients(monkeypatch, gemini, ollama)
    monkeypatch.setattr(tool_router, "_get_gemini_tools", lambda: [], raising=False)
    cfg = SimpleNamespace(**{**vars(_CFG), "LLM_FALLBACK_ENABLED": False})

    with pytest.raises(_QuotaErr):
        fresh_router._resolve_tool_call_llm("open chrome", "gemini-x", cfg)
    ollama.chat.assert_not_called()
