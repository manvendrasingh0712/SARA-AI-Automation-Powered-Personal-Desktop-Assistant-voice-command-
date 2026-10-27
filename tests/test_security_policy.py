"""Tests for sara.core.security.policy: decision matrix and argument validation."""
from __future__ import annotations

import dataclasses
import itertools
import time
from types import SimpleNamespace

import pytest

from config import Config
from sara.core.security import policy, tiers

DIRECT = policy.ORIGIN_USER_DIRECT
LLM = policy.ORIGIN_LLM_TOOL
CONFIRMED = policy.ORIGIN_USER_CONFIRMED
PROACTIVE = policy.ORIGIN_PROACTIVE
ORIGINS = (DIRECT, LLM, CONFIRMED, PROACTIVE)


def _expected(tier: int, origin: str, tainted: bool, mode: str) -> str:
    if mode == "off" or origin == CONFIRMED:
        return policy.ALLOW
    if mode == "strict":
        if origin == DIRECT:
            return policy.ALLOW if tier <= 1 else policy.CONFIRM
        return (policy.ALLOW, policy.CONFIRM, policy.DENY, policy.DENY)[tier]
    if origin == DIRECT:
        return policy.ALLOW
    if origin == LLM and not tainted:
        return (policy.ALLOW, policy.ALLOW, policy.CONFIRM, policy.CONFIRM)[tier]
    return (policy.ALLOW, policy.CONFIRM, policy.DENY, policy.DENY)[tier]


@pytest.mark.parametrize("mode", ["standard", "strict", "off"])
def test_matrix_all_registered_tools(mode):
    for tool, tier in tiers.TIERS.items():
        for origin, tainted in itertools.product(ORIGINS, (False, True)):
            res = policy.decide(tool, origin, tainted=tainted, mode=mode)
            assert res.decision == _expected(tier, origin, tainted, mode), (tool, origin, tainted)
            assert (res.tier, res.origin, res.tainted) == (tier, origin, tainted)
            if mode != "off" and tainted and tier >= 1 and origin in (LLM, PROACTIVE):
                assert res.decision != policy.ALLOW, (tool, origin)


def test_reason_codes():
    assert policy.decide("lock_pc", DIRECT, tainted=False, mode="standard").reason == "user_direct_allow"
    assert policy.decide("lock_pc", LLM, tainted=True, mode="standard").reason == "tainted_llm_t2_deny"
    assert policy.decide("lock_pc", LLM, tainted=False, mode="standard").reason == "llm_t2_confirm"
    assert policy.decide("lock_pc", PROACTIVE, tainted=False, mode="standard").reason == "proactive_t2_deny"
    assert policy.decide("lock_pc", DIRECT, tainted=False, mode="strict").reason == "strict_t2_confirm"
    assert policy.decide("lock_pc", CONFIRMED, tainted=True, mode="strict").reason == "user_confirmed_allow"
    assert policy.decide("lock_pc", LLM, tainted=True, mode="off").reason == "mode_off"


def test_unknown_origin_is_llm_tool():
    for bad in ("weird", "", None, ["x"]):
        res = policy.decide("lock_pc", bad, tainted=False, mode="standard")
        assert res.origin == LLM and res.decision == policy.CONFIRM


def test_unknown_tool_is_tier_two_and_alias_resolves():
    clean = policy.decide("no_such_tool", LLM, tainted=False, mode="standard")
    dirty = policy.decide("no_such_tool", LLM, tainted=True, mode="standard")
    assert clean.tier == 2 and clean.decision == policy.CONFIRM
    assert dirty.decision == policy.DENY
    alias = policy.decide("forget_all_memories", LLM, tainted=False, mode="standard")
    assert alias.tier == 3 and alias.decision == policy.CONFIRM


def test_internal_error_fail_safe(monkeypatch):
    def boom(_name):
        raise RuntimeError("x")

    monkeypatch.setattr(tiers, "tier_of", boom)
    res = policy.decide("lock_pc", LLM, tainted=False, mode="standard")
    assert res.decision == policy.DENY and res.reason == "internal_error" and res.tier == 2

    calls = {"n": 0}

    def flaky(_name):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("x")
        return 1

    monkeypatch.setattr(tiers, "tier_of", flaky)
    assert policy.decide("set_volume", LLM, tainted=False, mode="standard").decision == policy.ALLOW


def test_taint_lookup_and_missing_module(monkeypatch):
    stub = SimpleNamespace(is_tainted=lambda: True, taint_sources=lambda: ("web", "file"))
    monkeypatch.setattr(policy, "_taint", stub)
    res = policy.decide("lock_pc", LLM, mode="standard")
    assert res.tainted and res.decision == policy.DENY and res.sources == ("web", "file")
    monkeypatch.setattr(policy, "_taint", None)
    res = policy.decide("lock_pc", LLM, mode="standard")
    assert not res.tainted and res.decision == policy.CONFIRM and res.sources == ()


def test_taint_legacy_names_still_enforced(monkeypatch):
    def needs_arg(value):
        return bool(value)

    stub = SimpleNamespace(
        is_tainted=needs_arg, turn_tainted=lambda: True, turn_sources=lambda: ("web",)
    )
    monkeypatch.setattr(policy, "_taint", stub)
    res = policy.decide("set_volume", LLM, mode="standard")
    assert res.tainted and res.decision == policy.CONFIRM and res.sources == ("web",)


