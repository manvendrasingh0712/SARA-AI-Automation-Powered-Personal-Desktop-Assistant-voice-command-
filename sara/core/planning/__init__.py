"""
sara.core.planning
Public package interface for the multi-step tool-chaining planner.

The primary function callers outside this package need is
try_plan_and_execute() below. Everything else (schema, planner,
executor, trigger) is re-exported here for convenience/testing (see
test_doubles.py / tests/test_planning.py) but is otherwise an internal
implementation detail.

INTEGRATION CONTRACT
---------------------
sara/orchestrator/intent_handlers.py calls try_plan_and_execute() from
within its existing `intent == "chat"` branch, BEFORE falling back to
resolve_tool_call() -- never instead of it. This function returns None
in every case where the caller should proceed exactly as it does today
(fall through to resolve_tool_call(), then to the LLM chat stream):

  - Config.PLANNING_ENABLED is False.
  - trigger.should_attempt_plan() says this message doesn't look
    multi-step.
  - The planning LLM call is unavailable, times out, or its content
    can't be turned into a valid plan.
  - The resulting plan collapsed to a single step (no benefit over the
    cheaper, already-hardened single-tool path).

This function NEVER raises -- every internal failure mode is caught and
converted into a None return, matching the "never block, always degrade
gracefully" shape used throughout this codebase (tool_router.py, rag.py,
proactive.py).

CIRCULAR IMPORT AVOIDANCE
---------------------------
This module does NOT import sara.orchestrator.intent_handlers. The
caller supplies a `dispatch` callback (see executor.DispatchFn) that
already knows how to route a resolved (tool_name, arguments) pair to the
real handler -- intent_handlers.py builds this callback using its own
existing _INTENT_HANDLERS table and tool_router.build_fake_match(), so
the dependency arrow points ONE way only: intent_handlers -> planning.

OBSERVABILITY
---------------
Every terminal outcome of try_plan_and_execute() (declined, unavailable,
collapsed, aborted, completed) logs exactly one structured line at INFO
or above, tagged with the deciding reason -- this function's log output
alone should be sufficient to reconstruct, after the fact, why any given
"chat"-intent message did or didn't get a multi-step plan, without
needing to reproduce the conversation live.
"""
from __future__ import annotations

import logging
import time
from typing import Any, FrozenSet, Optional

from .executor import DispatchFn, execute_plan, shutdown_correction_executor
from .planner import PlanningUnavailableError, propose_plan, shutdown_planner_executor
from .schema import (
    Plan,
    PlanOutcome,
    PlanStep,
    PlanValidationError,
    StepResult,
    StepStatus,
    validate_app_target,
    validate_tool_arguments,
    validate_url,
)
from .trigger import explain_trigger_decision, should_attempt_plan

__all__ = [
    "try_plan_and_execute",
    "should_attempt_plan",
    "explain_trigger_decision",
    "shutdown_planning_executors",
    "Plan",
    "PlanStep",
    "PlanOutcome",
    "StepResult",
    "StepStatus",
    "PlanValidationError",
    "PlanningUnavailableError",
    "validate_url",
    "validate_app_target",
    "validate_tool_arguments",
]

logger = logging.getLogger("sara.core.planning")


