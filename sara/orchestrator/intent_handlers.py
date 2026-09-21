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
from .calc_utils import _safe_calc, _parse_duration_to_seconds
from .text_utils import _extract_name
from .network_utils import _call_with_timeout
from .tts_worker import TTSWorker
from . import notifications
from ._constants import _SLEEP_WORDS, _FORGET_WORDS


import difflib
import random
import re
import time
import logging
from datetime import datetime, timedelta

import dateparser

from config import Config

from sara.core import routines
from sara.core.intent import detect_intent
from sara.core.unmatched_log import log_unmatched
from sara.tools.reminders import play_alarm_beep
from sara.tools.clipboard import read_clipboard, write_clipboard
from sara.tools import calendar as calendar_tools
from sara.tools import system as system_tools
from sara.tools import web as web_tools

# Same latency fix as sara/tools/reminders.py's add_reminder(): restrict
# dateparser to only the languages Sara actually needs (en/hi), instead
# of letting it try dozens of locales.
_CALENDAR_DATEPARSER_LANGUAGES = ["en", "hi"]
_DEFAULT_MEETING_DURATION_MINUTES = 30

# PRODUCTION-AUDIT ADDITION (Phase 2): long-term memory (RAG) and the
# LLM tool-calling fallback are both optional, additive features — if
# either module fails to import for any reason (e.g. numpy missing),
# the whole app must still start exactly as before, just without that
# one feature. Both are re-checked as None/False below wherever used.
try:
    from sara.core.rag import LongTermMemory

    _HAS_RAG = True
except Exception as _rag_import_err:  # noqa: BLE001
    LongTermMemory = None
    _HAS_RAG = False
    print(
        f"[Core] sara.core.rag unavailable, long-term memory disabled: {_rag_import_err}"
    )

try:
    from sara.core.tool_router import (
        resolve_tool_call,
        build_fake_match,
        TOOL_NAME_TO_INTENT,
    )

    _HAS_TOOL_ROUTER = True
except Exception as _tool_router_import_err:  # noqa: BLE001
    resolve_tool_call = None
    build_fake_match = None
    TOOL_NAME_TO_INTENT = {}
    _HAS_TOOL_ROUTER = False
    print(
        f"[Core] sara.core.tool_router unavailable, LLM tool-calling fallback "
        f"disabled: {_tool_router_import_err}"
    )

# NEW: multi-step planning engine (sara/core/planning/) -- optional,
# additive, same "never break startup" contract as RAG/tool_router
# above. If this fails to import for any reason, _handle_command() below
# simply never attempts a plan and behaves exactly as it did before this
# feature existed.
try:
    from sara.core.planning import try_plan_and_execute
    from sara.core.planning.schema import (
        PlanValidationError,
        validate_tool_arguments,
    )

    # LATENCY FIX: the plan-signal detector is now needed HERE, up front, by
    # _route_chat_message() below -- it is what decides (without any LLM
    # call) whether this turn takes the planner path or the tool-router
    # path. It used to be called only from inside try_plan_and_execute().
    # Imported defensively: older builds of sara.core.planning may not
    # re-export it from the package root, in which case the router simply
    # never picks the "plan" route and behaves like planning is disabled.
    try:
        from sara.core.planning import should_attempt_plan
    except Exception:  # noqa: BLE001
        try:
            from sara.core.planning.trigger import should_attempt_plan
        except Exception:  # noqa: BLE001
            should_attempt_plan = None

    _HAS_PLANNING = True
except Exception as _planning_import_err:  # noqa: BLE001
    try_plan_and_execute = None
    validate_tool_arguments = None
    should_attempt_plan = None

    class PlanValidationError(Exception):  # type: ignore[no-redef]
        """Sentinel fallback so isinstance/except checks below never crash
        when sara.core.planning failed to import."""

    _HAS_PLANNING = False
    print(
        f"[Core] sara.core.planning unavailable, multi-step planning "
        f"disabled (single-tool routing is unaffected): {_planning_import_err}"
    )

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# STOP-WORD FIX: bare "stop" used to be in this set. _matches_phrase_set()
# does prefix/suffix word matching, not exact matching, so ANY sentence
# starting or ending with the single word "stop" matched -- "stop the
# music", "stop timer", "stop recording" all silently exited the entire
# app instead of doing what they said. Removed; "exit"/"quit"/"shutdown"/
# "goodbye"/etc. are still exact/prefix/suffix matches for actually
# quitting Sara.
_EXIT_WORDS = {
    "exit",
    "quit",
    "goodbye",
    "bye",
    "shutdown",
    "band karo",
    "band kar",
    "alvida",
    "phir milenge",
    "bye bye",
    "बंद करो",
    "अलविदा",
}
# NOTE: stays local, NOT in ._constants -- this shard's value conflicts
# with another shard's copy (that one still has "stop"; this one
# deliberately doesn't, see STOP-WORD FIX above). See CONFLICTS.

# CONFIRMATION FLOW: closing/stopping something "risky" (a core system
# process/service, not an everyday app) asks for a yes/no first instead
# of just doing it. Matched as a case-insensitive substring against the
# app/service name, so e.g. "explorer" also catches "explorer.exe".
# Tune these lists as needed -- err on the side of adding, not removing.
_RISKY_APP_KEYWORDS = (
    "explorer", "taskmgr", "task manager", "cmd", "command prompt",
    "powershell", "terminal", "regedit", "registry", "services.msc",
    "control panel", "defender", "antivirus", "firewall", "vpn",
    "svchost", "winlogon", "csrss", "system32",
)
_RISKY_SERVICE_KEYWORDS = (
    "defend", "wuau", "dns", "dhcp", "eventlog", "rpcss", "winmgmt",
    "netlogon", "lanman", "cryptsvc", "bits", "schedule", "power",
    "audiosrv", "spooler", "themes",
)

_CONFIRM_YES_WORDS = {
    "yes", "yeah", "yep", "confirm", "sure", "go ahead", "do it",
    "haan", "ha", "kar do", "kardo", "theek hai", "ok", "okay",
}
_CONFIRM_NO_WORDS = {
    "no", "nope", "cancel", "abort", "never mind", "nevermind",
    "nahi", "mat karo", "rehne do", "chhodo",
}
_CONFIRM_PENDING_TTL_S = 30.0


def _is_risky(name: str, keywords) -> bool:
    lowered = (name or "").lower()
    return any(kw in lowered for kw in keywords)


def _matches_phrase_set(text: str, phrase_set: set) -> bool:
    """
    Matches `text` against a set of short phrases using exact/prefix/
    suffix word matching (NOT plain substring matching) -- e.g. "sleep"
    matches "go to sleep" or "sleep now" but a bare substring check
    would also wrongly match "sleeping pills" or "asleep". See the
    STOP-WORD FIX comment near _EXIT_WORDS above for why bare substring
    matching was previously buggy for single-word phrases like "stop".
    """
    if not text:
        return False
    for phrase in phrase_set:
        if not phrase:
            continue
        if text == phrase or text.startswith(phrase + " ") or text.endswith(" " + phrase):
            return True
    return False



# ── Memory management (NEW) ─────────────────────────────────────────────
# Fuzzy-match confidence threshold the "forget that I like X" voice
# intent requires before it will delete a specific RAG long-term memory.
# See config.py's MEMORY_FORGET_MATCH_THRESHOLD docstring for the full
# rationale -- below this, Sara says she couldn't find a confident match
# instead of guessing and deleting the wrong memory.
_MEMORY_FORGET_MATCH_THRESHOLD = getattr(Config, "MEMORY_FORGET_MATCH_THRESHOLD", 0.45)