def test_mode_from_config(monkeypatch):
    monkeypatch.setattr(Config, "SECURITY_MODE", "strict", raising=False)
    assert policy.decide("lock_pc", DIRECT, tainted=False).decision == policy.CONFIRM
    monkeypatch.setattr(Config, "SECURITY_MODE", "bogus", raising=False)
    assert policy.decide("lock_pc", DIRECT, tainted=False).decision == policy.ALLOW


def test_policy_result_is_frozen():
    res = policy.decide("weather", DIRECT, tainted=False, mode="standard")
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.decision = "x"  # type: ignore[misc]


ACCEPTED = [
    ("typing_text", {"g1": "hello world"}),
    ("typing_text", {"g1": "x" * 4000}),
    ("typing_text", {"g1": "line1\nline2\tTab"}),
    ("typing_text", {"g1": "Hindi नमस्ते"}),
    ("press_key", {"g1": "ctrl+c"}),
    ("press_key", {"g1": "alt+f4"}),
    ("press_key", {"g1": "win+right"}),
    ("close_app", {"g1": "chrome"}),
    ("close_app", {"g1": "notepad.exe"}),
    ("stop_service", {"g1": "bits"}),
    ("start_service", {"g1": "Spooler"}),
    ("open_url", {"g1": "https://example.com/a"}),
    ("summarize_url", {"g1": "https://news.example.org/story"}),
    ("find_file", {"g1": "resume.pdf"}),
    ("calendar_create", {"title": "Dentist"}),
    ("calendar_create", {"g1": "x" * 120}),
    ("weather", {"g1": "जयपुर"}),
    ("take_note", {"g1": "buy milk and call mom.."}),
    ("open_app", {"g1": "C:\\Program Files\\App\\app.exe"}),
    ("weather", None),
    ("weather", {}),
]


@pytest.mark.parametrize("tool,args", ACCEPTED)
def test_validate_accepts(tool, args):
    assert policy.validate_args(tool, args).ok


REJECTED = [
    ("typing_text", {"g1": "a\x00b"}, "control_chars", "g1"),
    ("typing_text", {"g1": "a\rb"}, "control_chars", "g1"),
    ("typing_text", {"g1": "x" * 4001}, "too_long", "g1"),
    ("weather", {"g1": "a" * 601}, "too_long", "g1"),
    ("press_key", {"g1": "win+r"}, "blocked_chord", "g1"),
    ("press_key", {"g1": "Win + R"}, "blocked_chord", "g1"),
    ("press_key", {"g1": "ctrl+alt+delete"}, "blocked_chord", "g1"),
    ("press_key", {"g1": "alt+f4 alt+f4"}, "blocked_chord", "g1"),
    ("press_key", {"g1": "alt+f4", "count": 3}, "blocked_chord", "g1"),
    ("close_app", {"g1": "csrss.exe"}, "critical_process", "g1"),
    ("close_app", {"g1": "C:\\Windows\\System32\\lsass.exe"}, "critical_process", "g1"),
    ("restart_application", {"g1": "winlogon"}, "critical_process", "g1"),
    ("stop_service", {"g1": "WinDefend"}, "protected_service", "g1"),
    ("start_service", {"g1": "mpssvc"}, "protected_service", "g1"),
    ("stop_service", {"g1": "Windows Defender Antivirus Service"}, "protected_service", "g1"),
    ("open_url", {"g1": "http://localhost:8000"}, "url_local_host", "g1"),
    ("open_url", {"g1": "javascript:alert(1)"}, "url_scheme_javascript", "g1"),
    ("summarize_url", {"g1": "file:///c:/x"}, "url_scheme_file", "g1"),
    ("open_url", {"g1": ""}, "url_empty", "g1"),
    ("find_file", {"g1": ".env"}, "sensitive_path", "g1"),
    ("find_file", {"g1": "credentials.json"}, "sensitive_path", "g1"),
    ("find_file", {"g1": "C:\\Users\\a\\.ssh\\id_rsa"}, "sensitive_path", "g1"),
    ("take_note", {"g1": "..\\..\\.env"}, "sensitive_path", "g1"),
    ("weather", {"g1": "/etc/x/.env"}, "sensitive_path", "g1"),
    ("calendar_create", {"title": "t" * 121}, "title_too_long", "title"),
    ("take_note", {"g1": "https://user:pw@example.com/x"}, "url_credentials", "g1"),
    ("take_note", {"g1": "data:text/plain;base64,AAAA"}, "url_scheme_data", "g1"),
    ("weather", ["x"], "args_not_mapping", ""),
    ("weather", {"g1": ["ok", "a\x00b"]}, "control_chars", "g1"),
    ("weather", {f"k{i}": "x" for i in range(33)}, "too_many_args", ""),
]


@pytest.mark.parametrize("tool,args,reason,field", REJECTED)
def test_validate_rejects(tool, args, reason, field):
    res = policy.validate_args(tool, args)
    assert not res.ok and res.reason == reason and res.field == field


def test_validate_internal_error_fail_safe(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(policy.rules, "chord_blocked", boom)
    assert not policy.validate_args("typing_text", {"g1": "hi"}).ok
    monkeypatch.setattr(policy.rules, "sensitive_path", boom)
    assert policy.validate_args("find_file", {"g1": "abc"}).ok


def test_policy_calls_are_fast():
    start = time.perf_counter()
    for _ in range(1000):
        policy.decide("lock_pc", LLM, tainted=True, mode="standard")
        policy.validate_args("open_url", {"g1": "https://example.com/a?q=1"})
    assert time.perf_counter() - start < 1.0