def try_plan_and_execute(
    user_input: str,
    model_name: str,
    dispatch: DispatchFn,
    cfg: Any,
    *,
    allowed_apps: FrozenSet[str] = frozenset(),
) -> Optional[PlanOutcome]:
    """
    Attempts the full multi-step planning + execution flow for
    `user_input`. Returns a PlanOutcome on success (even a PARTIALLY
    successful one -- check PlanOutcome.results / .aborted for details),
    or None if planning should be skipped/declined entirely, in which
    case the caller MUST fall back to its existing single-tool path.

    Parameters
    ----------
    user_input:
        The raw, unmatched ("chat" intent) user command. May be English,
        Hindi, or Hinglish -- the planner's system prompt is built to
        understand all three while keeping tool names/argument keys in
        English (matching TOOL_NAME_TO_INTENT's keys).
    model_name:
        The Ollama model name to use for both the planning call and any
        per-step correction calls (same model the caller's single-tool
        resolve_tool_call() already uses -- see
        sara.orchestrator.intent_handlers's brain.model_name).
    dispatch:
        Callback of shape (tool_name: str, arguments: dict) -> str,
        raising on failure. The caller owns tool execution entirely;
        this package only decides WHAT to call and WHEN, never HOW.
    cfg:
        The Config class (or a compatible test double) -- read via
        getattr() throughout so a partially-populated test config never
        raises AttributeError.
    allowed_apps:
        Frozenset of allowed application name/alias substrings, used to
        validate any open_app/close_app step this plan proposes. Callers
        should pass a frozenset built from Config.APP_LAUNCH_ALLOWLIST.

    Returns
    -------
    Optional[PlanOutcome]:
        A PlanOutcome if a multi-step plan was attempted (regardless of
        whether every step succeeded), or None if planning was skipped/
        declined/failed for any reason and the caller should proceed
        exactly as it did before this feature existed.

    Never raises. Every internal exception (planning unavailable, plan
    validation failure, unexpected error) is caught, logged, and turned
    into a None return -- indistinguishable to the caller from "planning
    correctly decided not to engage."
    """
    if not getattr(cfg, "PLANNING_ENABLED", True):
        return None

    if not isinstance(user_input, str) or not user_input.strip():
        return None

    if not should_attempt_plan(user_input):
        return None

    max_steps = int(getattr(cfg, "PLANNING_MAX_STEPS", 4))
    step_timeout_s = float(
        getattr(cfg, "PLANNING_STEP_TIMEOUT_S", getattr(cfg, "TOOL_CALLING_TIMEOUT_S", 3.0))
    )
    total_timeout_s = float(getattr(cfg, "PLANNING_TOTAL_TIMEOUT_S", 6.0))
    retry_enabled = bool(getattr(cfg, "PLANNING_STEP_RETRY_ENABLED", True))
    app_allowlist_enabled = bool(getattr(cfg, "APP_LAUNCH_ALLOWLIST_ENABLED", True))

    # Defensive sanity clamp in case a caller supplies an unvalidated cfg
    # object (e.g. a hand-built test double) with out-of-range values --
    # Config.validate() already enforces these bounds for the real
    # Config class, this is a second, cheap layer of protection so this
    # function's own timeout arithmetic below can never go negative or
    # divide-by-zero-adjacent.
    max_steps = max(1, max_steps)
    step_timeout_s = max(0.1, step_timeout_s)
    total_timeout_s = max(step_timeout_s, total_timeout_s)

    overall_start = time.monotonic()

    try:
        plan = propose_plan(
            user_input,
            model_name,
            cfg,
            max_steps=max_steps,
            timeout_s=step_timeout_s,
            allowed_apps=allowed_apps,
            app_allowlist_enabled=app_allowlist_enabled,
        )
    except PlanningUnavailableError as exc:
        logger.info(
            "Planning unavailable for input=%r: %s", user_input, exc
        )
        return None
    except PlanValidationError as exc:
        logger.info(
            "Planning declined for input=%r (invalid proposal): %s",
            user_input,
            exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001 -- absolute safety net
        logger.error(
            "Unexpected error during plan proposal for input=%r: %s",
            user_input,
            exc,
            exc_info=True,
        )
        return None

    if len(plan.steps) <= 1:
        # A "plan" that collapsed to a single step (or the model, despite
        # the trigger gate firing, only found one genuine action) offers
        # no benefit over the existing single-tool path and carries the
        # planning call's latency for nothing -- decline here so the
        # caller falls back to resolve_tool_call(), which is cheaper for
        # a single tool and already fully hardened/tested.
        logger.info(
            "Plan collapsed to %d step(s) for input=%r -- declining in "
            "favor of the single-tool path.",
            len(plan.steps),
            user_input,
        )
        return None

    # Whatever time the planning call itself consumed is deducted from
    # the execution phase's own budget, so the TOTAL time this function
    # can spend (proposal + execution combined) never exceeds
    # total_timeout_s by more than the unavoidable overhead of the
    # proposal call itself having already returned. Without this
    # deduction, a slow-but-successful planning call followed by a full
    # total_timeout_s execution window could make the combined latency
    # roughly double the configured budget.
    elapsed_on_proposal = time.monotonic() - overall_start
    execution_budget = max(0.5, total_timeout_s - elapsed_on_proposal)

    try:
        outcome = execute_plan(
            plan,
            dispatch,
            model_name=model_name,
            cfg=cfg,
            step_timeout_s=step_timeout_s,
            total_timeout_s=execution_budget,
            retry_enabled=retry_enabled,
            allowed_apps=allowed_apps,
            app_allowlist_enabled=app_allowlist_enabled,
        )
    except Exception as exc:  # noqa: BLE001 -- absolute safety net
        logger.error(
            "Unexpected error during plan execution for input=%r: %s",
            user_input,
            exc,
            exc_info=True,
        )
        return None

    total_elapsed = time.monotonic() - overall_start
    logger.info(
        "Plan run complete for input=%r: %d step(s), aborted=%s, "
        "proposal=%.2fs, execution=%.2fs, total=%.2fs",
        user_input,
        len(outcome.results),
        outcome.aborted,
        elapsed_on_proposal,
        outcome.elapsed_s,
        total_elapsed,
    )
    return outcome


def shutdown_planning_executors(wait: bool = False) -> None:
    """
    Shuts down every background thread pool this package owns (planner
    proposal calls, step-correction calls). Not required for normal
    long-running voice-assistant operation -- daemon-style background
    pools are fine to leave running for the life of the process -- but
    exposed as a single call for clean, deterministic teardown in tests
    and any future explicit application-shutdown sequence (mirroring the
    shutdown pattern already used elsewhere in this codebase, e.g.
    sara.orchestrator.network_utils._shutdown_network_executor()).
    """
    shutdown_planner_executor(wait=wait)
    shutdown_correction_executor(wait=wait)