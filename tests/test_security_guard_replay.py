"""Confirmation approval tokens are argument-bound, expiring and one-use."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from config import Config
from sara.core.security import events, guard
from sara.core.security import taint as taint_mod

MATCH = SimpleNamespace(groups=lambda: ("5",))


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    events.configure(db_path=tmp_path / "security.sqlite")
    monkeypatch.setattr(Config, "SECURITY_MODE", "standard", raising=False)
    monkeypatch.setattr(taint_mod, "turn_tainted", lambda: False)
    monkeypatch.setattr(taint_mod, "turn_sources", lambda: ())
    yield
    events.flush()
    events.configure(None)


def make_pending(
    tool="lock_pc", *, args=None, match=None, text="", tool_name=None, origin="llm_tool"
):
    outcome = guard.check_tool_call(
        tool, origin, args=args, match=match, text=text, tool_name=tool_name
    )
    assert outcome.action == "confirm"
    assert outcome.pending and outcome.pending.get("approval_id")
    return outcome.pending


def replay(pending, handlers=None, simple=None, detect=None, build=None, cancel_event=None):
    return guard.replay_confirmed(
        pending, {"ctx": 1},
        handlers=handlers or {},
        simple_actions=simple or {},
        detect_intent=detect or (lambda text: ("chat", None)),
        build_fake_match=build or (lambda name, args: MATCH),
        cancel_event=cancel_event,
    )


def test_simple_action_called_once_and_duplicate_replay_denied():
    calls = []
    pending = make_pending()
    action = lambda: calls.append(1) or "locked"
    assert replay(pending, simple={"lock_pc": action}) == "locked"
    assert replay(pending, simple={"lock_pc": action}) is None
    assert calls == [1]


def test_handler_replay_uses_approved_tool_and_arguments():
    seen = []
    pending = make_pending(
        "open_url", args={"url": "https://example.com/page"},
        tool_name="open_url", origin="proactive",
    )
    handler = lambda match, ctx: seen.append((match, ctx)) or "opened"
    out = replay(
        pending, handlers={"open_url": handler},
        build=lambda name, args: (name, dict(args)),
    )
    assert out == "opened"
    assert seen == [(("open_url", {"url": "https://example.com/page"}), {"ctx": 1})]


def test_direct_match_replay_must_preserve_the_approved_arguments():
    pending = make_pending("set_volume", match=MATCH, text="set volume five")
    changed = SimpleNamespace(groups=lambda: ("6",))
    called = []
    assert replay(
        pending,
        handlers={"set_volume": lambda *_: called.append(1)},
        detect=lambda _text: ("set_volume", changed),
    ) is None
    assert called == []


def test_modified_arguments_invalidate_approval():
    called = []
    pending = make_pending(
        "open_url", args={"url": "https://example.com/page"},
        tool_name="open_url", origin="proactive",
    )
    pending["arguments"]["url"] = "https://evil.example/"
    assert replay(pending, handlers={"open_url": lambda *_: called.append(1)}) is None
    assert called == []


def test_approval_cannot_change_tool_or_target():
    called = []
    pending = make_pending()
    pending["tool"] = "shutdown_system"
    pending["target"] = "shutdown_system"
    assert replay(pending, simple={"shutdown_system": lambda: called.append(1)}) is None
    assert called == []


def test_expired_approval_cannot_be_replayed(monkeypatch):
    pending = make_pending()
    now = guard.time.time()
    monkeypatch.setattr(guard.time, "time", lambda: now + 500)
    called = []
    assert replay(pending, simple={"lock_pc": lambda: called.append(1)}) is None
    assert called == []


def test_cancelled_approval_cannot_be_replayed():
    pending = make_pending()
    guard.cancel_pending(pending)
    called = []
    assert replay(pending, simple={"lock_pc": lambda: called.append(1)}) is None
    assert called == []


def test_cancel_event_prevents_replay():
    pending = make_pending()
    cancel = __import__("threading").Event()
    cancel.set()
    called = []
    assert replay(
        pending, simple={"lock_pc": lambda: called.append(1)}, cancel_event=cancel
    ) is None
    assert called == []


def test_unregistered_tool_and_bad_pending_are_denied():
    assert replay({"action": "guarded_tool", "tool": "invented"}) is None
    assert replay({"action": "guarded_tool", "tool": "lock_pc"}) is None


def test_missing_handler_returns_none():
    pending = make_pending(
        "open_url", args={"url": "https://example.com"},
        tool_name="open_url", origin="proactive",
    )
    assert replay(pending, handlers={}) is None


def test_invalid_arguments_after_approval_are_rejected():
    pending = make_pending(
        "open_url", args={"url": "https://example.com"},
        tool_name="open_url", origin="proactive",
    )
    pending["arguments"]["url"] = "javascript:alert(1)"
    # The signed snapshot rejects argument alteration before the handler can run.
    called = []
    assert replay(pending, handlers={"open_url": lambda *_: called.append(1)}) is None
    assert called == []


def test_handler_exception_does_not_leak_details():
    pending = make_pending(
        "set_volume", origin="proactive", text="set volume", match=MATCH
    )

    def boom(match, ctx):
        raise RuntimeError("secret path C:\\Users\\x")

    out = replay(
        pending, handlers={"set_volume": boom},
        detect=lambda text: ("set_volume", MATCH),
    )
    assert out == "Sorry, I couldn't do that safely."
    assert "secret" not in out


def test_cancelled_pending_is_consumed_even_if_caller_keeps_a_copy():
    pending = make_pending()
    copied = dict(pending)
    guard.cancel_pending(pending)
    called = []
    assert replay(copied, simple={"lock_pc": lambda: called.append(1)}) is None
    assert called == []
