"""tests for sara.core.intent.types"""
from __future__ import annotations

import dataclasses

import pytest

from sara.core.intent.types import (
    EntitySpec,
    IntentSpec,
    ResolvedCommand,
    RouteState,
)

SECRET = "ZX-SECRET-7731"
RAW = "please open ZX-RAW-TEXT-4420 now"


def _cmd(**kwargs):
    base = {"intent": "open_app", "entities": {"app": "notepad"}}
    base.update(kwargs)
    return ResolvedCommand(**base)


def test_entity_spec_is_frozen():
    spec = EntitySpec(name="app", type="str")
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]


def test_intent_spec_is_frozen():
    spec = IntentSpec(name="open_app", desc="open an app")
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.tier = 0  # type: ignore[misc]


def test_resolved_command_is_frozen():
    cmd = _cmd()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cmd.intent = "other"  # type: ignore[misc]


def test_entities_read_only_and_isolated_from_source_dict():
    source = {"app": "notepad"}
    cmd = ResolvedCommand(intent="open_app", entities=source)
    with pytest.raises(TypeError):
        cmd.entities["app"] = "calc"  # type: ignore[index]
    source["app"] = "changed"
    source["extra"] = "x"
    assert cmd.entity("app") == "notepad"
    assert "extra" not in cmd.entities


def test_entity_default():
    cmd = _cmd()
    assert cmd.entity("app") == "notepad"
    assert cmd.entity("missing") is None
    assert cmd.entity("missing", 5) == 5


def test_invalid_confidence_and_likelihood_raise():
    with pytest.raises(ValueError):
        _cmd(confidence=1.2)
    with pytest.raises(ValueError):
        _cmd(command_likelihood=-0.1)


def test_boundary_values_accepted():
    assert _cmd(confidence=0.0, command_likelihood=0.0).confidence == 0.0
    assert _cmd(confidence=1.0, command_likelihood=1.0).command_likelihood == 1.0


def test_with_changes_returns_new_object_and_keeps_original():
    original = _cmd(confidence=0.5)
    changed = original.with_changes(confidence=0.9, intent="close_app")
    assert changed is not original
    assert original.confidence == 0.5
    assert original.intent == "open_app"
    assert changed.confidence == 0.9
    assert changed.intent == "close_app"
    assert changed.entity("app") == "notepad"


def test_with_changes_revalidates():
    with pytest.raises(ValueError):
        _cmd().with_changes(confidence=2)


def test_is_actionable_true_for_direct_real_intent():
    assert _cmd(state=RouteState.DIRECT).is_actionable is True


def test_is_actionable_false_for_chat_intent():
    assert _cmd(intent="chat").is_actionable is False


def test_is_actionable_false_when_ambiguous():
    assert _cmd(ambiguity=True).is_actionable is False


def test_is_actionable_false_for_clarify_state():
    assert _cmd(state=RouteState.CLARIFY).is_actionable is False


def test_log_dict_hides_text_and_entity_values_by_default():
    cmd = ResolvedCommand(
        intent="open_app",
        entities={"app": SECRET},
        raw_text=RAW,
        normalized_text=RAW.lower(),
        evidence=("regex",),
    )
    log = cmd.to_log_dict()
    text = str(log)
    assert SECRET not in text
    assert "ZX-RAW-TEXT-4420" not in text
    assert "raw_text" not in log
    assert "normalized_text" not in log
    assert log["entity_names"] == ["app"]
    assert log["intent"] == "open_app"
    assert log["resolver"] == "fast"
    assert log["state"] == "direct"
    assert log["evidence"] == ["regex"]


def test_log_dict_include_text_contains_text_and_values():
    cmd = ResolvedCommand(
        intent="open_app",
        entities={"app": SECRET},
        raw_text=RAW,
        normalized_text=RAW.lower(),
    )
    log = cmd.to_log_dict(include_text=True)
    text = str(log)
    assert SECRET in text
    assert "ZX-RAW-TEXT-4420" in text
    assert log["raw_text"] == RAW


def test_intent_spec_entity_helpers():
    required = EntitySpec(name="app", type="str")
    optional = EntitySpec(name="mode", type="str", required=False)
    spec = IntentSpec(name="open_app", desc="open", entities=(required, optional))
    assert spec.entity_names == ("app", "mode")
    assert spec.required_entities == (required,)
    assert spec.is_zero_arg is False


def test_intent_spec_zero_arg():
    spec = IntentSpec(name="screenshot", desc="take screenshot")
    assert spec.is_zero_arg is True
    assert spec.entity_names == ()
    assert spec.required_entities == ()