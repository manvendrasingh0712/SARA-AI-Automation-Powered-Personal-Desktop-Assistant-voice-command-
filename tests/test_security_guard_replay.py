"""Tests for guard.replay_confirmed with fake handlers (temp events DB)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sara.core.security import events, guard

MATCH = SimpleNamespace(groups=lambda: ("x",))
OTHER = SimpleNamespace(groups=lambda: ("y",))


@pytest.fixture(autouse=True)
def env(tmp_path):
    events.configure(db_path=tmp_path / "security.sqlite")
    yield
    events.flush()
    events.configure(None)


def pend(**over):
    base = {
        "action": "guarded_tool", "target": "set_volume", "tool": "set_volume",
        "origin": "llm_tool", "text": "", "tool_name": "set_volume",
        "arguments": {"level": "5"}, "sources": ["web_page"], "expires_at": 9e12,
    }
    base.update(over)
    return base


def run(pending, handlers=None, simple=None, detect=None, build=None):
    return guard.replay_confirmed(
        pending, {"ctx": 1},
        handlers=handlers or {},
        simple_actions=simple or {},
        detect_intent=detect or (lambda text: ("chat", None)),
        build_fake_match=build or (lambda name, args: MATCH),
    )


def test_simple_action_called():
    calls = []
    out = run(pend(tool="lock_pc", arguments=None), simple={"lock_pc": lambda: calls.append(1) or "locked"})
    assert out == "locked" and calls == [1]


def test_handler_path_uses_fake_match():
    seen = []
    handler = lambda match, ctx: seen.append((match, ctx)) or "volume set"
    assert run(pend(), handlers={"set_volume": handler}) == "volume set"
    assert seen == [(MATCH, {"ctx": 1})]
    events.flush()
    row = events.get_events(kind="confirmed")[0]
    assert row["tool"] == "set_volume" and row["origin"] == "user_confirmed"


def test_text_redetected_when_intent_matches():
    seen = []
    handler = lambda match, ctx: seen.append(match) or "ok"
    out = run(
        pend(text="volume five", arguments=None),
        handlers={"set_volume": handler},
        detect=lambda text: ("set_volume", OTHER),
    )
    assert out == "ok" and seen == [OTHER]


def test_text_with_other_intent_falls_back_to_arguments():
    seen = []
    handler = lambda match, ctx: seen.append(match) or "ok"
    run(
        pend(text="volume five"),
        handlers={"set_volume": handler},
        detect=lambda text: ("chat", None),
    )
    assert seen == [MATCH]


def test_bad_args_rejected_safely():
    called = []
    out = run(
        pend(tool="open_url", target="open_url", tool_name="open_url",
             arguments={"url": "javascript:alert(1)"}),
        handlers={"open_url": lambda m, c: called.append(1) or "opened"},
    )
    assert called == []
    assert out == "Sorry, I couldn't do that safely."
    events.flush()
    assert events.get_events(kind="arg_rejected")


def test_missing_handler_returns_none():
    assert run(pend(), handlers={}) is None


def test_no_text_and_no_arguments_returns_none():
    assert run(pend(arguments=None, text=""), handlers={"set_volume": lambda m, c: "x"}) is None


def test_handler_exception_never_leaks():
    def boom(match, ctx):
        raise RuntimeError("secret path C:\\Users\\x")

    out = run(pend(), handlers={"set_volume": boom})
    assert out == "Sorry, I couldn't do that safely."
    assert "secret" not in out


def test_hinglish_failure_sentence():
    def boom(match, ctx):
        raise RuntimeError("x")

    out = guard.replay_confirmed(
        pend(), {}, handlers={"set_volume": boom}, simple_actions={},
        detect_intent=lambda t: ("chat", None), build_fake_match=lambda n, a: MATCH,
        lang="hinglish",
    )
    assert out == "Sorry, main ye safely nahi kar paayi."


def test_bad_pending_returns_none():
    assert run({"action": "guarded_tool"}) is None