# ── Low-confidence confirmation gate (NEW) ───────────────────────────────
# Fixed set of destructive/disruptive zero-arg system actions that
# additionally require confirmation when this turn's STT confidence was
# below Config.STT_CONFIDENCE_CONFIRM_THRESHOLD -- separate from, and
# layered alongside, the existing _RISKY_APP_KEYWORDS/
# _RISKY_SERVICE_KEYWORDS check (untouched).
_LOW_CONFIDENCE_CONFIRM_ACTIONS = {
    "shutdown_system",
    "restart_system",
    "log_off",
    "empty_recycle_bin",
}

_LOW_CONFIDENCE_CONFIRM_PHRASES = {
    "shutdown_system": "shut down the system",
    "restart_system": "restart the system",
    "log_off": "log off",
    "empty_recycle_bin": "empty the recycle bin",
}

_STT_CONFIDENCE_CONFIRM_THRESHOLD = getattr(
    Config, "STT_CONFIDENCE_CONFIRM_THRESHOLD", 0.55
)
# ── Undo / rollback for settings changes (NEW, v1) ──────────────────────
# Reason-string prefix stamped on the FRESH decision_log entry that
# _h_undo_setting_change() writes after a successful revert (see that
# handler's docstring for why a fresh entry, not a schema change, is
# how "consumption" is represented). Checked by that same handler
# against the MOST RECENT decision_log entry before reverting anything,
# so "undo, undo, undo" in a row stops cleanly after one real revert
# instead of ping-ponging a setting back and forth forever -- v1 is
# single-step only.
_UNDO_REASON_MARKER = "[undo] "


# ── Modes / Personas (NEW) ───────────────────────────────────────────────
# Each mode is JUST a named bundle of EXISTING preference keys, applied
# together via db.set_preference() -- the exact same mechanism
# sara/gui/app/settings.py's individual toggles already use. No new
# underlying behavior: "focus_mode" is already read by
# sara/orchestrator/proactive.py to suppress proactive nudges,
# "assistant_active" is already read at startup by
# sara/orchestrator/core_wiring.py, and "mic_sensitivity" is already
# read at startup by sara/orchestrator/history.py and already live-wired
# by settings.py's set_mic_sensitivity(). See that confirmed bundle
# table for exactly why each mode contains what it does.
#
# A key's absence from a mode's dict means "leave it untouched" -- the
# handler below only ever calls db.set_preference() for keys that are
# actually present in the chosen mode's dict, so whatever the user had
# set previously for any other key is left exactly as-is.
_MODE_BUNDLES = {
    "normal": {"focus_mode": "0", "assistant_active": "1"},
    "study": {"focus_mode": "1", "assistant_active": "1"},
    "work": {"focus_mode": "0", "assistant_active": "1"},
    "gaming": {"focus_mode": "0", "assistant_active": "1", "mic_sensitivity": "20"},
    "home": {"focus_mode": "0", "assistant_active": "1"},
}

# "default" is a spoken synonym for the "normal" bundle above -- kept as
# a separate alias table (rather than a duplicate _MODE_BUNDLES entry)
# so _MODE_BUNDLES stays the single source of truth for what each real
# mode name actually applies.
_MODE_ALIASES = {"default": "normal"}

_MODE_CONFIRMATIONS = {
    "normal": "Normal mode on — proactive nudges and mic sensitivity are back to default.",
    "study": "Study mode on — proactive nudges are off now.",
    "work": "Work mode on — proactive nudges are set to normal.",
    "gaming": "Gaming mode on — proactive nudges are off and mic sensitivity is lowered.",
    "home": "Home mode on — proactive nudges are fully on.",
}


# ----------------------------------------------------------------------------
# Command dispatch
# ----------------------------------------------------------------------------


def _quick(ctx: dict, text: str) -> str:
    ctx["ui_update"]("status", "speaking")
    ctx["tts"].speak(text, fast=True)
    return text


_ACK_PHRASES = (
    "On it!",
    "Sure thing!",
    "Ek second...",
    "Done-ish, hold on!",
    "Coming right up!",
)


def _ack(ctx: dict) -> None:
    """
    Fires an instant, non-blocking acknowledgment so the user hears
    something immediately instead of dead silence while a genuinely
    slow action (app launch, service control, network call, screen
    description, ...) runs right after it. Picks a random phrase each
    time so it doesn't feel robotic/repetitive.

    Must NEVER raise: a TTS/UI hiccup here should never block or kill
    the actual command that follows it.
    """
    try:
        ctx["ui_update"]("status", "working")
        ctx["tts"].speak(random.choice(_ACK_PHRASES), fast=True, block=False)
    except Exception as e:
        print(f"[Core] _ack() failed (non-fatal, command continues): {e}")


# ── Live Activity card (NEW) ─────────────────────────────────────────
# Tells the GUI WHAT Sara is doing right now, as a small card next to the
# orb: "Opening Chrome" -> "Chrome opened" (or "Couldn't open Chrome"),
# then it fades away on its own. Pushed as ("activity", state, icon, text)
# through ctx["ui_update"] -- the same channel "status"/"transcript" use.
#   state: "start" | "done" | "error"
# Used ONLY for genuinely slow actions that have a clear name (app launch,
# web search, media, file/service ops) -- never for chit-chat or instant
# local lookups. GUI side: js/home.js (ev:activity) + style/home.css.

# A tool reply is treated as FAILED (card shows the warning state) if the
# first ~60 characters START with one of the prefixes or CONTAIN one of the
# phrases below. This is a text heuristic -- tune these two tuples if a
# real failure shows a green tick, or a success shows a warning.
_ACTIVITY_FAIL_PREFIXES = ("sorry", "error", "failed", "unable", "unfortunately", "oops")
_ACTIVITY_FAIL_PHRASES = (
    "couldn't", "could not", "i can't", "i cannot", "unable to", "failed to",
    "timed out", "not found", "not installed", "doesn't exist",
    "does not exist", "wasn't able",
)


def _activity(ctx: dict, state: str, icon: str, text: str) -> None:
    """Push one activity event. Must NEVER raise -- a GUI hiccup must never break the command."""
    try:
        ctx["ui_update"]("activity", state, str(icon)[:24], str(text)[:60])
    except Exception as e:  # noqa: BLE001
        print(f"[Activity] push failed (non-fatal, command continues): {e}")


def _activity_label(name, limit: int = 28) -> str:
    """Short, tidy display name for the card: ' chrome ' -> 'Chrome'."""
    label = " ".join(str(name or "").split())
    if not label:
        return "app"
    if label.islower():
        label = label.title()
    return label if len(label) <= limit else label[: limit - 3] + "..."


def _activity_failed(result) -> bool:
    """True if a tool's reply looks like a failure (see the two tuples above)."""
    if isinstance(result, tuple) and result:  # e.g. play_next_youtube -> (message, index)
        result = result[0]
    if result is None:
        return True
    if not isinstance(result, str):
        return False
    head = result.strip()[:60].lower()
    return head.startswith(_ACTIVITY_FAIL_PREFIXES) or any(
        phrase in head for phrase in _ACTIVITY_FAIL_PHRASES
    )


