"""
sara.orchestrator.command_helpers

General-purpose command-dispatch helpers shared across the fast-path
intent handlers: risky-target detection, phrase-set matching, the
memory-management / low-confidence-confirmation / undo-rollback /
modes-personas constant tables, the _quick()/_ack() speaking helpers,
the Live Activity card helpers, and the action audit log writer. Split
out of the former monolithic intent_handlers.py -- see that module's
docstring for the full feature rationale behind each section below.
"""
import random

from config import Config

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


class _LikelyMisfireReply(str):
    """
    A str subclass -- same trick ears.listen()'s TranscriptionResult
    (see core_wiring.py) already uses to carry extra signal on top of a
    plain string -- marking a fast-path handler's reply as OUR OWN "I
    matched something, but I don't trust the argument I captured" case
    (e.g. an unresolved pronoun/reference with nothing fresh to resolve
    against -- see _resolve_app_target() above), as opposed to a normal
    successful reply OR a genuine, expected failure (wrong permissions,
    app not installed, ...) that just happens to also be an error
    string.

    Every existing caller of a handler (_build_plan_dispatch_fn()'s
    _dispatch(), the chat_route == "tool" branch, _quick() itself) keeps
    working completely unchanged, since this IS a str -- only
    _handle_command()'s fast-path dispatch below additionally checks
    `isinstance(result, _LikelyMisfireReply)` to decide whether a second
    opinion from the smarter LLM tool router (_retry_via_tool_router()
    below) is worth trying before accepting this as the final answer.
    """


def _quick_likely_misfire(ctx: dict, text: str) -> _LikelyMisfireReply:
    """
    Same as _quick() (speaks `text`, updates status) but returns it
    wrapped as _LikelyMisfireReply instead of a plain str. Use this
    ONLY for "I matched something, but I don't trust the captured
    argument" replies -- never for a genuine/expected failure, or every
    real error would start paying for an extra, usually-pointless LLM
    round-trip.
    """
    spoken = _quick(ctx, text)
    return _LikelyMisfireReply(spoken)


_ACK_PHRASES = (
    "On it!",
    "Sure thing!",
    "Ek second...",
    "Done-ish, hold on!",
    "Coming right up!",
    "Ek min, karti hoon!",
    "Ho raha hai!",
    "Dhoondh rahi hoon...",
    "Just a sec!",
    "Abhi karti hoon!",
    "Got it, working on it!",
    "Ruko, dekh rahi hoon!",
    "Right away!",
    "Karti hoon, thoda wait!",
)

# ── Last-used ack phrase index (NEW) ─────────────────────────────────────
# Best-effort, non-thread-safe module-level tracker so _ack() doesn't
# repeat the same phrase twice in a row. Not critical-path -- a rare
# race just means an occasional repeat, which is fine for this UX
# nicety.
_LAST_ACK_INDEX = None


def _ack(ctx: dict) -> None:
    """
    Fires an instant, non-blocking acknowledgment so the user hears
    something immediately instead of dead silence while a genuinely
    slow action (app launch, service control, network call, screen
    description, ...) runs right after it. Picks a random phrase each
    time (avoiding an immediate repeat of the last one used) so it
    doesn't feel robotic/repetitive.

    Must NEVER raise: a TTS/UI hiccup here should never block or kill
    the actual command that follows it.
    """
    global _LAST_ACK_INDEX
    try:
        ctx["ui_update"]("status", "working")
        choices = range(len(_ACK_PHRASES))
        if _LAST_ACK_INDEX is not None and len(_ACK_PHRASES) > 1:
            choices = [i for i in choices if i != _LAST_ACK_INDEX]
        index = random.choice(list(choices))
        _LAST_ACK_INDEX = index
        ctx["tts"].speak(_ACK_PHRASES[index], fast=True, block=False)
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
def _log_action(db, action_type: str, action_name: str, outcome: str, reason: str = None) -> None:
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
        db.log_action(action_type, action_name, outcome, reason=reason, wait=False)
    except Exception as e:  # noqa: BLE001 -- audit logging must never break dispatch
        print(f"[AuditLog] log_action('{action_type}', '{action_name}') failed (non-fatal): {e}")

