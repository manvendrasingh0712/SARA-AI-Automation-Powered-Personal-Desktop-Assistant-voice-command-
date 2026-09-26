"""
sara.orchestrator.dispatcher

The _INTENT_HANDLERS registry (and register_handler(), used by
sara/skills/__init__.py's plugin auto-discovery), the pending-
destructive-action confirmation gate for multi-step plan steps
(_plan_step_confirmation_needed), the plan-execution dispatch callback
(_build_plan_dispatch_fn), and _handle_command() itself -- the single
entry point that routes one turn of user input all the way through
fast-path regex intents, zero-arg system actions, the multi-step
planner, and the single-tool LLM resolver, down to a plain chat
fallback. Split out of the former monolithic intent_handlers.py -- see
that module's docstring for the full design rationale behind each of
these pieces, in particular "LLM ROUTING IS MUTUALLY EXCLUSIVE" and
"ACTION AUDIT LOG".
"""
import time
import sqlite3
from concurrent.futures import TimeoutError as _FutureTimeoutError

from config import Config

from sara.core.intent import detect_intent
from sara.core.unmatched_log import log_unmatched
from sara.tools import system as system_tools

from .calc_utils import _safe_calc
from .text_utils import _extract_name
from .network_utils import _call_with_timeout
from .state import TURN_STATE
from .tts_worker import TTSWorker
from ._constants import _SLEEP_WORDS, _FORGET_WORDS

from ._shared_state import (
    _HAS_RAG,
    resolve_tool_call,
    build_fake_match,
    TOOL_NAME_TO_INTENT,
    try_plan_and_execute,
    PlanStepRequiresConfirmation,
    _RISKY_APP_KEYWORDS,
    _RISKY_SERVICE_KEYWORDS,
    _EXIT_WORDS,
    _CONFIRM_YES_WORDS,
    _CONFIRM_NO_WORDS,
    _CONFIRM_PENDING_TTL_S,
)
from .command_helpers import (
    _quick,
    _ack,
    _activity_label,
    _run_activity,
    _matches_phrase_set,
    _is_risky,
    _LOW_CONFIDENCE_CONFIRM_ACTIONS,
    _LOW_CONFIDENCE_CONFIRM_PHRASES,
    _STT_CONFIDENCE_CONFIRM_THRESHOLD,
    _LikelyMisfireReply,
    _log_action,
)
from .route_chat import _route_chat_message, _retry_via_tool_router

from .context_tracking import _h_followup_query
from .handlers.timers import (
    _h_reminder_add,
    _h_reminder_list,
    _h_reminder_cancel,
    _h_set_timer,
    _h_set_alarm,
    _h_start_stopwatch,
    _h_stop_stopwatch,
    _h_lap_stopwatch,
)
from .handlers.notes import (
    _h_take_note,
    _h_read_notes,
    _h_clear_notes,
    _h_add_todo,
    _h_list_todos,
    _h_complete_todo,
    _h_delete_todo,
)
from .handlers.media import (
    _h_play_youtube,
    _h_play_next_youtube,
    _h_play_spotify,
    _h_web_search,
    _h_summarize_url,
    _h_open_url,
)
from .handlers.system import (
    _h_clipboard_read,
    _h_clipboard_write,
    _h_screenshot_describe,
    _h_calculator,
    _h_system_info,
    _h_set_volume,
    _h_set_brightness,
    _h_mute,
    _h_unmute,
    _h_open_app,
    _h_close_app,
    _h_typing_text,
    _h_press_key,
    _h_find_file,
    _h_open_file,
    _h_start_service,
    _h_stop_service,
    _h_restart_application,
    _h_switch_to_application,
    _h_move_resize_window,
    _h_always_on_top,
    _h_fullscreen,
    _h_notify_on_file,
)
from .handlers.info import (
    _h_weather,
    _h_news,
    _h_time_query,
    _h_date_query,
)
from .handlers.memory_intents import (
    _h_memory_recall,
    _h_memory_forget_specific,
    _h_memory_forget_all,
)
from .handlers.calendar import (
    _h_calendar_today,
    _h_calendar_create,
)
from .handlers.automation import _h_run_routine
from .handlers.misc import (
    _h_why_proactive,
    _h_why_decision,
    _h_undo_setting_change,
    _h_switch_mode,
)