def _run_activity(ctx: dict, icon: str, start_text: str, done_text: str, error_text: str, call):
    """
    Runs `call()` (a zero-argument function that performs the action and
    returns its reply) between a "start" card and a "done"/"error" card,
    and hands the reply back UNCHANGED so the caller speaks it exactly as
    before. The final card is pushed BEFORE the caller speaks the reply,
    so the card flips to done/error the moment the action finishes, not
    after Sara finishes talking. If `call()` raises, an error card is
    pushed and the exception is re-raised untouched, so
    _handle_command()'s existing handler deals with it as it always did.
    """
    _activity(ctx, "start", icon, start_text)
    try:
        result = call()
    except Exception:
        _activity(ctx, "error", icon, error_text)
        raise
    if _activity_failed(result):
        _activity(ctx, "error", icon, error_text)
    else:
        _activity(ctx, "done", icon, done_text)
    return result


# ── Action audit log (NEW) ───────────────────────────────────────────
def _log_action(db, action_type: str, action_name: str, outcome: str) -> None:
    """
    Fire-and-forget write to sara/core/memory.py's action_log table
    ("what did Sara actually DO"). Called from _handle_command()'s two
    dispatch chokepoints -- see this module's docstring for exactly
    which two, and why those two cover every fast-path intent, every
    auto-discovered sara/skills/ plugin, and every zero-arg system
    action without sprinkling logging calls through individual
    tool/skill files.

    Must NEVER raise or add latency: an audit-log hiccup must never
    break, slow down, or change the outcome of the command that was
    actually just run. `db` may legitimately be None (e.g. during
    early startup wiring or in a stripped-down test ctx), so that's
    just a silent no-op, not an error.
    """
    if db is None or not hasattr(db, "log_action"):
        return
    try:
        db.log_action(action_type, action_name, outcome, wait=False)
    except Exception as e:  # noqa: BLE001 -- audit logging must never break dispatch
        print(f"[AuditLog] log_action('{action_type}', '{action_name}') failed (non-fatal): {e}")


def _h_reminder_add(match, ctx):
    if not match:
        return None
    return _quick(ctx, ctx["reminders"].add_reminder(match.group(1), match.group(2)))


def _h_reminder_list(match, ctx):
    return _quick(ctx, ctx["reminders"].list_reminders())


def _h_reminder_cancel(match, ctx):
    return _quick(ctx, ctx["reminders"].cancel_all_reminders())


def _h_set_timer(match, ctx):
    if not match:
        return None
    duration_text = match.group(1).strip()
    seconds = _parse_duration_to_seconds(duration_text)
    if not seconds:
        return _quick(
            ctx, f"Sorry, I couldn't understand the duration '{duration_text}'."
        )

    tts, ui_update = ctx["tts"], ctx["ui_update"]

    def _timer_done(msg: str):
        try:
            play_alarm_beep(repetitions=2)
        except Exception as e:
            print(f"[Warning] alarm beep failed: {e}")
        ui_update("status", "speaking")
        tts.speak(msg, fast=True)
        ui_update("transcript", "sara", f"\u23f0 {msg}")

    return _quick(ctx, system_tools.set_timer(seconds, duration_text, _timer_done))


def _h_take_note(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.take_note(match.group(1).strip()))


def _h_read_notes(match, ctx):
    return _quick(ctx, system_tools.read_notes())


def _h_clear_notes(match, ctx):
    return _quick(ctx, system_tools.clear_notes())


def _h_clipboard_read(match, ctx):
    return _quick(ctx, f"Your clipboard contains: {read_clipboard()}")


def _h_clipboard_write(match, ctx):
    if not match:
        return None
    return _quick(ctx, write_clipboard(match.group(1)))


def _h_screenshot_describe(match, ctx):
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    return _quick(
        ctx,
        _run_activity(
            ctx, "screen", "Checking your screen", "Screen checked", "Couldn't check the screen",
            lambda: ctx["vision"].describe_screen(),
        ),
    )


