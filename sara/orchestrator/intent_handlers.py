"""
sara.orchestrator.intent_handlers
One small handler per fast-path regex intent (reminders, notes, clipboard,
weather/news/web, system control, calculator, ...) plus _handle_command(),
the dispatcher that routes a detected intent to its handler.

PLANNING ENGINE INTEGRATION (sara.core.planning)
---------------------------------------------------
_handle_command() can attempt a bounded multi-step plan (via
try_plan_and_execute()) for "chat"-intent messages, as an alternative to
the single-tool resolve_tool_call() path. It engages only when
sara.core.planning.trigger.should_attempt_plan() detects a genuine
multi-action signal in the message.

LLM ROUTING IS MUTUALLY EXCLUSIVE (LATENCY FIX)
--------------------------------------------------
The planner and the single-tool resolver used to be two independent `if`
blocks, so one "chat"-intent turn could cost THREE sequential LLM
round-trips: planner -> tool-router -> final chat generation. They are
now gated by _route_chat_message() (further down this file), a pure,
LLM-free string heuristic that picks exactly ONE of "plan" / "tool" /
"chat" up front. Worst case is now 2 LLM calls, and a plain
conversational message costs exactly 1 (previously 2, because the
tool-router ran on every unmatched message). See that function's
docstring for the heuristic and its explicit trade-off.

SECURITY HARDENING (open_url / open_app / close_app)
--------------------------------------------------------
_h_open_url(), _h_open_app(), and _h_close_app() now validate their
arguments through sara.core.planning.schema.validate_tool_arguments()
before calling the real tool function -- this closes the gap where the
fast-path regex capture alone had no scheme allowlist (open_url) or
application allowlist (open_app/close_app) enforcement. See
sara/core/planning/schema.py's module docstring for the full rationale.
This applies uniformly whether the tool was reached via the fast-path
regex matcher, the LLM single-tool resolver, or a multi-step plan --
all three converge on these same three handler functions.

MEMORY MANAGEMENT / DECISION MEMORY (NEW)
--------------------------------------------
_h_memory_recall(), _h_memory_forget_specific(), _h_memory_forget_all(),
and _h_why_decision() back the voice-triggerable long-term-memory
management feature. _h_memory_forget_all() NEVER deletes anything
directly -- it only arms ctx["confirm_state"]["pending"] (the exact same
mechanism already used for close_app/stop_service on risky targets), and
the actual sara.core.rag.LongTermMemory.clear_all() call happens only
after an explicit "yes" is resolved in _handle_command()'s existing
pending-confirmation block below. See that block for the
"forget_all_memories" branch.

UNDO / ROLLBACK FOR SETTINGS CHANGES (NEW, v1 -- single-step only)
--------------------------------------------------------------------
_h_undo_setting_change() backs the voice-triggerable "undo my last
change" / "revert my last setting" intent ("undo_setting_change" --
NOT the pre-existing, unrelated "undo" intent that sends a keyboard
Ctrl+Z; see that handler's own docstring below for how the two stay
separate). It reads the single most recent sara/core/memory.py
decision_log entry via the new PreferencesDB.get_last_decision(), and
reverts it via the exact same db.set_preference() mechanism every other
preference write in this codebase already uses. v1 scope is a single
most-recent change only -- there is no multi-step undo chain, and a
successful revert is itself logged (via the existing, unchanged
log_decision()) with a reason prefix so a second "undo" right after
stops cleanly instead of ping-ponging the setting back and forth. See
that handler's own docstring for the full design rationale.

MODES / PERSONAS (NEW)
--------------------------------------------
_h_switch_mode() backs the voice-triggerable named-mode switcher
(Study/Work/Gaming/Home/Normal). It applies a FIXED bundle of already-
existing preference keys together -- "focus_mode" (already read by
sara/orchestrator/proactive.py to suppress proactive nudges),
"assistant_active" (already read at startup by
sara/orchestrator/core_wiring.py), and, Gaming Mode only,
"mic_sensitivity" (already read at startup by sara/orchestrator/
history.py and already live-wired by sara/gui/app/settings.py's
set_mic_sensitivity()). No new underlying behavior is introduced -- this
is purely "write several existing preference keys at once, using the
same db.set_preference()/get_preference() mechanism every other
preference in this codebase already uses" plus, for Gaming Mode, the
same live ears.set_manual_energy_threshold() call the GUI setter already
makes when that object happens to be available in ctx. The currently
active mode name itself is persisted under one new preference key,
"active_mode", purely so it can be recalled/displayed later -- that key
does not drive any behavior on its own.

ACTION AUDIT LOG (NEW)
--------------------------------------------
_log_action() below writes to sara/core/memory.py's new action_log
table ("what did Sara actually DO", distinct from conversation_log and
proactive_log). It is called from exactly TWO places in
_handle_command():

  1. The _INTENT_HANDLERS dispatch block ("handler = _INTENT_HANDLERS.get(intent)").
     This is the single chokepoint every fast-path regex intent AND every
     auto-discovered sara/skills/ plugin already passes through -- skills
     register themselves into this same _INTENT_HANDLERS dict via
     register_handler(), so no separate hook is needed for them.
  2. The SIMPLE_ACTIONS dispatch block ("intent in system_tools.SIMPLE_ACTIONS").
     This is the single chokepoint every zero-arg system-tool action
     (lock_pc, mute, open_downloads, ...) passes through. Note this
     block already lives here in intent_handlers.py, not in
     sara/tools/system/dispatch.py (dispatch.py only defines the
     SIMPLE_ACTIONS lookup table itself) -- so dispatch.py needed no
     changes for this feature.

KNOWN SCOPE LIMIT: intents resolved via the LLM single-tool router
(resolve_tool_call(), further down this file) or the multi-step planner
(_build_plan_dispatch_fn()) call _INTENT_HANDLERS entries directly at
their own separate call sites, bypassing these two chokepoints, so
those two paths are NOT currently audit-logged. Both only ever engage
for "chat"-intent input that the fast-path regex matcher already failed
to route (see should_attempt_plan()'s trigger conditions and the
TOOL_CALLING_ENABLED block below) -- i.e. the less common paths. Logging
those too would mean editing sara/core/tool_router.py and
sara/core/planning/executor.py, which are out of scope for this change;
flagging this here so it's a deliberate, visible decision rather than a
silent gap.
"""

