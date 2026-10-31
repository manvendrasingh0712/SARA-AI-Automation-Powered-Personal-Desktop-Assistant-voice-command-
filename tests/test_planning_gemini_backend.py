"""
Coverage for the DEFAULT planner/executor tool-calling path: Gemini.

Phase 1 audit finding D4
------------------------
Config.LLM_BACKEND defaults to "gemini" (config.py), and
sara.core.llm.clients._select_llm_client() also falls back to gemini. So in a
default install, planner._call_planner_llm() and the executor's step-correction
call both go through:

    client.models.generate_content(model=..., contents=..., config=...)
    -> resp.function_calls[i].name / .args

Every existing planning test used FakeOllamaClient with
FakeConfig(LLM_BACKEND="ollama"), which exercises the other branch
(`client.chat(...)` -> `resp.message.tool_calls[...]`). tests/test_planning.py
is the largest test file in the repo and uses FakeOllamaClient 40 times, yet no
test anywhere touched generate_content or function_calls for planning. The
default production path had zero coverage.

This module closes that gap, and deliberately asserts the two branches agree:
test_both_backends_agree_* runs the same scenario through each fake and
compares the outcome, so a future change to one branch that is not mirrored in
the other fails here rather than in the field.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from sara.core.planning import planner
from sara.core.planning.schema import PlanValidationError
from sara.core.planning.planner import PlanningUnavailableError
from tests.planning_doubles import (
    FakeConfig,
    FakeGeminiClient,
    FakeGeminiWrongFunctionClient,
    FakeOllamaClient,
)

ALLOWED_APPS = frozenset({"chrome", "notepad", "calculator", "spotify"})


def _gemini_cfg(**overrides):
    """FakeConfig pinned to the DEFAULT backend."""
    overrides.setdefault("LLM_BACKEND", "gemini")
    return FakeConfig(**overrides)


def _propose(client, cfg=None, user_input="weather in Jaipur and cricket news"):
    cfg = cfg or _gemini_cfg()
    with patch("sara.core.planning.planner._get_gemini_client", return_value=client):
        return planner.propose_plan(
            user_input,
            "gemini-2.0-flash",
            cfg,
            max_steps=cfg.PLANNING_MAX_STEPS,
            timeout_s=cfg.PLANNING_TOTAL_TIMEOUT_S,
            allowed_apps=ALLOWED_APPS,
            app_allowlist_enabled=True,
        )


# ──────────────────────────────────────────────────────────────────────
# The fake must actually match the shape production reads
# ──────────────────────────────────────────────────────────────────────

def test_fake_gemini_client_exposes_the_production_call_shape() -> None:
    """Guard the guard: if the fake drifts, every test below passes vacuously."""
    client = FakeGeminiClient()
    assert hasattr(client, "models"), "production calls client.models.generate_content(...)"
    assert callable(client.models.generate_content)

    resp = client.models.generate_content(model="m", contents="hi", config=None)
    assert hasattr(resp, "function_calls"), "production reads resp.function_calls"
    call = resp.function_calls[0]
    assert hasattr(call, "name") and hasattr(call, "args"), (
        "production reads call.name / call.args, not call.function.arguments"
    )
    # And it must NOT accidentally satisfy the Ollama shape.
    assert not hasattr(resp, "message")


def test_gemini_branch_is_the_default_backend() -> None:
    """If this ever flips, the 'default path' premise of this file is wrong."""
    from sara.core.llm.clients import _select_llm_client

    sentinel_ollama, sentinel_gemini = object(), object()
    backend, client = _select_llm_client(
        FakeConfig(LLM_BACKEND="gemini"),
        lambda _c: sentinel_ollama,
        lambda _c: sentinel_gemini,
    )
    assert (backend, client) == ("gemini", sentinel_gemini)

    # An unset/unknown backend must also land on gemini.
    class _NoBackend:
        pass

    backend, client = _select_llm_client(
        _NoBackend(), lambda _c: sentinel_ollama, lambda _c: sentinel_gemini
    )
    assert backend == "gemini"


# ──────────────────────────────────────────────────────────────────────
# planner.propose_plan() over the Gemini branch
# ──────────────────────────────────────────────────────────────────────

def test_valid_plan_is_parsed_from_function_calls() -> None:
    client = FakeGeminiClient()
    plan = _propose(client)

    assert [s.tool for s in plan.steps] == ["weather", "news"]
    assert plan.steps[0].arguments == {"location": "Jaipur"}
    assert client.call_count == 1, "propose_plan should make exactly one LLM call"


def test_generate_content_receives_the_plan_tool_schema() -> None:
    """The model must be offered propose_plan, via config.tools."""
    client = FakeGeminiClient()
    _propose(client)

    kwargs = client.call_log[0]
    assert "contents" in kwargs and "config" in kwargs
    names = {
        decl.name
        for tool in (kwargs["config"].tools or [])
        for decl in (tool.function_declarations or [])
    }
    assert "propose_plan" in names


def test_no_tool_call_raises_planning_unavailable() -> None:
    with pytest.raises(PlanningUnavailableError):
        _propose(FakeGeminiClient(mode="no_tool_call"))


def test_transport_failure_raises_planning_unavailable() -> None:
    with pytest.raises(PlanningUnavailableError):
        _propose(FakeGeminiClient(mode="raises"))


def test_missing_client_raises_planning_unavailable() -> None:
    with patch("sara.core.planning.planner._get_gemini_client", return_value=None):
        with pytest.raises(PlanningUnavailableError):
            planner.propose_plan(
                "weather in Jaipur",
                "gemini-2.0-flash",
                _gemini_cfg(),
                max_steps=4,
                timeout_s=6.0,
                allowed_apps=ALLOWED_APPS,
            )


def test_wrong_function_name_raises_plan_validation_error() -> None:
    """planner handles this explicitly rather than trusting `tools=`."""
    with pytest.raises(PlanValidationError):
        _propose(FakeGeminiWrongFunctionClient())


@pytest.mark.parametrize("mode", ["empty_steps", "malformed_json", "hallucinated_tool"])
def test_unusable_model_output_never_yields_a_runnable_plan(mode: str) -> None:
    """
    An empty plan, a structurally broken one, and one naming an unregistered
    tool must all end as either a raised planning error or a plan with no
    executable steps -- never a plan that would dispatch something unintended.
    """
    try:
        plan = _propose(FakeGeminiClient(mode=mode))
    except (PlanningUnavailableError, PlanValidationError):
        return
    assert not plan.steps, f"mode={mode} produced runnable steps: {plan.steps}"


def test_empty_user_input_is_rejected_before_any_llm_call() -> None:
    client = FakeGeminiClient()
    with pytest.raises(PlanningUnavailableError):
        _propose(client, user_input="   ")
    assert client.call_count == 0, "no LLM call should be made for empty input"


# ──────────────────────────────────────────────────────────────────────
# The two branches must stay in agreement
# ──────────────────────────────────────────────────────────────────────

def _propose_ollama(client, cfg=None):
    cfg = cfg or FakeConfig(LLM_BACKEND="ollama")
    with patch("sara.core.planning.planner._get_ollama_client", return_value=client):
        return planner.propose_plan(
            "weather in Jaipur and cricket news",
            "qwen3:4b",
            cfg,
            max_steps=cfg.PLANNING_MAX_STEPS,
            timeout_s=cfg.PLANNING_TOTAL_TIMEOUT_S,
            allowed_apps=ALLOWED_APPS,
            app_allowlist_enabled=True,
        )


def test_both_backends_agree_on_a_valid_plan() -> None:
    gemini_plan = _propose(FakeGeminiClient())
    ollama_plan = _propose_ollama(FakeOllamaClient())

    assert [s.tool for s in gemini_plan.steps] == [s.tool for s in ollama_plan.steps]
    assert [s.arguments for s in gemini_plan.steps] == [
        s.arguments for s in ollama_plan.steps
    ]


@pytest.mark.parametrize("mode", ["no_tool_call", "raises", "empty_steps", "malformed_json"])
def test_both_backends_agree_on_failure_handling(mode: str) -> None:
    """Same bad model output, same observable outcome, whichever backend."""

    def outcome(run):
        try:
            plan = run()
        except PlanningUnavailableError:
            return "unavailable"
        except PlanValidationError:
            return "invalid"
        return f"plan:{len(plan.steps)}"

    assert outcome(lambda: _propose(FakeGeminiClient(mode=mode))) == outcome(
        lambda: _propose_ollama(FakeOllamaClient(mode=mode))
    )