def _h_weather(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    location = match.group(1)
    _remember_context(ctx, "weather", location)
    return _quick(
        ctx,
        _run_activity(
            ctx, "weather", "Checking weather", "Weather ready", "Couldn't get weather",
            lambda: _call_with_timeout(web_tools.get_weather, location),
        ),
    )


def _h_news(match, ctx):
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    if match and match.lastindex and match.lastindex >= 1:
        topic = match.group(1)
        _remember_context(ctx, "news", topic)
        news_call = lambda: _call_with_timeout(web_tools.get_news, topic)  # noqa: E731
    else:
        news_call = lambda: _call_with_timeout(web_tools.get_news)  # noqa: E731
    return _quick(
        ctx,
        _run_activity(
            ctx, "news", "Fetching news", "News ready", "Couldn't get news", news_call
        ),
    )


# ── Context Follow-ups ("what about jaipur?" / "aur dilli ka?") ────────
# ctx["context_state"] is a plain dict, created once per run in
# sara/orchestrator/core_wiring.py -- same pattern as playback_state and
# confirm_state -- so it genuinely persists turn-to-turn. Only weather
# and news currently opt in via _remember_context(); any future intent
# can opt in the same way by calling it with its own intent name and
# slot value.
_CONTEXT_TTL_S = 120  # short window: a natural follow-up, not a resumed
                       # conversation from minutes ago


def _remember_context(ctx, intent_name: str, slot_value: str) -> None:
    if not slot_value:
        return
    ctx["context_state"]["last_slot_intent"] = {
        "intent": intent_name,
        "slot": slot_value,
        "ts": time.time(),
    }


# Maps a remembered intent name to the tool function that intent's own
# handler calls, so a follow-up can redo "the same kind of question"
# with a new slot value without duplicating each handler's own
# ack/status/error-handling logic.
_FOLLOWUP_TOOL_BY_INTENT = {
    "weather": web_tools.get_weather,
    "news": web_tools.get_news,
}


def _h_followup_query(match, ctx):
    if not match:
        return None
    state = ctx["context_state"].get("last_slot_intent")
    if not state or (time.time() - state.get("ts", 0) > _CONTEXT_TTL_S):
        return _quick(
            ctx, "I'm not sure what you're asking about — could you rephrase that?"
        )
    tool_fn = _FOLLOWUP_TOOL_BY_INTENT.get(state["intent"])
    if tool_fn is None:
        return _quick(
            ctx, "I'm not sure what you're asking about — could you rephrase that?"
        )
    new_slot = match.group(1).strip()
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    _remember_context(ctx, state["intent"], new_slot)
    is_news = state["intent"] == "news"
    return _quick(
        ctx,
        _run_activity(
            ctx,
            "news" if is_news else "weather",
            "Fetching news" if is_news else "Checking weather",
            "News ready" if is_news else "Weather ready",
            "Couldn't get news" if is_news else "Couldn't get weather",
            lambda: _call_with_timeout(tool_fn, new_slot),
        ),
    )


def _h_play_youtube(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")
    query = match.group(1).strip()
    result = _run_activity(
        ctx, "youtube", "Searching YouTube", "Playing on YouTube", "Couldn't play that",
        lambda: _call_with_timeout(web_tools.play_youtube, query, tool_name="play_youtube"),
    )
    if isinstance(result, str) and result.startswith("Playing"):
        ctx["playback_state"]["youtube"] = {"query": query, "index": 0}
    return _quick(ctx, result)


def _h_play_next_youtube(match, ctx):
    """
    'next video' / 'agla video chalao' follow-up — only makes sense
    right after a play_youtube call, so it needs ctx["playback_state"]
    to know which search to continue.
    """
    state = ctx["playback_state"].get("youtube")
    if not state:
        return _quick(ctx, "I'm not playing anything from YouTube right now.")
    ctx["ui_update"]("status", "thinking")
    result = _run_activity(
        ctx, "youtube", "Loading next video", "Next video playing", "Couldn't load next video",
        lambda: _call_with_timeout(
            web_tools.play_next_youtube,
            state["query"],
            state["index"],
            tool_name="play_next_youtube",
        ),
    )
    if isinstance(result, tuple) and len(result) == 2:
        message, new_index = result
        state["index"] = new_index
    else:
        # _call_with_timeout hit its own timeout/exception path and
        # returned a plain error string instead of our (msg, index) tuple.
        message = result
    return _quick(ctx, message)


def _h_play_spotify(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")
    query = match.group(1).strip()
    return _quick(
        ctx,
        _run_activity(
            ctx, "spotify", "Opening Spotify", "Playing on Spotify", "Couldn't play that",
            lambda: _call_with_timeout(web_tools.play_spotify, query),
        ),
    )


def _h_web_search(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    query = match.group(1)
    return _quick(
        ctx,
        _run_activity(
            ctx, "search", "Searching the web", "Search complete", "Search failed",
            lambda: _call_with_timeout(web_tools.search_web, query),
        ),
    )


def _h_summarize_url(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")

    def _read_and_summarize():
        page_text = _call_with_timeout(web_tools.read_webpage, match.group(1))
        if isinstance(page_text, str) and (
            page_text.startswith("Error:") or page_text.startswith("Sorry,")
        ):
            return page_text
        return ctx["brain"].summarize_text(page_text)

    return _quick(
        ctx,
        _run_activity(
            ctx, "link", "Reading page", "Summary ready", "Couldn't read that page",
            _read_and_summarize,
        ),
    )


def _h_open_url(match, ctx):
    """
    Opens a URL captured by the open_url fast-path regex (or resolved by
    the LLM single-tool router, or proposed by a multi-step plan -- all
    three converge here).

    SECURITY HARDENING: the captured URL is validated via
    sara.core.planning.schema.validate_tool_arguments() before being
    passed to web_tools.open_url() -- only http:// and https:// schemes
    are ever allowed through; javascript:/data:/file:/vbscript:/etc. are
    rejected with a clear spoken message instead of being opened. If
    sara.core.planning failed to import (_HAS_PLANNING is False), this
    degrades to the original unvalidated behavior rather than crashing
    -- matching this codebase's "optional feature missing must never
    break the app" convention.
    """
    if not match:
        return None
    raw_url = match.group(1)
    if _HAS_PLANNING and validate_tool_arguments is not None:
        try:
            validated = validate_tool_arguments("open_url", {"url": raw_url})
            raw_url = validated["url"]
        except PlanValidationError as e:
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] open_url validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely open that link.")
    _ack(ctx)
    return _quick(
        ctx,
        _run_activity(
            ctx, "link", "Opening link", "Link opened", "Couldn't open link",
            lambda: web_tools.open_url(raw_url),
        ),
    )


def _h_calculator(match, ctx):
    if not match:
        return None
    expr = match.group(1).strip() if match.lastindex and match.lastindex >= 1 else ""
    if expr and expr.lower() not in ("calculator", "calc"):
        return _quick(ctx, _safe_calc(expr))
    return _quick(ctx, system_tools.open_application("calc"))


def _h_system_info(match, ctx):
    return _quick(ctx, system_tools.get_system_summary())


def _h_set_volume(match, ctx):
    if not match:
        return None
    volume_state = ctx["volume_state"]
    try:
        level = int(match.group(1))
        volume_state["last"] = level
        return _quick(ctx, system_tools.set_volume(level))
    except (TypeError, ValueError, IndexError):
        lowered_input = ctx["user_input"].lower()
        if any(w in lowered_input for w in ("up", "increase", "raise", "louder")):
            return _quick(ctx, system_tools.adjust_volume(10))
        if any(
            w in lowered_input
            for w in ("down", "decrease", "lower", "reduce", "quieter")
        ):
            return _quick(ctx, system_tools.adjust_volume(-10))
        return _quick(ctx, "What volume level would you like?")


def _h_set_brightness(match, ctx):
    if not match:
        return None
    try:
        return _quick(ctx, system_tools.set_brightness(int(match.group(1))))
    except (TypeError, ValueError, IndexError):
        return _quick(ctx, "What brightness level would you like?")


def _h_mute(match, ctx):
    volume_state = ctx["volume_state"]
    get_vol_func = getattr(system_tools, "get_volume", None)
    if get_vol_func:
        try:
            current = get_vol_func()
            if current and current > 0:
                volume_state["pre_mute"] = current
        except Exception:
            pass
    return _quick(ctx, system_tools.set_volume(0))


def _h_unmute(match, ctx):
    restore_to = ctx["volume_state"].get("pre_mute", 50)
    return _quick(ctx, system_tools.set_volume(restore_to))


def _h_open_app(match, ctx):
    """
    Launches an application captured by the open_app fast-path regex (or
    resolved by the LLM single-tool router, or proposed by a multi-step
    plan -- all three converge here).

    SECURITY HARDENING: the captured application name is validated
    against Config.APP_LAUNCH_ALLOWLIST via
    sara.core.planning.schema.validate_tool_arguments() before being
    passed to system_tools.open_application() -- an unrecognized
    application name is rejected with a clear spoken message instead of
    being launched. Set Config.APP_LAUNCH_ALLOWLIST_ENABLED=False to
    disable this enforcement entirely (not recommended). If
    sara.core.planning failed to import, this degrades to the original
    unvalidated behavior rather than crashing.
    """
    if not match:
        return None
    target = match.group(1).strip()
    if _HAS_PLANNING and validate_tool_arguments is not None:
        try:
            allowed_apps = frozenset(getattr(Config, "APP_LAUNCH_ALLOWLIST", []))
            allowlist_enabled = getattr(Config, "APP_LAUNCH_ALLOWLIST_ENABLED", True)
            validated = validate_tool_arguments(
                "open_app",
                {"target": target},
                allowed_apps=allowed_apps,
                app_allowlist_enabled=allowlist_enabled,
            )
            target = validated["target"]
        except PlanValidationError as e:
            _activity(ctx, "error", "app", f"Couldn't open {_activity_label(target)}")
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] open_app validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely open that application.")
    _ack(ctx)
    label = _activity_label(target)
    return _quick(
        ctx,
        _run_activity(
            ctx, "app", f"Opening {label}", f"{label} opened", f"Couldn't open {label}",
            lambda: _call_with_timeout(
                system_tools.open_application, target, tool_name="open_application"
            ),
        ),
    )


def _h_close_app(match, ctx):
    """
    Closes an application captured by the close_app fast-path regex (or
    resolved by the LLM single-tool router, or proposed by a multi-step
    plan -- all three converge here).

    SECURITY HARDENING: same allowlist validation as _h_open_app() above,
    applied BEFORE the existing risky-app confirmation flow -- an
    unrecognized application is rejected outright rather than reaching
    the "are you sure?" prompt at all.
    """
    if not match:
        return None
    app_name = match.group(1).strip()
    if _HAS_PLANNING and validate_tool_arguments is not None:
        try:
            allowed_apps = frozenset(getattr(Config, "APP_LAUNCH_ALLOWLIST", []))
            allowlist_enabled = getattr(Config, "APP_LAUNCH_ALLOWLIST_ENABLED", True)
            validated = validate_tool_arguments(
                "close_app",
                {"target": app_name},
                allowed_apps=allowed_apps,
                app_allowlist_enabled=allowlist_enabled,
            )
            app_name = validated["target"]
        except PlanValidationError as e:
            _activity(ctx, "error", "app", f"Couldn't close {_activity_label(app_name)}")
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] close_app validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely close that application.")
    if _is_risky(app_name, _RISKY_APP_KEYWORDS):
        ctx["confirm_state"]["pending"] = {
            "action": "close_app",
            "target": app_name,
            "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
        }
        return _quick(
            ctx, f"{app_name} is a system app -- are you sure you want to close it? Say yes or cancel."
        )
    _ack(ctx)
    label = _activity_label(app_name)
    return _quick(
        ctx,
        _run_activity(
            ctx, "app", f"Closing {label}", f"{label} closed", f"Couldn't close {label}",
            lambda: _call_with_timeout(
                system_tools.close_application, app_name, tool_name="close_application"
            ),
        ),
    )
def _h_typing_text(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.type_text(match.group(1).strip()))


def _h_press_key(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.press_key(match.group(1).strip()))


def _h_find_file(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    file_query = match.group(1).strip()
    return _quick(
        ctx,
        _run_activity(
            ctx, "file", "Searching files", "File found", "Couldn't find that file",
            lambda: _call_with_timeout(system_tools.find_file, file_query, tool_name="find_file"),
        ),
    )

def _h_start_service(match, ctx):
    if not match:
        return None
    _ack(ctx)
    service_name = match.group(1).strip()
    label = _activity_label(service_name)
    return _quick(
        ctx,
        _run_activity(
            ctx, "service", f"Starting {label}", f"{label} started", f"Couldn't start {label}",
            lambda: _call_with_timeout(
                system_tools.start_service, service_name, tool_name="start_service"
            ),
        ),
    )


def _h_stop_service(match, ctx):
    if not match:
        return None
    service_name = match.group(1).strip()
    if _is_risky(service_name, _RISKY_SERVICE_KEYWORDS):
        ctx["confirm_state"]["pending"] = {
            "action": "stop_service",
            "target": service_name,
            "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
        }
        return _quick(
            ctx,
            f"{service_name} looks like a core system service -- are you sure you want to stop it? Say yes or cancel.",
        )
    _ack(ctx)
    label = _activity_label(service_name)
    return _quick(
        ctx,
        _run_activity(
            ctx, "service", f"Stopping {label}", f"{label} stopped", f"Couldn't stop {label}",
            lambda: _call_with_timeout(
                system_tools.stop_service, service_name, tool_name="stop_service"
            ),
        ),
    )

def _h_restart_application(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    app_name = match.group(1).strip()
    label = _activity_label(app_name)
    return _quick(
        ctx,
        _run_activity(
            ctx, "app", f"Restarting {label}", f"{label} restarted", f"Couldn't restart {label}",
            lambda: _call_with_timeout(
                system_tools.restart_application, app_name, tool_name="restart_application"
            ),
        ),
    )


def _h_switch_to_application(match, ctx):
    if not match:
        return None
    _ack(ctx)
    return _quick(
        ctx,
        _call_with_timeout(
            system_tools.switch_to_application, match.group(1).strip(), tool_name="switch_to_application"
        ),
    )


def _h_move_resize_window(match, ctx):
    if not match:
        return None
    _ack(ctx)
    app_name, position = match.group(1).strip(), match.group(2).strip()
    return _quick(ctx, system_tools.move_window(app_name, position))


def _h_always_on_top(match, ctx):
    if not match:
        return None
    _ack(ctx)
    return _quick(ctx, system_tools.toggle_always_on_top(match.group(1).strip()))


def _h_fullscreen(match, ctx):
    _ack(ctx)
    app_name = match.group(1).strip() if (match and match.lastindex) else ""
    return _quick(ctx, system_tools.toggle_fullscreen(app_name))
    
def _h_time_query(match, ctx):
    return _quick(ctx, f"It's {datetime.now().strftime('%I:%M %p')}.")


def _h_date_query(match, ctx):
    return _quick(ctx, f"Today is {datetime.now().strftime('%A, %B %d, %Y')}.")


def _h_why_proactive(match, ctx):
    """
    Explainable-AI handler for "why did you say that?" / "kyu bola?".
    Looks up the most recent sara/orchestrator/proactive.py nudge (logged
    via db.log_proactive_event) and speaks the specific, human-readable
    reason that was recorded for it at the time it fired.
    """
    db = ctx["db"]
    event = None
    if db is not None and hasattr(db, "get_last_proactive_event"):
        try:
            event = db.get_last_proactive_event()
        except Exception as e:
            print(f"[Proactive] get_last_proactive_event failed: {e}")
    if not event:
        return _quick(ctx, "I haven't said anything on my own recently.")
    reason = event.get("reason") or "I don't have a specific reason recorded for that one."
    return _quick(ctx, reason)


def _h_why_decision(match, ctx):
    """
    "why did I change X" / "maine X kyun change kiya tha" -- looks up
    the most recent matching sara/core/memory.py decision_log entry
    (fuzzy-matched against the setting's key, via
    PreferencesDB.find_decision_by_query()) and speaks back the
    plain-English reason recorded for it at the time it was changed.
    Never guesses: if nothing matches confidently, says so instead.
    """
    if not match:
        return None
    query_text = match.group(1).strip()
    if not query_text:
        return _quick(ctx, "Which setting are you asking about?")

    db = ctx.get("db")
    if db is None or not hasattr(db, "find_decision_by_query"):
        return _quick(ctx, "I don't have any change history recorded.")

    try:
        entry = db.find_decision_by_query(query_text)
    except Exception as e:
        print(f"[Memory] find_decision_by_query failed: {e}")
        return _quick(ctx, "Sorry, I couldn't look that up right now.")

    if not entry:
        return _quick(
            ctx, f"I couldn't find a recent change matching '{query_text}'."
        )

    reason = entry.get("reason") or "I don't have a specific reason recorded for that change."
    return _quick(ctx, reason)


def _h_undo_setting_change(match, ctx):
    """
    "undo my last change" / "revert my last setting" / Hindi equivalents
    -- reverts the single most recent sara/core/memory.py decision_log
    entry back to its old_value, via the exact same db.set_preference()
    mechanism every other preference write in this codebase already
    uses. Distinct from, and must never collide with, the pre-existing
    "undo" intent (see sara/core/intent/patterns.py's undo_setting_change
    block comment) which sends a keyboard Ctrl+Z to whatever app is
    focused -- that intent and its handler are completely untouched by
    this feature.

    v1 SCOPE: single most-recent change only. There is no multi-step
    "undo, undo again" chain -- see the design note below for exactly
    what happens if this is invoked twice in a row.

    DESIGN DECISION (does undo consume the entry it reverted, or log a
    fresh one?): sara/core/memory.py's decision_log schema and its
    existing writer (log_decision()) are explicitly off-limits for this
    feature -- no new column, no changed signature -- so "marking" the
    original row as consumed in-place isn't an option without violating
    that constraint. Instead, a successful revert logs a FRESH
    decision_log entry (via the existing, unchanged log_decision()) whose
    reason is prefixed with _UNDO_REASON_MARKER.

    Before reverting anything, this handler checks whether the MOST
    RECENT decision_log entry already carries that marker. If it does,
    the last change on record was itself an undo, and reverting IT again
    would just toggle the setting back and forth with no clearly
    "correct" end state -- so this handler stops and says so instead of
    chaining undos. That keeps decision_log itself as the single,
    append-only source of truth (nothing is ever deleted or silently
    reinterpreted) while still making repeated "undo" a clean, harmless
    no-op rather than a confusing ping-pong. This is the simpler and
    safer of the two options, since it needs no schema change and no new
    write path -- it reuses log_decision() and get_last_decision()
    exactly as written.
    """
    db = ctx.get("db")
    if db is None or not hasattr(db, "get_last_decision") or not hasattr(db, "set_preference"):
        return _quick(ctx, "I don't have any change history available right now.")

    try:
        entry = db.get_last_decision()
    except Exception as e:
        print(f"[Undo] get_last_decision failed: {e}")
        return _quick(ctx, "Sorry, I couldn't look up your last change right now.")

    if not entry:
        return _quick(
            ctx, "You haven't changed any settings yet, so there's nothing to undo."
        )

    reason = entry.get("reason") or ""
    if reason.startswith(_UNDO_REASON_MARKER):
        return _quick(
            ctx,
            "Your last change was already an undo, so I can't undo that again right now.",
        )

    setting_key = entry.get("setting_key")
    old_value = entry.get("old_value")
    if not setting_key or old_value is None:
        display_key = (setting_key or "that setting").replace("_", " ")
        return _quick(
            ctx,
            f"That was the first time {display_key} was ever set, so there's nothing to revert it to.",
        )

    _ack(ctx)
    try:
        ok = db.set_preference(setting_key, old_value)
    except Exception as e:
        print(f"[Undo] set_preference('{setting_key}') failed: {e}")
        ok = False

    if not ok:
        return _quick(ctx, "Sorry, I ran into a problem reverting that change.")

    try:
        db.log_decision(
            setting_key,
            entry.get("new_value"),
            old_value,
            f"{_UNDO_REASON_MARKER}Reverted via voice undo command.",
            wait=False,
        )
    except Exception as e:  # noqa: BLE001 -- audit logging must never break the revert
        print(f"[Undo] log_decision failed (non-fatal, revert already applied): {e}")

    display_key = setting_key.replace("_", " ")
    return _quick(ctx, f"Okay, I've set {display_key} back to {old_value}.")


def _best_fuzzy_memory_match(target_phrase: str, candidates: list) -> "dict | None":
    """
    Finds the RAG memory in `candidates` (list of {"id","text",...} from
    LongTermMemory.list_memories()) whose text best matches
    `target_phrase`, using stdlib difflib (no new dependency -- it's part
    of the Python standard library). Returns None if there are no
    candidates at all; otherwise returns the best candidate dict with an
    added "score" key (0-1) the caller checks against
    Config.MEMORY_FORGET_MATCH_THRESHOLD before acting on it.
    """
    if not target_phrase or not candidates:
        return None
    normalized_target = target_phrase.strip().lower()
    best = None
    best_score = 0.0
    for candidate in candidates:
        text = (candidate.get("text") or "").lower()
        ratio = difflib.SequenceMatcher(None, normalized_target, text).ratio()
        # Bonus for a direct substring match -- handles the common case
        # where the spoken phrase is a short fragment of a longer stored
        # memory sentence (e.g. "pizza" inside "user mentioned they like
        # pizza on weekends").
        if normalized_target in text:
            ratio = max(ratio, 0.8)
        if ratio > best_score:
            best_score = ratio
            best = candidate
    if best is None:
        return None
    return {**best, "score": best_score}


def _h_memory_recall(match, ctx):
    """
    "what do you remember about me" / "mere baare mein kya yaad hai" --
    speaks a short summary built from (1) the RAG long-term memory
    store's top semantic matches for a generic "personal facts" query,
    and (2) ONLY the user_name preference. Design decision: preferences
    DB's other keys (wake_word, streak_count, routine_last_run:*, etc.)
    are internal/system bookkeeping, not memories worth reading back to
    the user, so they are deliberately excluded here.
    """
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")

    db = ctx.get("db")
    user_name = db.get_preference("user_name") if db else None

    rag = ctx.get("notes_memory")
    memory_texts = []
    if rag is not None and _HAS_RAG and getattr(rag, "enabled", False):
        try:
            hits = rag.search(
                "personal facts and preferences about the user", top_k=5
            )
            memory_texts = [h.text for h in hits]
        except Exception as e:
            print(f"[Memory] recall search failed: {e}")

    if not memory_texts and not user_name:
        return _quick(ctx, "I don't have anything specific stored about you yet.")

    parts = []
    if user_name:
        parts.append(f"I know your name is {user_name}.")
    if memory_texts:
        joined = "; ".join(memory_texts[:5])
        parts.append(f"Here's what I remember: {joined}.")
    return _quick(ctx, " ".join(parts))


def _h_memory_forget_specific(match, ctx):
    """
    "forget that I like X" / "ye bhool jao ki mujhe X pasand hai" --
    fuzzy-matches the spoken phrase against ACTUAL stored RAG memories
    (never against preferences DB's system keys) and deletes only the
    single best match, ONLY if it clears
    Config.MEMORY_FORGET_MATCH_THRESHOLD. If nothing matches
    confidently, says so instead of guessing -- per this feature's spec,
    this must never wipe the wrong memory or the whole store.
    """
    if not match:
        return None
    target_phrase = match.group(1).strip()
    if not target_phrase:
        return _quick(ctx, "What would you like me to forget?")

    rag = ctx.get("notes_memory")
    if rag is None or not _HAS_RAG or not getattr(rag, "enabled", False):
        return _quick(ctx, "I don't have long-term memory available right now.")

    try:
        candidates = rag.list_memories()
    except Exception as e:
        print(f"[Memory] list_memories failed: {e}")
        return _quick(ctx, "Sorry, I couldn't look through my memories right now.")

    best = _best_fuzzy_memory_match(target_phrase, candidates)
    if best is None or best["score"] < _MEMORY_FORGET_MATCH_THRESHOLD:
        return _quick(
            ctx,
            f"I couldn't find a specific memory about '{target_phrase}' to forget. "
            f"Could you say it a bit differently?",
        )

    _ack(ctx)
    try:
        ok = rag.delete_memory(best["id"])
    except Exception as e:
        print(f"[Memory] delete_memory failed: {e}")
        ok = False
    if ok:
        return _quick(ctx, "Okay, I've forgotten that.")
    return _quick(ctx, "Sorry, I ran into a problem forgetting that.")


def _h_memory_forget_all(match, ctx):
    """
    "forget everything you know about me" -- a full wipe of the RAG
    long-term memory store is destructive and irreversible, so this
    NEVER executes on the first utterance. It only arms a pending
    confirmation (the SAME confirm_state mechanism already used for
    close_app/stop_service on risky targets -- see _handle_command's
    pending-confirmation block below for where "yes"/"cancel" is
    actually resolved and rag.clear_all() is actually called).

    SCOPE NOTE: this wipes ONLY the RAG long-term memory store, not
    conversation_log (that's the existing, separate "forget everything"/
    "forget our conversation" _FORGET_WORDS path above, untouched by
    this feature) and not preferences like user_name.
    """
    ctx["confirm_state"]["pending"] = {
        "action": "forget_all_memories",
        "target": None,
        "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
    }
    return _quick(
        ctx,
        "This will permanently delete everything I've remembered about you "
        "long-term -- are you sure? Say yes or cancel.",
    )


def _h_switch_mode(match, ctx):
    """
    "switch to study mode" / "study mode on karo" -- voice-triggerable
    named persona/mode switcher. Applies a FIXED bundle of already-
    existing preference keys together (see _MODE_BUNDLES above) via
    db.set_preference() -- the exact same mechanism every other
    preference toggle in this codebase already uses. Adds NO new
    underlying behavior: "focus_mode" is already read by
    sara/orchestrator/proactive.py, "assistant_active" is already read
    at startup by sara/orchestrator/core_wiring.py, and
    "mic_sensitivity" (Gaming Mode only) is already read at startup by
    sara/orchestrator/history.py.

    A key absent from the chosen mode's bundle is left completely
    untouched -- whatever the user had set before for that key persists
    as-is (e.g. switching to Study Mode never touches mic_sensitivity).

    For Gaming Mode, if ctx has an "ears" object available (the same
    audio-input object sara/gui/app/settings.py's set_mic_sensitivity()
    already calls), the new sensitivity is ALSO applied live via
    ears.set_manual_energy_threshold(...) so it takes effect
    immediately instead of only on next restart. If "ears" isn't in
    ctx, the preference is still saved (and will be restored on next
    startup per history.py), and the spoken confirmation says so.

    The active mode name itself is persisted under one new preference
    key, "active_mode" -- purely for later recall/display, it does not
    drive any behavior on its own.
    """
    if not match or not match.lastindex:
        return None
    raw_mode = match.group(1).strip().lower()
    mode_name = _MODE_ALIASES.get(raw_mode, raw_mode)
    bundle = _MODE_BUNDLES.get(mode_name)
    if bundle is None:
        return _quick(ctx, "I don't recognize that mode.")

    db = ctx.get("db")
    if db is None or not hasattr(db, "set_preference"):
        return _quick(ctx, "Sorry, I can't save mode changes right now.")

    try:
        for key, value in bundle.items():
            db.set_preference(key, value)
        db.set_preference("active_mode", mode_name)
    except Exception as e:
        print(f"[Mode] set_preference failed while switching to '{mode_name}': {e}")
        return _quick(ctx, "Sorry, I ran into a problem switching modes.")

    confirmation = _MODE_CONFIRMATIONS[mode_name]

    if "mic_sensitivity" in bundle:
        ears = ctx.get("ears")
        applied_live = False
        if ears is not None:
            try:
                value = int(bundle["mic_sensitivity"])
                threshold = max(100, 1000 - (value * 9))
                if hasattr(ears, "set_manual_energy_threshold"):
                    ears.set_manual_energy_threshold(threshold)
                    applied_live = True
                elif hasattr(ears, "energy_threshold"):
                    ears.energy_threshold = threshold
                    applied_live = True
            except Exception as e:
                print(f"[Mode] live mic sensitivity update failed: {e}")
        if not applied_live:
            confirmation += " Mic sensitivity change will apply after a restart."

    return _quick(ctx, confirmation)


def _h_calendar_today(match, ctx):
    """
    "aaj ka schedule batao" / "what's on my calendar today" -- reads
    today's real Google Calendar events via sara/tools/calendar.py and
    speaks a short human-readable summary. _ack() first since this is a
    real network call to the Calendar API, not an instant local lookup.
    """
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    events = calendar_tools.get_today_events()
    if not events:
        status = calendar_tools.get_calendar_status()
        if not status.get("connected"):
            return _quick(
                ctx,
                "Calendar isn't connected yet. Add credentials.json and try again.",
            )
        return _quick(ctx, "You have nothing on your calendar today.")

    lines = []
    for event in events[:5]:
        summary = event.get("summary") or "an event"
        start_iso = event.get("start", "")
        try:
            start_dt = datetime.fromisoformat(start_iso)
            when = start_dt.strftime("%I:%M %p")
        except (TypeError, ValueError):
            when = ""
        lines.append(f"{summary}{f' at {when}' if when else ''}")

    return _quick(ctx, "Here's today's schedule: " + "; ".join(lines) + ".")


def _h_calendar_create(match, ctx):
    """
    "kal 3 baje meeting set karo" / "schedule a meeting tomorrow at 5pm
    called <title>" -- parses the natural-language time via dateparser
    (exact same restricted-language pattern as sara/tools/reminders.py's
    add_reminder()) and creates a real Google Calendar event via
    sara/tools/calendar.py. ACCURACY FIRST: if the time can't be
    confidently parsed, this never guesses -- it asks the user to
    rephrase instead of creating an event at the wrong time.
    """
    if not match:
        return None

    groups = match.groupdict() if match.groupdict() else {}
    when_text = (groups.get("when") or "").strip()
    title = (groups.get("title") or "").strip()

    if not when_text:
        return _quick(ctx, "When would you like to schedule that meeting?")

    _ack(ctx)
    ctx["ui_update"]("status", "thinking")

    start_dt = dateparser.parse(
        when_text,
        languages=_CALENDAR_DATEPARSER_LANGUAGES,
        settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
    )

    if not start_dt:
        return _quick(
            ctx, f"I couldn't understand the time '{when_text}'. Could you rephrase it?"
        )

    if start_dt <= datetime.now():
        return _quick(ctx, "That time has already passed. Please give me a future time.")

    end_dt = start_dt + timedelta(minutes=_DEFAULT_MEETING_DURATION_MINUTES)
    result = calendar_tools.create_event(title or "Meeting", start_dt, end_dt)
    return _quick(ctx, result.get("message", "Sorry, I couldn't create that event."))


def _h_run_routine(match, ctx):
    if not match:
        return None
    requested = (match.group(1) or "").strip()
    if not requested:
        return _quick(ctx, "Which routine should I run?")

    db = ctx.get("db")
    resolved = routines.resolve_routine_name(db, requested)
    if not resolved:
        return _quick(ctx, f"I couldn't find a routine called '{requested}'.")

    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    # run_routine() already speaks every step in order as it runs (see
    # sara/core/routines.py's module docstring) -- do NOT speak these
    # again here, just surface each one in the transcript.
    outcomes = routines.run_routine(resolved, ctx)

    spoken_texts = []
    for outcome in outcomes:
        text = outcome.get("text")
        if not text:
            continue
        try:
            ctx["ui_update"]("transcript", "sara", text)
        except Exception as e:
            print(f"[Routines] transcript push failed: {e}")
        spoken_texts.append(text)

    return " ".join(spoken_texts) if spoken_texts else "Routine finished."

def _h_notify_on_file(match, ctx):
    """
    "tell me when the download finishes" / "jab download complete ho
    jaye batana" -- arms (or re-arms) a single watch on the user's
    Downloads folder via the background NotificationWatcher
    (sara/orchestrator/notifications.py). Only one watch is active at a
    time: calling this again while a watch is already running silently
    REPLACES the previous target -- it never crashes and never silently
    no-ops -- see NotificationWatcher.watch_for_next_file()'s docstring.

    notifications.get_watcher() is handed ctx["tts"]/ctx["ui_update"] so
    it can lazily create the process-wide singleton if
    sara/gui/app/bootstrap.py's init_watcher() call somehow hasn't run
    yet (defensive only -- in normal operation the singleton already
    exists by the time any voice command reaches this handler).
    """
    watcher = notifications.get_watcher(ctx["tts"], ctx["ui_update"])
    if watcher is None:
        return _quick(ctx, "Sorry, file notifications aren't available right now.")
    if not getattr(Config, "NOTIFICATIONS_ENABLED", True):
        return _quick(
            ctx, "File notifications are turned off right now, so I can't watch for that."
        )
    result = watcher.watch_for_next_file()
    return _quick(ctx, result)


_INTENT_HANDLERS = {
    "switch_mode": _h_switch_mode,
    "undo_setting_change": _h_undo_setting_change,
    "reminder_add": _h_reminder_add,
    "reminder_list": _h_reminder_list,
    "reminder_cancel": _h_reminder_cancel,
    "set_timer": _h_set_timer,
    "take_note": _h_take_note,
    "read_notes": _h_read_notes,
    "clear_notes": _h_clear_notes,
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


# Auto-discovers and registers every skill in sara/skills/ (Daily
# Briefing, Notes Q&A, and any future drop-in skill file) via
# register_intent()/register_handler() above. Imported here, at the
# bottom of this module, specifically so both functions already exist by
# the time sara/skills/__init__.py runs its discovery loop. Wrapped in
# try/except for the same reason the RAG/tool_router imports above are:
# a missing or broken skill must degrade to "that one skill doesn't
# work", never to "the app won't start".
try:
    import importlib

    importlib.import_module("sara.skills")
except Exception as _skills_import_err:  # noqa: BLE001
    print(f"[Core] sara.skills unavailable, plugin skills disabled: {_skills_import_err}")


# ── Chat-route selection (LATENCY FIX) ──────────────────────────────────
# Before this, a "chat"-intent message (i.e. the fast-path regex matcher
# found nothing) could cost up to THREE sequential LLM round-trips:
#
#   1. try_plan_and_execute()          -> planner LLM call
#   2. resolve_tool_call()             -> tool-router LLM call
#   3. brain.generate_response_stream() -> final chat LLM call
#
# because the planner block and the tool-router block were two INDEPENDENT
# `if` statements: whenever the planner engaged and then returned None (no
# usable plan, or a plan that produced nothing), execution fell straight
# through into the tool-router, and from there into plain chat.
#
# _route_chat_message() makes those two routing stages MUTUALLY EXCLUSIVE.
# It is a pure string heuristic -- zero LLM calls, zero I/O -- that picks
# exactly ONE of:
#
#   "plan" -> only the planner is attempted. If it yields nothing we go
#             straight to plain chat; the tool-router is NOT tried.
#   "tool" -> only the single-tool LLM resolver is attempted. Same deal:
#             no plan attempt, and a miss falls straight to plain chat.
#   "chat" -> neither routing stage runs at all. A conversational message
#             ("what do you think about X", "tell me a joke") now costs
#             exactly ONE LLM call instead of two.
#
# Worst case therefore drops from 3 sequential LLM calls to 2, and the
# common conversational case drops from 2 to 1.
#
# Trade-off, stated plainly: a message that the heuristic routes to "tool"
# but that was really a multi-step request no longer gets a second chance
# at the planner (and vice versa). That is the deliberate price of the
# latency win -- the fallback in both cases is a normal chat answer, not
# an error.

# Upper word count for anything to be considered a command at all. Real
# tool requests are short imperatives ("open chrome", "remind me at 6 to
# call mom"); a long sentence is nearly always conversation.
_TOOL_SIGNAL_MAX_WORDS = 16

# Action verbs that signal "do something", EN + Hinglish. Matched only at
# a word boundary so "opening hours" / "search engine kya hai" don't trip
# it purely by containing the substring.
_TOOL_SIGNAL_RE = re.compile(
    r"\b("
    r"open|launch|start|run|close|quit|kill|stop|restart|switch"
    r"|play|pause|resume|skip|next|mute|unmute"
    r"|search|google|youtube|look\s+up|find|download"
    r"|set|change|increase|decrease|raise|lower|turn"
    r"|remind|schedule|book|create|make|add|delete|remove|clear"
    r"|send|message|call|email|type|press|copy|paste"
    r"|screenshot|lock|shutdown|sleep|restart"
    r"|kholo|khol|chalao|chala|band|bajao|baja|dhundo|dhoondo"
    r"|bhejo|likho|banao|karo|kar\s+do|set\s+karo|yaad\s+dila"
    r")\b",
    re.IGNORECASE,
)

# Conversational openers that OVERRIDE the verb match above. "what is the
# best way to open a jar" contains "open" but is obviously a question, not
# a command -- these keep such messages out of the tool-router entirely.
_CHAT_SIGNAL_RE = re.compile(
    r"^\s*("
    r"who|what|why|how|when|which|whose|whom"
    r"|tell\s+me|explain|describe|define|summar|compare|suggest|recommend"
    r"|do\s+you|are\s+you|can\s+you\s+explain|should\s+i|is\s+it|was\s+it"
    r"|kya|kyun|kyu|kaise|kaun|kab|kahan|batao|bata|samjhao|samjha"
    r")\b",
    re.IGNORECASE,
)


def _route_chat_message(user_input: str) -> str:
    """
    Decide which SINGLE routing stage (if any) a "chat"-intent message gets.

    Returns exactly one of "plan", "tool", or "chat". Pure function: makes
    no LLM call, touches no network, no DB, no shared state -- so it is
    safe to call on every chat turn and trivially unit-testable.

    Order matters. The plan check runs first because a genuine multi-action
    message ("open chrome and then play some music") would ALSO match the
    tool-verb heuristic below, and the planner is the correct handler for
    it; the tool-router could only ever execute the first of the two.
    """
    text = (user_input or "").strip()
    if not text:
        return "chat"

    # ── 1. Multi-step plan? ─────────────────────────────────────────────
    planning_available = (
        _HAS_PLANNING
        and try_plan_and_execute is not None
        and should_attempt_plan is not None
        and getattr(Config, "PLANNING_ENABLED", True)
    )
    if planning_available:
        try:
            wants_plan = should_attempt_plan(text)
        except TypeError:
            # Tolerate an older/newer signature that also takes Config,
            # rather than hard-failing the whole routing decision on it.
            try:
                wants_plan = should_attempt_plan(text, Config)
            except Exception:  # noqa: BLE001
                wants_plan = False
        except Exception:  # noqa: BLE001
            # A broken trigger must degrade to "no plan", never to a
            # crashed command -- same defensive posture as every other
            # optional-feature call site in this module.
            wants_plan = False
        if wants_plan:
            return "plan"

    # ── 2. Single tool? ─────────────────────────────────────────────────
    tool_router_available = (
        getattr(Config, "TOOL_CALLING_ENABLED", True)
        and resolve_tool_call is not None
        and build_fake_match is not None
        and bool(TOOL_NAME_TO_INTENT)
    )
    if not tool_router_available:
        return "chat"

    if len(text.split()) > _TOOL_SIGNAL_MAX_WORDS:
        return "chat"
    if _CHAT_SIGNAL_RE.match(text):
        return "chat"
    if _TOOL_SIGNAL_RE.search(text):
        return "tool"

    # ── 3. Neither. Straight to plain chat, one LLM call total. ─────────
    return "chat"


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

    Raises RuntimeError (never returns None) on any failure -- the
    executor in sara.core.planning.executor treats any raised exception
    from this callback identically to a tool function raising directly,
    triggering its existing retry/skip/partial-success logic.

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
        result = tool_handler(fake_match, ctx)
        if result is None:
            raise RuntimeError(
                f"Handler for intent '{mapped_intent}' (tool '{tool_name}') "
                f"returned no result."
            )
        return result

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
        if result is not None:
            # AUDIT LOG (NEW): handler ran and produced a spoken result --
            # one "success" entry.
            _log_action(db, "intent", intent, "success")
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
    if intent == "chat":
        chat_route = _route_chat_message(user_input)

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
                if plan_outcome is not None:
                    return _quick(ctx, plan_outcome.final_message)
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
                        if tool_result is not None:
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

    ui_update("status", "thinking")
    try:
        stream = brain.generate_response_stream(user_input)
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