# ============================================================================
# COMPATIBILITY FACADE
# ----------------------------------------------------------------------------
# This module used to contain every name below directly. It has been split,
# purely structurally (no logic changes), into the modules imported from
# here -- see each submodule's own docstring for what it now owns. This file
# re-imports and re-exports every single name that used to be defined at
# module level here, EXPLICITLY (never `import *`), because most of those
# names are underscore-prefixed and `import *` does not forward
# underscore-prefixed names by default. Any other file in this codebase that
# does `from sara.orchestrator.intent_handlers import X` for any of these
# names keeps working completely unchanged.
# ============================================================================

from ._shared_state import (
    _CALENDAR_DATEPARSER_LANGUAGES,
    _DEFAULT_MEETING_DURATION_MINUTES,
    LongTermMemory,
    _HAS_RAG,
    resolve_tool_call,
    build_fake_match,
    TOOL_NAME_TO_INTENT,
    _HAS_TOOL_ROUTER,
    try_plan_and_execute,
    PlanValidationError,
    validate_tool_arguments,
    PlanStepRequiresConfirmation,
    should_attempt_plan,
    _HAS_PLANNING,
    logger,
    _EXIT_WORDS,
    _RISKY_APP_KEYWORDS,
    _RISKY_SERVICE_KEYWORDS,
    _CONFIRM_YES_WORDS,
    _CONFIRM_NO_WORDS,
    _CONFIRM_PENDING_TTL_S,
)
from .command_helpers import (
    _is_risky,
    _matches_phrase_set,
    _MEMORY_FORGET_MATCH_THRESHOLD,
    _LOW_CONFIDENCE_CONFIRM_ACTIONS,
    _LOW_CONFIDENCE_CONFIRM_PHRASES,
    _STT_CONFIDENCE_CONFIRM_THRESHOLD,
    _UNDO_REASON_MARKER,
    _MODE_BUNDLES,
    _MODE_ALIASES,
    _MODE_CONFIRMATIONS,
    _quick,
    _LikelyMisfireReply,
    _quick_likely_misfire,
    _ACK_PHRASES,
    _ack,
    _ACTIVITY_FAIL_PREFIXES,
    _ACTIVITY_FAIL_PHRASES,
    _activity,
    _activity_label,
    _activity_failed,
    _run_activity,
    _log_action,
)
from .context_tracking import (
    _CONTEXT_TTL_S,
    _FOLLOWUP_SLOT_BY_INTENT,
    _remember_entity,
    _resolve_app_target,
    _remember_context,
    _FOLLOWUP_TOOL_BY_INTENT,
    _latest_followup_entity,
    _h_followup_query,
    _REFERENCE_HINT_RE,
    _is_pronoun_reference,
    _ENTITY_SLOT_LABELS,
    _describe_recent_entities,
)
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
    _best_fuzzy_memory_match,
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
from .route_chat import (
    _TOOL_SIGNAL_MAX_WORDS,
    _TOOL_SIGNAL_RE,
    _CHAT_SIGNAL_RE,
    _tool_router_available,
    _route_chat_message,
    _retry_via_tool_router,
)
from .dispatcher import (
    _INTENT_HANDLERS,
    register_handler,
    _plan_step_confirmation_needed,
    _build_plan_dispatch_fn,
    _handle_command,
)

