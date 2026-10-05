"""Dispatcher-level tests for the security guard wiring (stubs, temp events DB)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from config import Config
from sara.core.security import events, guard
from sara.core.security import taint as taint_mod
from sara.orchestrator import dispatcher, route_chat

MATCH = SimpleNamespace(groups=lambda: ())
TODO_ARGS = {"title": "milk"}


def set_taint(monkeypatch, sources: tuple[str, ...]) -> None:
    monkeypatch.setattr(taint_mod, "turn_tainted", lambda: bool(sources))
    monkeypatch.setattr(taint_mod, "turn_sources", lambda: tuple(sources))


class Spy:
    """Handler stub that counts its calls."""

    def __init__(self, result: str = "done"):
        self.calls = 0
        self.result = result

    def __call__(self, match, ctx):
        self.calls += 1
        return self.result


@pytest.fixture
def spy(tmp_path, monkeypatch):
    events.configure(db_path=tmp_path / "security.sqlite")
    monkeypatch.setattr(Config, "SECURITY_MODE", "standard", raising=False)
    set_taint(monkeypatch, ())
    monkeypatch.setattr(dispatcher, "_quick", lambda ctx, text: text)
    monkeypatch.setattr(dispatcher, "_ack", lambda ctx: None)
    monkeypatch.setattr(dispatcher, "_log_action", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "_acknowledge_reminder", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "log_unmatched", lambda text: None)
    monkeypatch.setattr(
        dispatcher, "build_fake_match",
        lambda name, args: SimpleNamespace(groups=lambda: (), name=name, args=args),
    )
    handler = Spy()
    monkeypatch.setitem(dispatcher._INTENT_HANDLERS, "delete_todo", handler)
    dispatcher.TURN_STATE.begin()
    yield handler
    events.flush()
    events.configure(None)


def route_to_tool(monkeypatch, route: str = "tool") -> None:
    monkeypatch.setattr(dispatcher, "detect_intent", lambda text: ("chat", None))
    monkeypatch.setattr(dispatcher, "_route_chat_message", lambda text, state: (route, None))
    monkeypatch.setattr(
        dispatcher, "resolve_tool_call",
        lambda text, model: {"name": "delete_todo", "arguments": dict(TODO_ARGS)},
    )


def run(text: str, confirm_state: dict) -> str:
    brain = SimpleNamespace(model_name="m", record_exchange=lambda *a: None)
    return dispatcher._handle_command(
        text, brain, SimpleNamespace(), None, None, None, None,
        lambda *a: None, {}, confirm_state=confirm_state, context_state={},
    )


def test_tainted_turn_llm_t2_denied_and_handler_not_called(spy, monkeypatch):
    route_to_tool(monkeypatch)
    set_taint(monkeypatch, ("web_page",))
    state: dict = {}
    result = run("drop the milk item", state)
    assert spy.calls == 0
    assert "a web page" in result and "tell me directly" in result
    assert "pending" not in state


def test_clean_turn_llm_t2_confirm_then_yes_runs_once(spy, monkeypatch):
    route_to_tool(monkeypatch)
    state: dict = {}
    assert run("drop the milk item", state) == (
        "delete todo -- shall I go ahead? Say yes or cancel."
    )
    assert state["pending"]["action"] == "guarded_tool" and spy.calls == 0
    assert run("yes", state) == "done"
    assert spy.calls == 1 and "pending" not in state
    route_to_tool(monkeypatch, route="chat")
    run("yes", state)
    assert spy.calls == 1


def test_no_cancels_and_logs_denied_by_user(spy, monkeypatch):
    route_to_tool(monkeypatch)
    state: dict = {}
    run("drop the milk item", state)
    assert run("no", state) == "Okay, cancelled."
    assert spy.calls == 0 and "pending" not in state
    events.flush()
    row = events.get_events(kind="denied_by_user")[0]
    assert row["tool"] == "delete_todo" and row["origin"] == "llm_tool"


def test_mode_off_behaves_as_before(spy, monkeypatch):
    route_to_tool(monkeypatch)
    set_taint(monkeypatch, ("web_page",))
    monkeypatch.setattr(Config, "SECURITY_MODE", "off", raising=False)
    assert run("drop the milk item", {}) == "done"
    assert spy.calls == 1


def test_user_direct_handler_runs_as_before(spy, monkeypatch):
    monkeypatch.setattr(dispatcher, "detect_intent", lambda text: ("delete_todo", MATCH))
    set_taint(monkeypatch, ("web_page",))
    assert run("delete the milk todo", {}) == "done"
    assert spy.calls == 1


def test_user_direct_t3_simple_action_runs_as_before(spy, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(dispatcher, "detect_intent", lambda text: ("shutdown_system", None))
    monkeypatch.delitem(dispatcher._INTENT_HANDLERS, "shutdown_system", raising=False)
    monkeypatch.setitem(
        dispatcher.system_tools.SIMPLE_ACTIONS, "shutdown_system",
        lambda: calls.append(1) or "bye",
    )
    assert run("shut the pc down", {}) == "bye"
    assert calls == [1]


def test_strict_mode_user_direct_t2_needs_yes(spy, monkeypatch):
    monkeypatch.setattr(Config, "SECURITY_MODE", "strict", raising=False)
    monkeypatch.setattr(dispatcher, "detect_intent", lambda text: ("delete_todo", MATCH))
    state: dict = {}
    assert "Say yes or cancel" in run("delete the milk todo", state)
    assert spy.calls == 0 and state["pending"]["text"] == "delete the milk todo"
    assert run("yes", state) == "done"
    assert spy.calls == 1


def test_plan_step_deny_raises_blocked_message(spy, monkeypatch):
    set_taint(monkeypatch, ("calendar",))
    fn = dispatcher._build_plan_dispatch_fn({"confirm_state": {}})
    with pytest.raises(guard.PlanStepBlocked) as err:
        fn("delete_todo", dict(TODO_ARGS))
    assert "your calendar" in err.value.message
    assert spy.calls == 0


def test_plan_step_confirm_arms_guarded_pending(spy):
    ctx = {"confirm_state": {}}
    fn = dispatcher._build_plan_dispatch_fn(ctx)
    with pytest.raises(dispatcher.PlanStepRequiresConfirmation) as err:
        fn("delete_todo", dict(TODO_ARGS))
    assert err.value.action == "guarded_tool" and err.value.target == "delete_todo"
    assert ctx["confirm_state"]["pending"]["arguments"] == TODO_ARGS
    assert spy.calls == 0


def test_planner_confirmation_keeps_guarded_pending(spy, monkeypatch):
    monkeypatch.setattr(dispatcher, "detect_intent", lambda text: ("chat", None))
    monkeypatch.setattr(dispatcher, "_route_chat_message", lambda text, state: ("plan", None))

    def fake_plan(text, model, dispatch, cfg, allowed_apps=None):
        try:
            dispatch("delete_todo", dict(TODO_ARGS))
        except dispatcher.PlanStepRequiresConfirmation as exc:
            reason = f"CONFIRMATION_REQUIRED::{exc.action}::{exc.target or ''}::{exc.prompt}"
        return SimpleNamespace(aborted=True, abort_reason=reason, final_message="")

    monkeypatch.setattr(dispatcher, "try_plan_and_execute", fake_plan)
    state: dict = {}
    assert "Say yes or cancel" in run("drop milk and then more", state)
    assert state["pending"]["arguments"] == TODO_ARGS
    assert run("yes", state) == "done"
    assert spy.calls == 1


def test_retry_via_tool_router_denied_when_tainted(spy, monkeypatch):
    set_taint(monkeypatch, ("notes",))
    monkeypatch.setattr(route_chat, "_tool_router_available", lambda: True)
    monkeypatch.setattr(
        route_chat, "resolve_tool_call",
        lambda text, model: {"name": "delete_todo", "arguments": dict(TODO_ARGS)},
    )
    monkeypatch.setattr(
        route_chat, "build_fake_match", lambda name, args: SimpleNamespace(groups=lambda: ())
    )
    brain = SimpleNamespace(model_name="m")
    result = route_chat._retry_via_tool_router("drop that", {"confirm_state": {}}, brain)
    assert "your saved notes" in result and spy.calls == 0


def test_retry_via_tool_router_clean_arms_confirmation(spy, monkeypatch):
    monkeypatch.setattr(route_chat, "_tool_router_available", lambda: True)
    monkeypatch.setattr(
        route_chat, "resolve_tool_call",
        lambda text, model: {"name": "delete_todo", "arguments": dict(TODO_ARGS)},
    )
    ctx = {"confirm_state": {}}
    result = route_chat._retry_via_tool_router("drop that", ctx, SimpleNamespace(model_name="m"))
    assert "Say yes or cancel" in result
    assert ctx["confirm_state"]["pending"]["action"] == "guarded_tool" and spy.calls == 0