"""
sara.core.planning.executor
Bounded execution loop for a validated Plan: per-step timeout, an
independent total-plan timeout, one self-correction retry per failed
step, and explicit, never-silent partial-success handling.

SECURITY NOTE: every step's arguments are re-validated via
schema.validate_tool_arguments() immediately before each dispatch
attempt -- including corrected arguments produced by the retry path
below -- so a "corrected" open_url/open_app argument can never bypass
the scheme/allowlist checks just because it came from a retry instead
of the original plan. See schema.py's module docstring for the full
rationale.

CONCURRENCY / TIMEOUT MODEL
------------------------------
- One dedicated single-worker ThreadPoolExecutor is created per
  execute_plan() call (NOT a module-level shared pool) and is shut down
  via a `finally` block with wait=False -- guaranteeing execute_plan()
  never blocks its return on a dispatch call that has already been
  abandoned due to timeout. A plain `with ...:` context manager is
  deliberately NOT used here: ThreadPoolExecutor.__exit__() calls
  shutdown(wait=True) by default, which would block until every
  submitted (including abandoned/timed-out) call actually finishes --
  directly defeating the "abandon on timeout, never wait past the
  bound" guarantee this module promises. shutdown(wait=False,
  cancel_futures=True) cancels anything still queued (not yet started)
  and detaches from anything already running, without waiting for it.
- `total_timeout_s` is treated as an absolute wall-clock deadline
  computed once, at the very start of execution. Every subsequent
  per-step or per-retry timeout is the MINIMUM of the step's own bound
  and whatever time remains against that deadline -- this is what makes
  the "N steps at their individual timeout can't silently exceed the
  total budget" guarantee actually hold, rather than just being true in
  the common case.
- A dispatch call that exceeds its timeout is abandoned (the underlying
  future/thread is left to finish or fail on its own, never awaited
  again) -- this matches the same "never block the caller past its
  bound" philosophy used throughout this codebase (tool_router.py,
  planner.py).
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from typing import Any, Callable, Dict, FrozenSet, List, Optional

from sara.core.llm.clients import _get_ollama_client

from .schema import (
    Plan,
    PlanStep,
    PlanOutcome,
    PlanValidationError,
    StepResult,
    StepStatus,
    validate_tool_arguments,
)

logger = logging.getLogger("sara.core.planning.executor")

# DispatchFn: given (tool_name, arguments) -> a human-readable result
# string, or raises on failure. Supplied by the caller (see
# sara.core.planning.__init__) so this module never needs to import
# sara.orchestrator.intent_handlers itself -- that would risk a
# circular import (intent_handlers -> planning -> intent_handlers).
DispatchFn = Callable[[str, Dict[str, Any]], str]

_CORRECTION_TOOL_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "corrected_step",
            "description": "Provide corrected arguments for the failed step.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["tool", "arguments"],
            },
        },
    }
]

# Correction calls get their own small bounded pool, separate from both
# the planner's executor and the per-plan step executor created inside
# execute_plan() below -- keeps the three concurrency domains (propose,
# dispatch, correct) from ever contending with each other.
_CORRECTION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="sara-plan-correct"
)


def _remaining_budget(start: float, total_timeout_s: float) -> float:
    """Returns seconds left against the absolute total-plan deadline (never negative)."""
    return max(0.0, total_timeout_s - (time.monotonic() - start))


def _request_step_correction(
    step: PlanStep,
    error_message: str,
    model_name: str,
    cfg: Any,
    timeout_s: float,
) -> Optional[Dict[str, Any]]:
    """
    Asks the model to correct ONE failed step's arguments, given the
    real error it produced. Returns a corrected arguments dict, or None
    if the correction call itself is unavailable/times out/errors, or
    the model's response is malformed in any way -- None is treated by
    the caller as "give up on this step," NEVER as "retry with the
    original broken arguments."

    Bounded via future.result(timeout=timeout_s); a timed-out correction
    call is abandoned (not awaited further) and treated as unavailable.
    """
    if timeout_s <= 0:
        return None

    client = _get_ollama_client(cfg)
    if not client:
        return None

    prompt = (
        f"Tool '{step.tool}' was called with arguments {step.arguments!r} "
        f"and failed with this error: {error_message}\n"
        "Call corrected_step with the SAME tool and corrected arguments "
        "that fix this specific problem. Do not change the tool name."
    )

    future = _CORRECTION_EXECUTOR.submit(
        client.chat,
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        tools=_CORRECTION_TOOL_SCHEMA,
        options={"num_predict": 150, "temperature": 0.2},
        keep_alive=getattr(cfg, "OLLAMA_KEEP_ALIVE", "30m"),
    )
    try:
        resp = future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        future.cancel()
        logger.info(
            "Step correction call for tool '%s' exceeded its %.1fs budget.",
            step.tool,
            timeout_s,
        )
        return None
    except Exception as exc:  # noqa: BLE001 -- degrade to "no correction available"
        logger.info(
            "Step correction call for tool '%s' failed (%s): %s",
            step.tool,
            type(exc).__name__,
            exc,
        )
        return None

    message = getattr(resp, "message", None)
    tool_calls = getattr(message, "tool_calls", None) if message is not None else None
    if not tool_calls:
        return None

    call = tool_calls[0]
    function = getattr(call, "function", None)
    function_name = getattr(function, "name", None) if function is not None else None
    if function_name != "corrected_step":
        return None

    raw_arguments = getattr(function, "arguments", None)
    if raw_arguments is None:
        return None

    arguments = dict(raw_arguments)
    corrected = arguments.get("arguments")
    return corrected if isinstance(corrected, dict) else None


def _dispatch_with_timeout(
    dispatch: DispatchFn,
    tool_name: str,
    arguments: Dict[str, Any],
    timeout_s: float,
    step_executor: concurrent.futures.ThreadPoolExecutor,
) -> str:
    """
    Runs one dispatch call bounded by `timeout_s` on the given executor.
    Raises on failure or timeout -- callers handle both identically as
    "this attempt failed." A timed-out call's future is abandoned (left
    to complete or fail on its own thread, never waited on again --
    including at executor shutdown time, see execute_plan()'s docstring)
    rather than force-killed, matching Python's cooperative threading
    model.
    """
    if timeout_s <= 0:
        raise TimeoutError(f"No time budget remaining to attempt tool '{tool_name}'.")
    future = step_executor.submit(dispatch, tool_name, arguments)
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise TimeoutError(
            f"Tool '{tool_name}' did not complete within {timeout_s:.1f}s."
        ) from exc


def execute_plan(
    plan: Plan,
    dispatch: DispatchFn,
    *,
    model_name: str,
    cfg: Any,
    step_timeout_s: float,
    total_timeout_s: float,
    retry_enabled: bool = True,
    allowed_apps: FrozenSet[str] = frozenset(),
    app_allowlist_enabled: bool = True,
) -> PlanOutcome:
    """
    Executes every step in `plan`, in order, subject to:

      - `step_timeout_s`: bounds each individual dispatch attempt (and
        each correction call).
      - `total_timeout_s`: an independent wall-clock deadline for the
        WHOLE plan (proposal time is NOT included here -- this timeout
        covers execution only, starting from this function's own entry),
        checked before starting each step and re-checked before every
        retry sub-step -- N steps at their individual timeout can never
        silently exceed this.
      - `retry_enabled`: if a step fails (raised exception or timeout at
        dispatch time), the model is shown the real error and given ONE
        chance to correct that step's arguments before it is marked
        failed. This only affects the single failing step, never the
        rest of the plan.
      - A step marked `depends_on_previous=True` whose predecessor
        FAILED or was itself SKIPPED is SKIPPED, not attempted with
        stale/guessed arguments -- this is the explicit partial-success
        policy: the plan continues past a failure, but never blindly
        executes a step that genuinely needed a result that never
        arrived. A skip propagates forward (a chain of 3 dependent
        steps after one failure all get skipped, not just the
        immediate next one).

    Every corrected-arguments dict (from the retry path) is re-validated
    through schema.validate_tool_arguments() before dispatch -- a
    "correction" can never bypass URL-scheme or app-allowlist checks.

    Never raises: any exception from `dispatch` itself, from the
    correction call, or from timeout handling is caught and turned into
    a StepResult with StepStatus.FAILED. The only way this function
    reports an abnormal exit is via `PlanOutcome.aborted=True` when the
    total timeout is reached mid-plan -- never via a raised exception.

    Never blocks its return on an abandoned (timed-out) dispatch call --
    the per-execution step_executor is shut down with wait=False, so a
    hung tool function cannot delay this function's return past
    total_timeout_s (see module docstring for why a plain `with`
    context manager cannot be used for this).
    """
    if not plan.steps:
        # Should be unreachable given schema.parse_plan_from_llm()'s own
        # "at least one step" guarantee, but defended here explicitly
        # rather than assumed, since Plan objects can in principle be
        # constructed directly (e.g. in tests) without going through the
        # parser.
        return PlanOutcome(
            results=(),
            aborted=False,
            abort_reason=None,
            elapsed_s=0.0,
            final_message="There was nothing to do.",
        )

    start = time.monotonic()
    results: List[StepResult] = []
    aborted = False
    abort_reason: Optional[str] = None
    # Tracks whether the immediately preceding step's outcome should
    # block a dependent successor -- set True on both FAILED and SKIPPED
    # so a chain of dependent steps after one failure all skip together.
    predecessor_blocked = False

    step_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="sara-plan-step"
    )
    try:
        for index, step in enumerate(plan.steps):
            remaining_before_step = _remaining_budget(start, total_timeout_s)
            if remaining_before_step <= 0:
                aborted = True
                abort_reason = (
                    f"Total plan timeout ({total_timeout_s:.1f}s) reached "
                    f"before step {index + 1} of {len(plan.steps)} could run."
                )
                logger.warning(abort_reason)
                break

            if step.depends_on_previous and predecessor_blocked:
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.SKIPPED,
                        error="Skipped: depended on a previous step that failed or was skipped.",
                    )
                )
                logger.info(
                    "Skipping step %d ('%s'): depended on a failed/skipped predecessor.",
                    index,
                    step.tool,
                )
                predecessor_blocked = True  # propagate the block down the chain
                continue

            this_step_timeout = min(step_timeout_s, remaining_before_step)

            attempts = 1
            first_error: Optional[str] = None
            try:
                output = _dispatch_with_timeout(
                    dispatch, step.tool, step.arguments, this_step_timeout, step_executor
                )
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.SUCCESS,
                        output=output,
                        attempts=attempts,
                    )
                )
                predecessor_blocked = False
                logger.info("Step %d ('%s') succeeded on first attempt.", index, step.tool)
                continue
            except Exception as exc:  # noqa: BLE001
                first_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Step %d ('%s') failed on first attempt: %s",
                    index,
                    step.tool,
                    first_error,
                )

            if not retry_enabled:
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.FAILED,
                        error=first_error,
                        attempts=attempts,
                    )
                )
                predecessor_blocked = True
                continue

            remaining_for_retry = _remaining_budget(start, total_timeout_s)
            if remaining_for_retry <= 0:
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.FAILED,
                        error=first_error,
                        attempts=attempts,
                    )
                )
                aborted = True
                abort_reason = (
                    "Total plan timeout reached before a retry could be "
                    f"attempted for step {index + 1}."
                )
                logger.warning(abort_reason)
                predecessor_blocked = True
                break

            correction_timeout = min(step_timeout_s, remaining_for_retry)
            corrected_arguments = _request_step_correction(
                step, first_error, model_name, cfg, correction_timeout
            )
            attempts += 1

            if corrected_arguments is None:
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.FAILED,
                        error=first_error,
                        attempts=attempts,
                    )
                )
                predecessor_blocked = True
                continue

            try:
                validated_corrected = validate_tool_arguments(
                    step.tool,
                    corrected_arguments,
                    allowed_apps=allowed_apps,
                    app_allowlist_enabled=app_allowlist_enabled,
                )
            except PlanValidationError as exc:
                logger.warning(
                    "Step %d ('%s') retry rejected: corrected arguments "
                    "failed validation: %s",
                    index,
                    step.tool,
                    exc,
                )
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.FAILED,
                        error=f"{first_error}; retry rejected (invalid corrected arguments): {exc}",
                        attempts=attempts,
                    )
                )
                predecessor_blocked = True
                continue

            remaining_for_retry_dispatch = _remaining_budget(start, total_timeout_s)
            if remaining_for_retry_dispatch <= 0:
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.FAILED,
                        error=first_error,
                        attempts=attempts,
                    )
                )
                aborted = True
                abort_reason = (
                    "Total plan timeout reached before the retry dispatch "
                    f"could run for step {index + 1}."
                )
                logger.warning(abort_reason)
                predecessor_blocked = True
                break

            retry_dispatch_timeout = min(step_timeout_s, remaining_for_retry_dispatch)
            try:
                output = _dispatch_with_timeout(
                    dispatch,
                    step.tool,
                    validated_corrected,
                    retry_dispatch_timeout,
                    step_executor,
                )
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.SUCCESS,
                        output=output,
                        attempts=attempts,
                    )
                )
                predecessor_blocked = False
                logger.info(
                    "Step %d ('%s') succeeded after 1 correction retry.",
                    index,
                    step.tool,
                )
            except Exception as exc:  # noqa: BLE001
                second_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Step %d ('%s') failed again after retry: %s",
                    index,
                    step.tool,
                    second_error,
                )
                results.append(
                    StepResult(
                        step=step,
                        status=StepStatus.FAILED,
                        error=f"{first_error}; retry also failed: {second_error}",
                        attempts=attempts,
                    )
                )
                predecessor_blocked = True
    finally:
        # CRITICAL: wait=False + cancel_futures=True. Do NOT use a `with`
        # context manager for step_executor -- ThreadPoolExecutor's
        # __exit__ defaults to shutdown(wait=True), which would block
        # this function's return until every abandoned/timed-out dispatch
        # call actually finishes running (e.g. a genuinely hung tool
        # function), silently reintroducing the exact unbounded wait this
        # module's per-step/total timeouts exist to prevent. This shuts
        # the pool down immediately: anything still queued (not yet
        # started) is cancelled, and anything already running is detached
        # from -- it keeps running to completion on its own thread, but
        # this function never waits for or observes that again.
        step_executor.shutdown(wait=False, cancel_futures=True)

    elapsed_s = time.monotonic() - start
    final_message = _build_final_message(tuple(results), aborted, abort_reason)

    success_count = sum(1 for r in results if r.status == StepStatus.SUCCESS)
    failed_count = sum(1 for r in results if r.status == StepStatus.FAILED)
    skipped_count = sum(1 for r in results if r.status == StepStatus.SKIPPED)

    logger.info(
        "Plan execution finished: %d step(s) (%d success, %d failed, "
        "%d skipped), aborted=%s, elapsed=%.2fs",
        len(results),
        success_count,
        failed_count,
        skipped_count,
        aborted,
        elapsed_s,
    )

    return PlanOutcome(
        results=tuple(results),
        aborted=aborted,
        abort_reason=abort_reason,
        elapsed_s=elapsed_s,
        final_message=final_message,
    )


def _build_final_message(
    results: tuple, aborted: bool, abort_reason: Optional[str]
) -> str:
    """
    Builds the ready-to-speak summary of a completed (or aborted) plan
    run WITHOUT another LLM call -- purely string composition from the
    already-known step outcomes, to avoid spending an extra round trip
    just to describe what already happened.

    Ordering: successful step outputs first (in original plan order, so
    the summary reads naturally), then a single consolidated failure
    note, then a single consolidated skip note, then an abort note if
    applicable -- never one sentence per failed/skipped step, to keep
    the spoken summary from becoming a wall of text on a badly-failing
    plan.
    """
    if not results:
        return abort_reason or "I couldn't complete that -- nothing ran in time."

    successes = [r for r in results if r.status == StepStatus.SUCCESS and r.output]
    failures = [r for r in results if r.status == StepStatus.FAILED]
    skipped = [r for r in results if r.status == StepStatus.SKIPPED]

    parts: List[str] = [r.output for r in successes if r.output]

    if failures:
        failed_tools = ", ".join(r.step.tool for r in failures)
        if len(failures) == 1:
            parts.append(f"I couldn't complete the {failed_tools} step.")
        else:
            parts.append(f"I couldn't complete these steps: {failed_tools}.")

    if skipped:
        skipped_tools = ", ".join(r.step.tool for r in skipped)
        parts.append(
            f"I skipped {skipped_tools} since an earlier step it depended on didn't succeed."
        )

    if aborted and abort_reason:
        parts.append("I had to stop early -- it was taking too long.")

    if not parts:
        return "I wasn't able to complete that request."

    return " ".join(parts)


def shutdown_correction_executor(wait: bool = False) -> None:
    """
    Shuts down the module-level correction-call thread pool. Not
    required for normal long-running operation, but exposed for clean
    teardown in tests and any future explicit application-shutdown
    sequence.
    """
    _CORRECTION_EXECUTOR.shutdown(wait=wait, cancel_futures=not wait)