_INTENT_HANDLERS = {
    "switch_mode": _h_switch_mode,
    "undo_setting_change": _h_undo_setting_change,
    "reminder_add": _h_reminder_add,
    "reminder_list": _h_reminder_list,
    "reminder_cancel": _h_reminder_cancel,
    "set_timer": _h_set_timer,
    "set_alarm": _h_set_alarm,
    "start_stopwatch": _h_start_stopwatch,
    "stop_stopwatch": _h_stop_stopwatch,
    "lap_stopwatch": _h_lap_stopwatch,
    "take_note": _h_take_note,
    "read_notes": _h_read_notes,
    "clear_notes": _h_clear_notes,
    "add_todo": _h_add_todo,
    "list_todos": _h_list_todos,
    "complete_todo": _h_complete_todo,
    "delete_todo": _h_delete_todo,
    "clipboard_read": _h_clipboard_read,
    "clipboard_write": _h_clipboard_write,
    "screenshot_describe": _h_screenshot_describe,
    "weather": _h_weather,
    "news": _h_news,
    "followup_query": _h_followup_query,
    "play_youtube": _h_play_youtube,
    "play_next_youtube": _h_play_next_youtube,
    "play_spotify": _h_play_spotify,
    "web_search": _h_web_search,
    "summarize_url": _h_summarize_url,
    "open_url": _h_open_url,
    "calculator": _h_calculator,
    "system_info": _h_system_info,
    "set_volume": _h_set_volume,
    "set_brightness": _h_set_brightness,
    "mute": _h_mute,
    "unmute": _h_unmute,
    "open_app": _h_open_app,
    "close_app": _h_close_app,
    "typing_text": _h_typing_text,
    "press_key": _h_press_key,
    "find_file": _h_find_file,
    "open_file": _h_open_file,
    "start_service": _h_start_service,
    "stop_service": _h_stop_service,
    "restart_application": _h_restart_application,
    "switch_to_application": _h_switch_to_application,
    "move_window": _h_move_resize_window,
    "resize_window": _h_move_resize_window,
    "always_on_top": _h_always_on_top,
    "toggle_fullscreen": _h_fullscreen,
    "time_query": _h_time_query,
    "date_query": _h_date_query,
    "why_proactive": _h_why_proactive,
    "why_decision": _h_why_decision,
    "memory_recall": _h_memory_recall,
    "memory_forget_specific": _h_memory_forget_specific,
    "memory_forget_all": _h_memory_forget_all,
    "calendar_today": _h_calendar_today,
    "calendar_create": _h_calendar_create,
    "run_routine": _h_run_routine,
    "notify_on_file": _h_notify_on_file,
}


def register_handler(name: str, fn) -> None:
    """
    Registers (or replaces) the handler function for intent `name` —
    the sibling of sara.core.intent.register_intent(), used by
    sara/skills/__init__.py's plugin auto-discovery so a new skill file
    can wire up its own handle() without editing this file's
    _INTENT_HANDLERS table by hand. `fn` must accept (match, ctx) and
    return a string (or None to fall through, same contract as every
    handler above).
    """
    _INTENT_HANDLERS[name] = fn


