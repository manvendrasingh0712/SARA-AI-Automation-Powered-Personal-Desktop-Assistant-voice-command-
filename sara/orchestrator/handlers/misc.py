"""
sara.orchestrator.handlers.misc

Explainable-AI "why did you say/do that" handlers, the single-step
settings-change undo/rollback, and the Modes/Personas switcher. Split
out of the former monolithic intent_handlers.py -- see that module's
docstring's UNDO / ROLLBACK and MODES / PERSONAS sections.
"""
import sqlite3
import time
from concurrent.futures import TimeoutError as _FutureTimeoutError

from ..command_helpers import (
    _quick,
    _ack,
    _UNDO_REASON_MARKER,
    _MODE_ALIASES,
    _MODE_BUNDLES,
    _MODE_CONFIRMATIONS,
)
from .._shared_state import logger
from sara.tools import system as system_tools

_UNDO_CLOSE_TTL_S = 300  # 5 min -- separate, longer window than the 120s follow-up TTL in context_tracking.py, since "undo" is a deliberate recall, not a quick follow-up

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
        except (sqlite3.Error, _FutureTimeoutError) as e:
            print(f"[Proactive] get_last_proactive_event failed: {e}")
        except Exception as e:
            logger.exception(
                "[Proactive] get_last_proactive_event raised an unexpected error "
                "type (this may be a bug): %s", e
            )
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
    except (sqlite3.Error, _FutureTimeoutError) as e:
        print(f"[Memory] find_decision_by_query failed: {e}")
        return _quick(ctx, "Sorry, I couldn't look that up right now.")
    except Exception as e:
        logger.exception(
            "[Memory] find_decision_by_query raised an unexpected error type "
            "(this may be a bug): %s", e
        )
        return _quick(ctx, "Sorry, I couldn't look that up right now.")

    if entry:
        reason = entry.get("reason") or "I don't have a specific reason recorded for that change."
        return _quick(ctx, reason)

    # NEW: not a settings change -- check action_log for a planner-driven
    # action whose plan goal matches, before falling back to "not found".
    if db is not None and hasattr(db, "find_action_by_query"):
        try:
            action_entry = db.find_action_by_query(query_text)
        except (sqlite3.Error, _FutureTimeoutError) as e:
            print(f"[Memory] find_action_by_query failed: {e}")
            action_entry = None
        except Exception as e:
            logger.exception(
                "[Memory] find_action_by_query raised an unexpected error type "
                "(this may be a bug): %s", e
            )
            action_entry = None

        if action_entry:
            action_reason = action_entry.get("reason")
            if action_reason:
                return _quick(
                    ctx, f"Maine ye kiya kyunki tumne kaha tha: '{action_reason}'."
                )

    return _quick(
        ctx, f"I couldn't find a recent change matching '{query_text}'."
    )


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
    # CLOSE-APP UNDO (NEW, v1 scope: close only): checked first, before
    # the settings-undo logic below -- if the user very recently closed
    # an app (context_tracking.py's "last_closed_app" slot, set by
    # handlers/system.py's _h_close_app()), treat "undo" as "reopen it"
    # rather than looking at decision_log at all. Consumes the slot on
    # success so a second "undo" right after falls through to the
    # settings logic instead of re-reopening the same app forever.
    recent_entities = ctx.get("context_state", {}).get("recent_entities", {})
    closed_entry = recent_entities.get("last_closed_app")
    if closed_entry and (time.time() - closed_entry.get("ts", 0)) <= _UNDO_CLOSE_TTL_S:
        app_name = closed_entry["value"]
        del recent_entities["last_closed_app"]
        _ack(ctx)
        return _quick(ctx, system_tools.open_application(app_name))

    db = ctx.get("db")
    if db is None or not hasattr(db, "get_last_decision") or not hasattr(db, "set_preference"):
        return _quick(ctx, "I don't have any change history available right now.")

    try:
        entry = db.get_last_decision()
    except (sqlite3.Error, _FutureTimeoutError) as e:
        print(f"[Undo] get_last_decision failed: {e}")
        return _quick(ctx, "Sorry, I couldn't look up your last change right now.")
    except Exception as e:
        logger.exception(
            "[Undo] get_last_decision raised an unexpected error type "
            "(this may be a bug): %s", e
        )
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
    except (sqlite3.Error, _FutureTimeoutError) as e:
        print(f"[Undo] set_preference('{setting_key}') failed: {e}")
        ok = False
    except Exception as e:
        logger.exception(
            "[Undo] set_preference('%s') raised an unexpected error type "
            "(this may be a bug): %s", setting_key, e
        )
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
    except (sqlite3.Error, _FutureTimeoutError) as e:
        print(f"[Mode] set_preference failed while switching to '{mode_name}': {e}")
        return _quick(ctx, "Sorry, I ran into a problem switching modes.")
    except Exception as e:
        logger.exception(
            "[Mode] set_preference raised an unexpected error type while "
            "switching to '%s' (this may be a bug): %s", mode_name, e
        )
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


