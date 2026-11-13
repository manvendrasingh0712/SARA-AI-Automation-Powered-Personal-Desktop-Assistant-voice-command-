"""Cross-path authorization regressions; all side-effecting boundaries are mocked."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from config import Config
from sara.core.security import events, guard
from sara.core.security import taint as taint_mod
from sara.orchestrator import dispatcher
from sara.orchestrator.state import TURN_STATE, VOICE_SESSION_CANCEL


@pytest.fixture(autouse=True)
def security_state(tmp_path, monkeypatch):
    events.configure(db_path=tmp_path / "security.sqlite")
    TURN_STATE.begin()
    monkeypatch.setattr(Config, "SECURITY_MODE", "standard", raising=False)
    monkeypatch.setattr(taint_mod, "turn_tainted", lambda: False)
    monkeypatch.setattr(taint_mod, "turn_sources", lambda: ())
    yield
    events.flush()
    events.configure(None)


def _base_handler_args():
    return (
        "run", SimpleNamespace(model_name="test", record_exchange=lambda *a: None),
        SimpleNamespace(speak=lambda *a, **k: None), None, None, None, None,
        lambda *a: None, {},
    )


def test_unknown_dynamic_handler_is_denied_before_invocation(monkeypatch):
    called = []
    monkeypatch.setitem(dispatcher._INTENT_HANDLERS, "unreviewed_test_tool", lambda *_: called.append(1))
    monkeypatch.setattr(dispatcher, "detect_intent", lambda text: ("unreviewed_test_tool", None))
    result = dispatcher._handle_command(*_base_handler_args(), confirm_state={}, context_state={})
    assert called == []
    assert "did not run" in result


def test_dispatcher_denies_if_guard_missing_or_raises(monkeypatch):
    monkeypatch.setattr(dispatcher, "_guard", None)
    assert dispatcher._guard_check("weather", "user_direct").action == "deny"

    class BrokenGuard:
        def check_tool_call(self, *_args, **_kwargs):
            raise RuntimeError("hidden details")

    monkeypatch.setattr(dispatcher, "_guard", BrokenGuard())
    out = dispatcher._guard_check("shutdown_system", "llm_tool")
    assert out.action == "deny" and "hidden details" not in out.message


def test_builtin_handler_cannot_be_replaced_by_skill_registration():
    original = dispatcher._INTENT_HANDLERS["weather"]
    with pytest.raises(ValueError, match="cannot replace built-in"):
        dispatcher.register_handler("weather", lambda *_: "replacement")
    assert dispatcher._INTENT_HANDLERS["weather"] is original


def test_builtin_intent_patterns_cannot_be_replaced_by_skill_registration():
    from sara.core.intent import engine

    before = list(engine._INTENT_PATTERNS)
    with pytest.raises(ValueError, match="cannot replace built-in"):
        engine.register_intent("weather", [r".*"])
    assert engine._INTENT_PATTERNS == before


def test_proactive_routine_denies_high_risk_step_and_stops_sequence(monkeypatch):
    from sara.core import routines
    from sara.tools import system as system_tools

    calls = []
    monkeypatch.setitem(system_tools.SIMPLE_ACTIONS, "shutdown_system", lambda: calls.append("shutdown"))
    monkeypatch.setitem(system_tools.SIMPLE_ACTIONS, "max_volume", lambda: calls.append("volume") or "ok")

    class DB:
        def get_routine(self, _name):
            return {
                "steps": [
                    {"type": "simple_action", "key": "shutdown_system"},
                    {"type": "simple_action", "key": "max_volume"},
                ]
            }

    spoken = []
    outcome = routines.run_routine(
        "scheduled", {
            "db": DB(), "tts": SimpleNamespace(speak=lambda text, **_: spoken.append(text)),
            "security_origin": "proactive", "confirm_state": {},
        },
    )
    assert calls == []
    assert outcome[0]["authorization_blocked"] is True
    assert len(outcome) == 1
    assert "not allowed" in spoken[0].lower()


def test_proactive_routine_keeps_registered_read_only_steps(monkeypatch):
    from sara.core import routines
    from sara.orchestrator import dispatcher as dispatch

    calls = []
    monkeypatch.setitem(dispatch._INTENT_HANDLERS, "time_query", lambda *_: calls.append(1) or "time")

    class DB:
        def get_routine(self, _name):
            return {"steps": [{"type": "intent", "name": "time_query", "args": {}}]}

    result = routines.run_routine("clock", {"db": DB(), "security_origin": "proactive"})
    assert calls == [1]
    assert result[0]["ok"] is True


@pytest.mark.parametrize("bad_args", [False, [], "bad"])
def test_routine_rejects_malformed_arguments_without_running_handler(monkeypatch, bad_args):
    from sara.core import routines
    from sara.orchestrator import dispatcher as dispatch

    calls = []
    monkeypatch.setitem(dispatch._INTENT_HANDLERS, "time_query", lambda *_: calls.append(1))

    class DB:
        def get_routine(self, _name):
            return {"steps": [{"type": "intent", "name": "time_query", "args": bad_args}]}

    result = routines.run_routine("invalid", {"db": DB()})
    assert calls == []
    assert result[0]["ok"] is False


def test_routine_settings_bridge_applies_strict_policy(monkeypatch):
    from sara.gui.app import routines_api
    from sara.tools import system as system_tools

    monkeypatch.setattr(Config, "SECURITY_MODE", "strict", raising=False)
    calls = []
    monkeypatch.setitem(system_tools.SIMPLE_ACTIONS, "shutdown_system", lambda: calls.append(1))
    spoken = threading.Event()
    transcript = []

    class DB:
        def get_routine(self, _name):
            return {"steps": [{"type": "simple_action", "key": "shutdown_system"}]}

    host = SimpleNamespace(
        db=DB(), brain=None,
        tts=SimpleNamespace(speak=lambda text, **_: (transcript.append(text), spoken.set())),
        ears=None, reminders=None, vision=None, volume_state={}, playback_state={},
        confirm_state={}, system_tools=system_tools,
    )
    monkeypatch.setattr(routines_api, "_push", lambda *args: None)
    response = routines_api.ApiRoutinesMixin.run_routine_now(host, "strict")
    assert response == {"ok": True}
    assert spoken.wait(2.0)
    assert calls == []
    assert "tier 3" in transcript[0] and "approval" in transcript[0].lower()


def test_stop_sara_invalidates_pending_approval(monkeypatch):
    from sara.gui.app import core

    pending = guard.check_tool_call("shutdown_system", "llm_tool").pending
    assert pending is not None
    copied = dict(pending)
    host = SimpleNamespace(confirm_state={"pending": pending}, tts=SimpleNamespace(stop=lambda: None))
    monkeypatch.setattr(core, "_push", lambda *args: None)
    VOICE_SESSION_CANCEL.clear()
    try:
        assert core.ApiCoreMixin.stop_sara(host) == {"ok": True}
        assert "pending" not in host.confirm_state
        called = []
        assert guard.replay_confirmed(
            copied, {}, handlers={}, simple_actions={"shutdown_system": lambda: called.append(1)},
            detect_intent=lambda text: ("chat", None), build_fake_match=lambda *_: None,
        ) is None
        assert called == [] and VOICE_SESSION_CANCEL.is_set()
    finally:
        VOICE_SESSION_CANCEL.clear()
        TURN_STATE.begin()