def _plan_step_confirmation_needed(mapped_intent: str, fake_match):
    """
    Mirrors _handle_command()'s existing pending-confirmation gate (see
    that function's "Pending destructive-action confirmation" block)
    for a step a multi-step PLAN is about to dispatch -- so a planned
    close_app/stop_service on a risky target, forget_all_memories, or
    anything in _LOW_CONFIDENCE_CONFIRM_ACTIONS can never auto-execute
    just because it arrived via the planner instead of a single command.
    Reuses _is_risky()/_RISKY_APP_KEYWORDS/_RISKY_SERVICE_KEYWORDS/
    _LOW_CONFIDENCE_CONFIRM_ACTIONS/_LOW_CONFIDENCE_CONFIRM_PHRASES
    exactly as-is -- no separate/duplicated risk logic.

    Returns (action, target, prompt) if this step needs confirmation
    before running, or None if it's safe to dispatch immediately.
    """
    if mapped_intent == "close_app":
        target = (
            fake_match.group(1).strip()
            if fake_match and fake_match.lastindex
            else ""
        )
        if target and _is_risky(target, _RISKY_APP_KEYWORDS):
            return (
                "close_app",
                target,
                f"{target} is a system app -- are you sure you want to close it? Say yes or cancel.",
            )
        return None

    if mapped_intent == "stop_service":
        target = (
            fake_match.group(1).strip()
            if fake_match and fake_match.lastindex
            else ""
        )
        if target and _is_risky(target, _RISKY_SERVICE_KEYWORDS):
            return (
                "stop_service",
                target,
                f"{target} looks like a core system service -- are you sure you want to stop it? Say yes or cancel.",
            )
        return None

    if mapped_intent == "memory_forget_all":
        return (
            "forget_all_memories",
            None,
            "This will permanently delete everything I've remembered about you "
            "long-term -- are you sure? Say yes or cancel.",
        )

    if mapped_intent in _LOW_CONFIDENCE_CONFIRM_ACTIONS:
        phrase = _LOW_CONFIDENCE_CONFIRM_PHRASES.get(
            mapped_intent, mapped_intent.replace("_", " ")
        )
        return (
            mapped_intent,
            None,
            f"Are you sure you want to {phrase}? Say yes or cancel.",
        )

    return None


def _build_plan_dispatch_fn(ctx: dict):
    """
    Builds the DispatchFn callback sara.core.planning.try_plan_and_execute()
    needs to actually execute a proposed plan's steps.

    Kept as its own small factory (rather than inlined in _handle_command
    below) so its contract is easy to unit-test in isolation: given a
    tool name from TOOL_NAME_TO_INTENT and a validated arguments dict, it
    builds a fake regex match via tool_router.build_fake_match() (the
    exact same mechanism the existing single-tool LLM resolver already
    uses) and calls the matching entry in _INTENT_HANDLERS -- i.e. a
    planned step reaches the real tool function through IDENTICAL code
    to a fast-path or single-tool-resolved command. There is no separate
    "plan execution" code path for the tool functions themselves; only
    the decision of WHICH tools to call and in WHAT order is new.

    SAFETY GATE (NEW): before calling the real handler, _dispatch() below
    checks _plan_step_confirmation_needed() -- the SAME risky-action gate
    _handle_command()'s single-command path already enforces (close_app/
    stop_service on a risky target, forget_all_memories, or anything in
    _LOW_CONFIDENCE_CONFIRM_ACTIONS). If the step needs confirmation, it
    raises sara.core.planning.executor.PlanStepRequiresConfirmation
    instead of dispatching -- execute_plan() catches that specifically,
    never retries it, and aborts the plan so _handle_command() can arm
    the same confirm_state/_CONFIRM_YES_WORDS mechanism already used for
    a single risky command.

    Raises RuntimeError (never returns None) on any failure -- the
    executor in sara.core.planning.executor treats any raised exception
    from this callback identically to a tool function raising directly,
    triggering its existing retry/skip/partial-success logic (except
    PlanStepRequiresConfirmation, which it handles separately -- see
    that exception's docstring).

    NOTE: steps executed through this callback are NOT written to the
    action_log audit table -- see this module's docstring ("KNOWN SCOPE
    LIMIT") for why.
    """

    def _dispatch(tool_name: str, tool_args: dict) -> str:
        mapped_intent = TOOL_NAME_TO_INTENT.get(tool_name)
        if not mapped_intent:
            raise RuntimeError(f"Unknown tool '{tool_name}' -- no mapped intent.")
        tool_handler = _INTENT_HANDLERS.get(mapped_intent)
        if tool_handler is None:
            raise RuntimeError(f"No handler registered for intent '{mapped_intent}'.")
        fake_match = build_fake_match(tool_name, tool_args)
        if PlanStepRequiresConfirmation is not None:
            confirmation = _plan_step_confirmation_needed(mapped_intent, fake_match)
            if confirmation is not None:
                action, target, prompt = confirmation
                raise PlanStepRequiresConfirmation(action, target, prompt)
        result = tool_handler(fake_match, ctx)
        if result is None:
            raise RuntimeError(
                f"Handler for intent '{mapped_intent}' (tool '{tool_name}') "
                f"returned no result."
            )
        return result

    # Audit hook: sara.core.planning.executor.execute_plan() reads this to
    # write one action_log entry per plan step. None is a silent no-op.
    _dispatch.audit_db = ctx.get("db")

    # Live-progress hook: execute_plan() calls this at plan start/each
    # step/plan end so the GUI can show a running plan card. Never raises
    # -- a GUI push must never break plan execution.
    def _on_plan_event(stage, payload):
        try:
            ctx["ui_update"]("plan_progress", stage, payload)
        except Exception as e:
            print(f"[PlanProgress] event push failed (non-fatal): {e}")
    _dispatch.on_event = _on_plan_event

    return _dispatch