__all__ = [
    "_CALENDAR_DATEPARSER_LANGUAGES", "_DEFAULT_MEETING_DURATION_MINUTES",
    "LongTermMemory", "_HAS_RAG", "resolve_tool_call", "build_fake_match",
    "TOOL_NAME_TO_INTENT", "_HAS_TOOL_ROUTER", "try_plan_and_execute",
    "PlanValidationError", "validate_tool_arguments",
    "PlanStepRequiresConfirmation", "should_attempt_plan", "_HAS_PLANNING",
    "logger", "_EXIT_WORDS", "_RISKY_APP_KEYWORDS", "_RISKY_SERVICE_KEYWORDS",
    "_CONFIRM_YES_WORDS", "_CONFIRM_NO_WORDS", "_CONFIRM_PENDING_TTL_S",
    "_is_risky", "_matches_phrase_set", "_MEMORY_FORGET_MATCH_THRESHOLD",
    "_LOW_CONFIDENCE_CONFIRM_ACTIONS", "_LOW_CONFIDENCE_CONFIRM_PHRASES",
    "_STT_CONFIDENCE_CONFIRM_THRESHOLD", "_UNDO_REASON_MARKER",
    "_MODE_BUNDLES", "_MODE_ALIASES", "_MODE_CONFIRMATIONS", "_quick",
    "_LikelyMisfireReply", "_quick_likely_misfire", "_ACK_PHRASES", "_ack",
    "_ACTIVITY_FAIL_PREFIXES", "_ACTIVITY_FAIL_PHRASES", "_activity",
    "_activity_label", "_activity_failed", "_run_activity", "_log_action",
    "_CONTEXT_TTL_S", "_FOLLOWUP_SLOT_BY_INTENT", "_remember_entity",
    "_resolve_app_target", "_remember_context", "_FOLLOWUP_TOOL_BY_INTENT",
    "_latest_followup_entity", "_h_followup_query", "_REFERENCE_HINT_RE",
    "_is_pronoun_reference", "_ENTITY_SLOT_LABELS", "_describe_recent_entities",
    "_h_reminder_add", "_h_reminder_list", "_h_reminder_cancel",
    "_h_set_timer", "_h_set_alarm", "_h_start_stopwatch", "_h_stop_stopwatch",
    "_h_lap_stopwatch", "_h_take_note", "_h_read_notes", "_h_clear_notes",
    "_h_add_todo", "_h_list_todos", "_h_complete_todo", "_h_delete_todo",
    "_h_play_youtube", "_h_play_next_youtube", "_h_play_spotify",
    "_h_web_search", "_h_summarize_url", "_h_open_url", "_h_clipboard_read",
    "_h_clipboard_write", "_h_screenshot_describe", "_h_calculator",
    "_h_system_info", "_h_set_volume", "_h_set_brightness", "_h_mute",
    "_h_unmute", "_h_open_app", "_h_close_app", "_h_typing_text",
    "_h_press_key", "_h_find_file", "_h_open_file", "_h_start_service",
    "_h_stop_service", "_h_restart_application", "_h_switch_to_application",
    "_h_move_resize_window", "_h_always_on_top", "_h_fullscreen",
    "_h_notify_on_file", "_h_weather", "_h_news", "_h_time_query",
    "_h_date_query", "_best_fuzzy_memory_match", "_h_memory_recall",
    "_h_memory_forget_specific", "_h_memory_forget_all", "_h_calendar_today",
    "_h_calendar_create", "_h_run_routine", "_h_why_proactive",
    "_h_why_decision", "_h_undo_setting_change", "_h_switch_mode",
    "_TOOL_SIGNAL_MAX_WORDS", "_TOOL_SIGNAL_RE", "_CHAT_SIGNAL_RE",
    "_tool_router_available", "_route_chat_message", "_retry_via_tool_router",
    "_INTENT_HANDLERS", "register_handler", "_plan_step_confirmation_needed",
    "_build_plan_dispatch_fn", "_handle_command",
]


# Auto-discovers and registers every skill in sara/skills/ (Daily
# Briefing, Notes Q&A, and any future drop-in skill file) via
# register_intent()/register_handler(). Placed here, at the bottom of
# this facade, so every name this module re-exports (register_handler
# included) is already bound by the time sara/skills/__init__.py's
# discovery loop imports it back from here -- moved from dispatcher.py
# to fix a circular import (sara.skills -> intent_handlers -> dispatcher
# -> sara.skills). Wrapped in try/except for the same reason the
# RAG/tool_router imports elsewhere are: a missing or broken skill must
# degrade to "that one skill doesn't work", never to "the app won't start".
try:
    import importlib as _importlib

    _importlib.import_module("sara.skills")
except Exception as _skills_import_err:  # noqa: BLE001
    print(f"[Core] sara.skills unavailable, plugin skills disabled: {_skills_import_err}")
