"""
sara.core.planning.planner
Makes the single, bounded "propose a full multi-step plan up front" LLM
call and turns its response into a validated Plan.

This is intentionally ONE round trip for the whole plan (not one call
per step) -- multi-step latency stays close to a single-tool call's
latency, and per-step corrections/confirmations during execution are
handled separately in sara.core.planning.executor, only when a step's
depends_on_previous flag or a failure genuinely requires it.

PERFORMANCE / ROBUSTNESS NOTES
---------------------------------
- A dedicated bounded ThreadPoolExecutor is used (separate from
  tool_router.py's own executor) so planning load never contends with
  or is starved by ordinary single-tool-call load, and vice versa.
- The tool catalog embedded in the system prompt is built from
  sara.core.tool_router.TOOLS_SCHEMA directly (not duplicated), so the
  planner can never drift out of sync with the tools the executor is
  actually able to dispatch.
- The system prompt is rebuilt per max_steps value rather than cached
  globally, since max_steps is config-tunable and could differ between
  calls in a test/multi-config context; the string-formatting cost is
  negligible relative to the LLM round-trip itself.
- All exceptions from the underlying `ollama` client are caught and
  normalized into either PlanningUnavailableError (infrastructure
  problem: no client, timeout, transport error) or PlanValidationError
  (the call succeeded but its content was unusable) -- callers only
  ever need to handle these two exception types, never raw ollama/
  concurrent.futures exceptions.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Any, FrozenSet, List, Optional

from sara.core.llm.clients import _get_ollama_client
from sara.core.tool_router import TOOL_NAME_TO_INTENT, TOOLS_SCHEMA

from .schema import Plan, PlanValidationError, parse_plan_from_llm

logger = logging.getLogger("sara.core.planning.planner")


class PlanningUnavailableError(Exception):
    """
    Raised when the planning LLM call itself could not be completed --
    the Ollama client isn't available, the call timed out, or it raised
    for any other infrastructure reason. Distinct from
    PlanValidationError (which means "the call completed but the content
    was bad") purely for clearer logging; callers upstream treat both
    identically -- fall back to the existing single-tool path.
    """


# Bounded-size executor so a slow/hung planning call can be abandoned via
# future.result(timeout=...) without ever blocking the calling (voice
# loop) thread indefinitely -- same shape as tool_router.py's own
# _TOOL_CALL_EXECUTOR, kept separate so planning load never contends
# with the existing single-tool-call executor's two workers. Two workers
# is plenty: planning only runs for messages the cheap trigger gate
# already flagged as multi-step, which is a small minority of traffic.
_PLANNER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="sara-planner"
)

# Guards lazy, one-time construction of the propose_plan function schema
# below (its `parameters` block is static, but built lazily so importing
# this module never pays the (tiny) construction cost unless planning is
# actually used).
_schema_lock = threading.Lock()
_plan_tool_schema_cache: Optional[List[dict]] = None


def _get_plan_tool_schema() -> List[dict]:
    """
    Returns the (lazily constructed, cached) `propose_plan` function
    schema passed to Ollama's `tools=` parameter. Thread-safe
    double-checked construction -- this is read-only after first build,
    so no lock is needed on the hot path once populated.
    """
    global _plan_tool_schema_cache
    if _plan_tool_schema_cache is not None:
        return _plan_tool_schema_cache
    with _schema_lock:
        if _plan_tool_schema_cache is None:
            _plan_tool_schema_cache = [
                {
                    "type": "function",
                    "function": {
                        "name": "propose_plan",
                        "description": (
                            "Propose an ordered sequence of tool calls to "
                            "fully satisfy a request that needs more than "
                            "one action."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "steps": {
                                    "type": "array",
                                    "description": (
                                        "Ordered list of steps, first to last."
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "tool": {
                                                "type": "string",
                                                "description": (
                                                    "Exact tool name from "
                                                    "the available tools "
                                                    "list."
                                                ),
                                            },
                                            "arguments": {
                                                "type": "object",
                                                "description": (
                                                    "Arguments for this "
                                                    "tool call."
                                                ),
                                            },
                                            "depends_on_previous": {
                                                "type": "boolean",
                                                "description": (
                                                    "True only if this "
                                                    "step's arguments "
                                                    "genuinely cannot be "
                                                    "decided without the "
                                                    "previous step's real "
                                                    "result."
                                                ),
                                            },
                                        },
                                        "required": ["tool", "arguments"],
                                    },
                                }
                            },
                            "required": ["steps"],
                        },
                    },
                }
            ]
        return _plan_tool_schema_cache


def _build_planner_system_prompt(max_steps: int) -> str:
    """
    Builds the planning system prompt, listing every tool currently
    available via sara.core.tool_router.TOOLS_SCHEMA so the model is
    never guessing at what exists -- this list is generated dynamically
    from the same schema tool_router.py's own single-tool resolver uses,
    so the two can never silently drift apart if a tool is added or
    removed there in the future.
    """
    tool_lines: List[str] = []
    for entry in TOOLS_SCHEMA:
        fn = entry.get("function", {})
        name = fn.get("name")
        description = fn.get("description", "")
        if name:
            tool_lines.append(f"- {name}: {description}")
    tools_block = "\n".join(tool_lines)

    return (
        "You are a planning engine for a voice assistant. The user's "
        "message may require MORE THAN ONE tool call to fully satisfy. "
        "Read the message and call propose_plan with an ORDERED list of "
        "steps, first to last, using ONLY the exact tool names below:\n"
        f"{tools_block}\n\n"
        "Rules:\n"
        f"- Propose at most {max_steps} steps. If the request only needs "
        "one tool, propose exactly one step.\n"
        "- Never invent a tool name that isn't in the list above.\n"
        "- Never repeat the exact same tool with the exact same "
        "arguments as an earlier step in the same plan.\n"
        "- Set depends_on_previous to true ONLY when a step's arguments "
        "genuinely cannot be decided without seeing the previous step's "
        "real result. Most steps should be false.\n"
        "- The user's message may be in English, Hindi, or a Hindi-"
        "English mix (Hinglish) -- understand it in whichever language "
        "it's written, but keep tool names and argument keys exactly as "
        "given above.\n"
        "- Always call propose_plan, even for a single-tool request -- "
        "do not call any other function, and do not reply with plain "
        "text instead of a function call."
    )


def _call_planner_llm(
    user_input: str, model_name: str, cfg: Any, max_steps: int
) -> Any:
    """
    Makes the actual (blocking) Ollama tool-calling request. Runs on the
    bounded executor above -- callers must never call this directly on
    the voice-loop thread.

    Returns the raw `steps` payload from the model's propose_plan call.
    Raises PlanningUnavailableError if the client is unavailable, the
    call transport-fails, or the model didn't call propose_plan at all.
    Raises PlanValidationError if it called a DIFFERENT function than
    propose_plan (should not normally happen given `tools=` restricts
    the model to the one schema offered, but handled explicitly rather
    than assumed away, since local/quantized models are known to
    sometimes ignore tool constraints).
    """
    client = _get_ollama_client(cfg)
    if not client:
        raise PlanningUnavailableError("Ollama client not available.")

    if not isinstance(user_input, str) or not user_input.strip():
        raise PlanningUnavailableError("Empty user_input passed to planner.")

    try:
        resp = client.chat(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": _build_planner_system_prompt(max_steps),
                },
                {"role": "user", "content": user_input},
            ],
            tools=_get_plan_tool_schema(),
            # Low, fixed temperature (not OLLAMA_TEMPERATURE) on purpose: this
            # call produces structured tool-call JSON, not conversational
            # text, and needs to be as deterministic/correct as possible
            # regardless of the chat-persona temperature setting.
            options={"num_predict": 400, "temperature": 0.2},
            keep_alive=getattr(cfg, "OLLAMA_KEEP_ALIVE", "30m"),
        )
    except Exception as exc:  # noqa: BLE001 -- any transport/client error
        raise PlanningUnavailableError(
            f"Ollama chat() call failed: {type(exc).__name__}: {exc}"
        ) from exc

    message = getattr(resp, "message", None)
    tool_calls = getattr(message, "tool_calls", None) if message is not None else None
    if not tool_calls:
        raise PlanningUnavailableError(
            "Model did not propose a plan (no tool call returned)."
        )

    call = tool_calls[0]
    function = getattr(call, "function", None)
    function_name = getattr(function, "name", None) if function is not None else None

    if function_name != "propose_plan":
        raise PlanValidationError(
            f"Model called unexpected function {function_name!r} instead "
            f"of propose_plan."
        )

    raw_arguments = getattr(function, "arguments", None)
    if raw_arguments is None:
        raise PlanValidationError("propose_plan call had no arguments.")

    arguments = dict(raw_arguments)
    return arguments.get("steps")


def propose_plan(
    user_input: str,
    model_name: str,
    cfg: Any,
    *,
    max_steps: int,
    timeout_s: float,
    allowed_apps: FrozenSet[str] = frozenset(),
    app_allowlist_enabled: bool = True,
) -> Plan:
    """
    Public entry point: makes ONE bounded Ollama tool-calling request
    proposing a full ordered plan, then parses and validates it via
    schema.parse_plan_from_llm().

    Parameters
    ----------
    user_input:
        The raw, unmatched ("chat" intent) user command -- may be
        English, Hindi, or Hinglish.
    model_name:
        The Ollama model name to use for the planning call (same model
        the caller's single-tool resolve_tool_call() already uses).
    cfg:
        The Config class (or a compatible test double) -- read via
        getattr() everywhere so a partially-populated test config never
        raises AttributeError.
    max_steps:
        Hard cap on the number of steps this plan may contain, enforced
        both in the prompt (soft guidance to the model) and again in
        parse_plan_from_llm() (hard truncation regardless of what the
        model actually proposed).
    timeout_s:
        Wall-clock bound for the entire LLM call, enforced via
        future.result(timeout=...) -- a hung Ollama request is
        guaranteed to be abandoned (not killed, just no longer awaited)
        after this many seconds.
    allowed_apps / app_allowlist_enabled:
        Forwarded to schema validation for any open_app/close_app step
        the plan proposes.

    Raises PlanningUnavailableError if the LLM call itself couldn't be
    made, timed out, or errored for any infrastructure reason.
    Raises PlanValidationError if the call completed but its content
    couldn't be turned into at least one valid step.

    Both exceptions mean the same thing to every caller in this
    package: decline planning and fall back to the existing single-tool
    path. This function never returns a partially-broken Plan -- either
    a fully validated Plan comes back, or an exception is raised.
    """
    if max_steps < 1:
        raise PlanValidationError(f"max_steps must be >= 1, got {max_steps}.")
    if timeout_s <= 0:
        raise PlanningUnavailableError(f"timeout_s must be > 0, got {timeout_s}.")

    allowed_tool_names = frozenset(TOOL_NAME_TO_INTENT.keys())

    future = _PLANNER_EXECUTOR.submit(
        _call_planner_llm, user_input, model_name, cfg, max_steps
    )
    try:
        raw_steps = future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError as exc:
        # The submitted call keeps running in the background thread pool
        # until it naturally completes/fails -- we simply stop waiting on
        # it. This mirrors tool_router.py's own timeout-handling
        # philosophy (never block the caller, let the orphaned call
        # finish silently in the background).
        future.cancel()
        raise PlanningUnavailableError(
            f"Planning call exceeded its {timeout_s:.1f}s budget."
        ) from exc
    except (PlanningUnavailableError, PlanValidationError):
        raise
    except Exception as exc:  # noqa: BLE001 -- absolute safety net
        raise PlanningUnavailableError(
            f"Planning call failed unexpectedly: {type(exc).__name__}: {exc}"
        ) from exc

    plan = parse_plan_from_llm(
        raw_steps,
        allowed_tools=allowed_tool_names,
        max_steps=max_steps,
        allowed_apps=allowed_apps,
        app_allowlist_enabled=app_allowlist_enabled,
    )
    logger.info(
        "Plan proposed: %d step(s) survived validation (of %d proposed) "
        "for input=%r using model=%r",
        len(plan.steps),
        plan.proposed_step_count,
        user_input,
        model_name,
    )
    return plan


def shutdown_planner_executor(wait: bool = False) -> None:
    """
    Shuts down the module-level planner thread pool. Not required for
    normal operation (daemon-style usage is fine for a long-running
    voice assistant process), but exposed for clean teardown in tests
    and for any future explicit application-shutdown sequence that wants
    to release every background executor deterministically.
    """
    _PLANNER_EXECUTOR.shutdown(wait=wait, cancel_futures=not wait)