def _handle_command(
    user_input,
    brain,
    tts: TTSWorker,
    ears,
    db,
    reminders,
    vision,
    ui_update,
    volume_state: dict,
    notes_memory=None,
    playback_state: dict = None,
    confirm_state: dict = None,
    context_state: dict = None,
    stt_confidence: float = 1.0,
    session_control: dict = None,
) -> str:
    if playback_state is None:
        playback_state = {}
    if confirm_state is None:
        confirm_state = {}
    if context_state is None:
        context_state = {}
    if session_control is None:
        session_control = {}

    # Cancellation: begin() is called by the CALLER (core.py / core_wiring.py).
    cancel_event = TURN_STATE.current_event()
    if cancel_event.is_set():
        return ""

    # SESSION-CONTROL FIX: exit/sleep/forget-memory/"my name is X" used to
    # be checked only in core_wiring.py's run_sara_logic() while-loop,
    # before _handle_command() was ever called -- so typing "exit" or
    # "my name is Priya" in the GUI (send_text_command -> _handle_command
    # directly) silently did nothing, only voice input honored them. Now
    # both callers go through here, so behavior matches for both.
    #
    # These 4 checks stay ahead of the pending-confirmation gate below on
    # purpose (same order as before the move): if Sara just asked "are you
    # sure?" and the user instead says "never mind" (a _SLEEP_WORDS
    # phrase) or "my name is Sam", that takes priority over re-parsing it
    # as a yes/cancel answer to the stale confirmation.
    lowered = (user_input or "").lower().strip()

    if _matches_phrase_set(lowered, _EXIT_WORDS):
        session_control["action"] = "exit"
        farewell = "Shutting down. Goodbye!"
        ui_update("status", "speaking")
        tts.speak(farewell, fast=True)
        return farewell

    if _matches_phrase_set(lowered, _SLEEP_WORDS):
        session_control["action"] = "sleep"
        reply = "Okay, going back to sleep."
        ui_update("status", "speaking")
        tts.speak(reply, fast=True)
        return reply

    if _matches_phrase_set(lowered, _FORGET_WORDS):
        ui_update("status", "thinking")
        brain.clear_memory()
        reply = "Done, I've cleared our conversation history."
        ui_update("status", "speaking")
        tts.speak(reply, fast=True)
        return reply

    name = _extract_name(user_input)
    if name:
        ui_update("status", "thinking")
        db.set_user_name(name)
        brain.set_user_name(name)
        reply = f"Nice to meet you, {name}!"
        ui_update("status", "speaking")
        tts.speak(reply, fast=True)
        return reply

    ctx = {
        "brain": brain,
        "tts": tts,
        "ears": ears,
        "db": db,
        "reminders": reminders,
        "vision": vision,
        "ui_update": ui_update,
        "volume_state": volume_state,
        "user_input": user_input,
        "notes_memory": notes_memory,
        "playback_state": playback_state,
        "confirm_state": confirm_state,
        "context_state": context_state,
        "stt_confidence": stt_confidence,
    }

    # ── Pending destructive-action confirmation (close_app / stop_service
    # on something risky, OR forget_all_memories) takes priority over
    # normal intent detection -- if Sara just asked "are you sure?", this
    # turn's job is to answer that, not to be re-parsed as a brand-new
    # command. Expires after _CONFIRM_PENDING_TTL_S so a stale "yes"
    # minutes later doesn't accidentally trigger an old, forgotten action.
    pending = confirm_state.get("pending")
    if pending:
        if time.time() > pending.get("expires_at", 0):
            confirm_state.pop("pending", None)
        else:
            reply = (user_input or "").strip().lower()
            if reply in _CONFIRM_YES_WORDS:
                confirm_state.pop("pending", None)
                action, target = pending["action"], pending["target"]
                _ack(ctx)
                if action == "close_app":
                    label = _activity_label(target)
                    result = _run_activity(
                        ctx, "app", f"Closing {label}", f"{label} closed", f"Couldn't close {label}",
                        lambda: _call_with_timeout(
                            system_tools.close_application, target, tool_name="close_application"
                        ),
                    )
                elif action == "stop_service":
                    label = _activity_label(target)
                    result = _run_activity(
                        ctx, "service", f"Stopping {label}", f"{label} stopped", f"Couldn't stop {label}",
                        lambda: _call_with_timeout(
                            system_tools.stop_service, target, tool_name="stop_service"
                        ),
                    )
                elif action == "forget_all_memories":
                    rag = ctx.get("notes_memory")
                    if rag is None or not _HAS_RAG or not getattr(rag, "enabled", False):
                        result = "I don't have long-term memory available right now."
                    else:
                        try:
                            ok = rag.clear_all()
                        except Exception as e:
                            print(f"[Memory] clear_all failed: {e}")
                            ok = False
                        result = (
                            "Done -- I've forgotten everything I knew about you long-term."
                            if ok
                            else "Sorry, I ran into a problem clearing my memory."
                        )
                elif action in _LOW_CONFIDENCE_CONFIRM_ACTIONS:
                    # NEW: confirmed low-confidence destructive action
                    # (shutdown_system / restart_system / log_off /
                    # empty_recycle_bin) -- reached the same way every
                    # other zero-arg action is, via SIMPLE_ACTIONS.
                    action_fn = system_tools.SIMPLE_ACTIONS.get(action)
                    if action_fn is None:
                        result = "Sorry, I don't know how to do that anymore."
                        _log_action(db, "system_action", action, "fail")
                    else:
                        try:
                            result = action_fn()
                            _log_action(db, "system_action", action, "success")
                        except Exception as e:
                            print(f"[Core] Confirmed low-confidence action '{action}' raised: {e}")
                            result = "Sorry, I ran into a problem with that."
                            _log_action(db, "system_action", action, "fail")
                else:
                    result = "Sorry, I lost track of what I was confirming."
                return _quick(ctx, result)
            if reply in _CONFIRM_NO_WORDS:
                confirm_state.pop("pending", None)
                return _quick(ctx, "Okay, cancelled.")
            # Anything else: fall through to normal intent detection below
            # (user changed their mind / asked something unrelated) but
            # drop the stale pending confirmation so it can't fire later.
            confirm_state.pop("pending", None)

    intent, match = detect_intent(user_input)

    # ── Low-confidence confirmation gate (NEW) ──────────────────────────
    # ONLY for the 4 actions above, and ONLY when this turn's confidence
    # was below threshold. Every other intent — including these same 4
    # actions at normal confidence — falls straight through, unaffected.
    if (
        intent in _LOW_CONFIDENCE_CONFIRM_ACTIONS
        and ctx["stt_confidence"] < _STT_CONFIDENCE_CONFIRM_THRESHOLD
    ):
        ctx["confirm_state"]["pending"] = {
            "action": intent,
            "target": None,
            "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
        }
        phrase = _LOW_CONFIDENCE_CONFIRM_PHRASES.get(intent, intent.replace("_", " "))
        return _quick(
            ctx,
            f"I didn't hear that too clearly -- are you sure you want to {phrase}? "
            f"Say yes or cancel.",
        )

    if cancel_event.is_set():
        return ""

    handler = _INTENT_HANDLERS.get(intent)
    if handler is not None:
        try:
            result = handler(match, ctx)
        except Exception as e:
            # A single bad handler (built-in or a sara/skills/ plugin) must
            # never be allowed to propagate up into run_sara_logic()'s
            # outer try/except, which treats any escaped exception as
            # fatal and exits the ENTIRE main voice loop thread. One
            # failed command should just be one failed command.
            print(f"[Core] Handler for intent '{intent}' raised: {e}")
            result = _quick(
                ctx, "Sorry, I ran into a problem with that. Let's try something else."
            )
            # AUDIT LOG (NEW): handler raised -- one "fail" entry for this
            # intent/skill. See this module's docstring for why this is
            # one of the two dispatch chokepoints action_log is written from.
            _log_action(db, "intent", intent, "fail")
            return result
        if cancel_event.is_set():
            return ""
        if result is not None:
            if isinstance(result, _LikelyMisfireReply):
                # LIKELY-MISFIRE RETRY (NEW): the fast-path regex matched
                # something, but the handler itself doesn't trust what it
                # captured (see _LikelyMisfireReply) -- give the smarter
                # LLM tool router ONE genuine second attempt at the SAME
                # original user_input before accepting this as the final
                # answer. Gated on this exact signal ONLY, so a normal
                # success or a genuine/expected failure (neither of
                # which is ever wrapped as _LikelyMisfireReply) pays
                # zero added latency or LLM calls -- exactly as before
                # this feature existed.
                retried_result = _retry_via_tool_router(user_input, ctx, brain)
                if retried_result is not None:
                    # Second opinion actually resolved something -- use
                    # it. If it didn't (None), `result` still holds the
                    # original handler's clarifying message, so the user
                    # never ends up with silence or a worse experience.
                    result = retried_result
            # AUDIT LOG (NEW): handler ran and produced a spoken result --
            # one "success" entry.
            _log_action(db, "intent", intent, "success")
            # HISTORY FIX (NEW): fast-path intent handlers used to never
            # reach SaraLLM's conversation history -- only the plain-chat
            # path at the bottom of this function did. That left the LLM
            # with zero memory this turn happened, so a follow-up like
            # "cancel that" right after a fast-path handler ran would fail.
            # brain.record_exchange() is a no-op-safe public wrapper (see
            # engine.py) around the same _append_history() the plain-chat
            # path already uses, so this reuses its existing dedup logic
            # rather than introducing a second, divergent history mechanism.
            brain.record_exchange(user_input, result)
            return result
        # AUDIT LOG (NEW): handler explicitly declined (returned None,
        # e.g. "if not match: return None") -- one "skipped" entry, then
        # fall through to the next dispatch stage exactly as before.
        _log_action(db, "intent", intent, "skipped")

    if intent in system_tools.SIMPLE_ACTIONS:
        try:
            result = _quick(ctx, system_tools.SIMPLE_ACTIONS[intent]())
            # AUDIT LOG (NEW): zero-arg system action executed successfully.
            _log_action(db, "system_action", intent, "success")
            # HISTORY FIX (NEW): same gap as the fast-path handler block
            # above -- a zero-arg system action (lock_pc, mute, ...) never
            # made it into SaraLLM's history before. See that block's
            # comment for the full rationale.
            brain.record_exchange(user_input, result)
            return result
        except Exception as e:
            print(f"[Core] SIMPLE_ACTIONS['{intent}'] raised: {e}")
            # AUDIT LOG (NEW): zero-arg system action raised.
            _log_action(db, "system_action", intent, "fail")
            return _quick(
                ctx, "Sorry, I ran into a problem with that. Let's try something else."
            )

    # ── LLM routing: ONE stage at most (LATENCY FIX) ────────────────────
    # These two blocks used to be independent `if`s, so a "chat"-intent
    # message could pay for the planner LLM call AND the tool-router LLM
    # call AND the final chat LLM call -- three sequential round-trips for
    # a single turn. _route_chat_message() (defined above this function)
    # is a pure, LLM-free string heuristic that now picks exactly one of
    # them up front, so the worst case is 2 calls and a plain
    # conversational message costs just 1.
    #
    # Both branches below keep their original bodies verbatim, including
    # their defensive try/except -- a failure in either degrades to the
    # plain-chat answer at the bottom of this function, never to an error
    # shown to the user, and never to an exception escaping into
    # run_sara_logic()'s fatal outer handler.
    #
    # CONTEXT INJECTION (NEW): context_hint is declared here, OUTSIDE the
    # `if intent == "chat":` block below, so it's always defined by the
    # time the generate_response_stream() call at the bottom of this
    # function is reached -- that call is NOT itself gated on
    # intent == "chat" (any unmatched intent can fall through to it), so
    # leaving context_hint undefined for a non-chat intent would risk a
    # NameError instead of the intended "no hint" no-op.
    context_hint = None
    if intent == "chat":
        chat_route, context_hint = _route_chat_message(user_input, context_state)

        if chat_route == "plan":
            # try_plan_and_execute() NEVER raises (see its own docstring);
            # this try/except is a second, redundant safety net purely so a
            # hypothetical future bug in that contract can never escalate
            # into killing the whole voice loop thread.
            try:
                allowed_apps = frozenset(getattr(Config, "APP_LAUNCH_ALLOWLIST", []))
                plan_outcome = try_plan_and_execute(
                    user_input,
                    brain.model_name,
                    _build_plan_dispatch_fn(ctx),
                    Config,
                    allowed_apps=allowed_apps,
                )
                if cancel_event.is_set():
                    return ""
                if plan_outcome is not None:
                    # SAFETY GATE (NEW): a plan step that hit a risky/
                    # destructive action (see PlanStepRequiresConfirmation
                    # in sara.core.planning.executor) aborts the plan and
                    # encodes the confirmation request in abort_reason --
                    # arm the SAME confirm_state/_CONFIRM_YES_WORDS
                    # mechanism _handle_command()'s single-command path
                    # already uses (see the "Pending destructive-action
                    # confirmation" block above), instead of speaking
                    # plan_outcome's normal final_message, which would
                    # misleadingly say the step just "couldn't complete."
                    plan_abort_reason = plan_outcome.abort_reason or ""
                    if plan_outcome.aborted and plan_abort_reason.startswith(
                        "CONFIRMATION_REQUIRED::"
                    ):
                        _, confirm_action, confirm_target, confirm_prompt = (
                            plan_abort_reason.split("::", 3)
                        )
                        ctx["confirm_state"]["pending"] = {
                            "action": confirm_action,
                            "target": confirm_target or None,
                            "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
                        }
                        plan_result = _quick(ctx, confirm_prompt)
                        brain.record_exchange(user_input, plan_result)
                        return plan_result
                    plan_result = _quick(ctx, plan_outcome.final_message)
                    # HISTORY FIX (NEW): a completed multi-step plan never
                    # reached SaraLLM's history before -- see the fast-path
                    # handler block above for the full rationale. Recorded
                    # against the ORIGINAL user_input (not any intermediate
                    # per-step text the planner may have used internally),
                    # since that's what a follow-up turn will actually be
                    # replying to.
                    brain.record_exchange(user_input, plan_result)
                    return plan_result
            except Exception as e:  # noqa: BLE001 -- absolute safety net
                print(f"[Planning] Multi-step plan attempt failed unexpectedly: {e}")
            # NOTE: no longer falls through to the tool-router. The router
            # already judged this message multi-action; re-asking a second
            # LLM to squeeze it into one tool was both slow and usually
            # wrong (it could only ever execute the first action). Plain
            # chat is the correct, cheaper fallback.

        elif chat_route == "tool":
            try:
                resolved = resolve_tool_call(user_input, brain.model_name)
                tool_name = resolved.get("name")
                tool_args = resolved.get("arguments", {})
                mapped_intent = TOOL_NAME_TO_INTENT.get(tool_name)
                if mapped_intent:
                    fake_match = build_fake_match(tool_name, tool_args)
                    tool_handler = _INTENT_HANDLERS.get(mapped_intent)
                    if tool_handler is not None:
                        tool_result = tool_handler(fake_match, ctx)
                        if cancel_event.is_set():
                            return ""
                        if tool_result is not None:
                            # HISTORY FIX (NEW): a resolved single-tool
                            # result never reached SaraLLM's history before
                            # -- see the fast-path handler block above for
                            # the full rationale. Recorded against the
                            # ORIGINAL user_input the user actually said,
                            # not the synthetic fake_match built from the
                            # tool-router's parsed arguments, so a later
                            # "that"/"it" follow-up resolves against what
                            # the user is actually referring back to.
                            brain.record_exchange(user_input, tool_result)
                            return tool_result
            except Exception as e:
                print(f"[ToolRouter] resolution failed: {e}")

        # chat_route == "chat": both LLM routing stages deliberately
        # skipped -- straight to the single generation call below.

    # ── Self-learning fallback log ───────────────────────────────────────
    # Reached only when the fast-path regex matcher, the multi-step
    # planner, AND the single-tool LLM resolver all failed to route this
    # to a real tool -- i.e. it's about to be answered as plain chat.
    # Logging it here (rather than at the top the moment detect_intent()
    # returns "chat") means a query the planner or tool-router DID
    # manage to resolve via the LLM never shows up as a "miss" -- only
    # genuinely unhandled input does, which is what's actually useful to
    # review later for new _INTENT_PATTERNS/_INTENT_GATES entries.
    if intent == "chat":
        log_unmatched(user_input)

    if cancel_event.is_set():
        return ""

    # RAG FOLLOW-UP CONTEXT (NEW): only for a plain chat message with no
    # context_hint already set from elsewhere -- a cheap, best-effort
    # lookup against long-term memory so the LLM can be reminded of
    # something the user explicitly told Sara before. Never allowed to
    # break or slow down the turn: any failure just leaves context_hint
    # as None, exactly as before this change.
    if intent == "chat" and chat_route == "chat" and not context_hint:
        try:
            rag = ctx.get("notes_memory")
            if rag is not None and _HAS_RAG:
                hits = rag.search(user_input, top_k=1, min_similarity=0.55)
                if hits:
                    context_hint = f"(Relevant past note: {hits[0].text})"
        except Exception as e:
            print(f"[RAG] follow-up context lookup failed (continuing): {e}")

    ui_update("status", "thinking")
    try:
        stream = brain.generate_response_stream(user_input, reference_context=context_hint)
        sentences = tts.speak_stream(
            stream,
            on_first_chunk=lambda: ui_update("status", "speaking"),
            on_chunk=lambda s: ui_update("transcript_chunk", "sara", s),
        )
        return " ".join(sentences)
    except Exception as e:
        print(f"[Error] LLM stream failed: {e}")
        return _quick(
            ctx, "Sorry, I had trouble responding to that. Could you try again?"
        )