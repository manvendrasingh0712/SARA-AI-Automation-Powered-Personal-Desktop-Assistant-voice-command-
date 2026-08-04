"""
sara/core/routines.py
Named, ordered sequences of EXISTING tools/intents/skills ("routines") --
e.g. "good morning routine chalao" runs weather -> reminders -> news ->
daily_briefing back to back, one command triggering multiple actions.

No new action-execution mechanism is introduced here. A routine step
reuses one of the app's two existing dispatch paths:

    {"type": "simple_action", "key": "<SIMPLE_ACTIONS key>"}
        -> sara.tools.system.dispatch.SIMPLE_ACTIONS[key]() -- a plain
           zero-arg callable that returns a string and has NO TTS side
           effect of its own (see dispatch.py's module docstring). This
           module speaks its result itself (see _speak() below).

    {"type": "intent", "name": "<intent name>", "args": {...}}   # args optional
    {"type": "skill",  "name": "<skill's INTENT_NAME>", "args": {...}}  # args optional
        -> sara.orchestrator.intent_handlers._INTENT_HANDLERS[name]
           (match, ctx). Skills auto-register into this SAME dict at
           startup (see sara/skills/__init__.py), so "intent" and
           "skill" steps are executed through one identical code path
           here -- they're kept as two `type` values purely so
           sara/gui/app/routines_api.py can validate a saved step
           against the right source list (built-in intents vs. loaded
           skills) and the Settings UI can label them differently.
           IMPORTANT: every built-in handler and every skill's handle()
           already speaks its own result via ctx["tts"].speak(...)
           BEFORE returning (see intent_handlers._quick() and
           daily_briefing.handle()) -- this module never re-speaks
           those, to avoid a double-speak.

Speaking + pacing: this module speaks simple_action results (and
skipped-step notes) itself, immediately, in step order -- NOT deferred
to the caller -- so that every step's audio comes out in the same order
the steps ran in, with a short natural pause between them, instead of
being bunched up out-of-order after the whole routine finishes (intent/
skill steps speak themselves mid-loop already; simple_action steps had
nothing speaking them at all otherwise). Every returned outcome already
has `spoken: True` for exactly this reason -- callers (the voice intent
handler, the Settings "test run" button, the scheduled auto-trigger)
only need to push each step's text to the UI transcript; they should
NOT call tts.speak() on it again.

Fail-safe by design: a bad/renamed/deleted step, a handler that raises,
or a handler that declines (returns None) is skipped -- logged, spoken
as a short "couldn't complete it" note, and the rest of the routine
keeps going. run_routine() itself never raises.
"""
from __future__ import annotations

import difflib
import logging
import random
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Natural pause between steps so back-to-back TTS doesn't sound like one
# run-on sentence -- a human-paced gap, not dead silence.
_STEP_PAUSE_RANGE_S = (0.4, 0.6)

_FAIL_MESSAGE = "Skipped one step (couldn't complete it)."

# A step needs at least this close a match (0-1, difflib ratio) against a
# saved routine's name/label to count as a fuzzy hit -- keeps "run good
# morning routine" from accidentally landing on an unrelated routine.
_FUZZY_CUTOFF = 0.6


def _pause() -> None:
    time.sleep(random.uniform(*_STEP_PAUSE_RANGE_S))


def _speak(ctx: dict, text: str) -> None:
    """Speaks `text` right now via ctx['tts'], same fast/blocking shape as
    intent_handlers._quick(). Never raises -- a broken TTS call must skip
    this one step's audio, not abort the routine."""
    if not ctx or not text:
        return
    tts = ctx.get("tts")
    if tts is None:
        return
    ui_update = ctx.get("ui_update")
    if ui_update:
        try:
            ui_update("status", "speaking")
        except Exception as e:
            logger.warning("routines: ui_update failed: %s", e)
    try:
        tts.speak(text, fast=True)
    except Exception as e:
        logger.warning("routines: tts.speak failed: %s", e)


def _fail_outcome(ctx: dict, message: str = _FAIL_MESSAGE) -> dict:
    _speak(ctx, message)
    return {"text": message, "spoken": True, "ok": False}


def _build_step_match(name: str, args: dict):
    """
    Builds a fake regex-match object for an intent/skill step using the
    SAME mechanism the LLM tool-calling fallback already uses (see
    sara/core/tool_router.py's build_fake_match/_FakeMatch) -- no new
    match-object shape invented here. Returns None when the intent takes
    no argument, or isn't in build_fake_match's table, or tool_router
    itself isn't available -- every built-in handler and every skill's
    handle() already tolerates match=None (e.g. _h_news, _h_reminder_list,
    daily_briefing.handle()).
    """
    args = dict(args or {})

    # "weather" specifically needs SOME location to be useful. If the
    # routine step didn't specify one, fall back to the same default
    # location the daily_briefing skill already uses, instead of asking
    # get_weather("") to guess.
    if name == "weather" and not args.get("location"):
        try:
            from config import Config

            args["location"] = getattr(Config, "DAILY_BRIEFING_LOCATION", "Ajmer,IN")
        except Exception:
            args["location"] = "Ajmer,IN"

    try:
        from sara.core.tool_router import build_fake_match
    except Exception:
        return None

    try:
        return build_fake_match(name, args)
    except Exception as e:  # noqa: BLE001 -- a bad step must never crash the routine
        logger.warning("routines: build_fake_match(%r) failed: %s", name, e)
        return None


def _run_simple_action_step(step: dict, ctx: dict) -> dict:
    key = step.get("key")

    try:
        from sara.tools import system as system_tools
    except Exception as e:
        logger.warning("routines: could not import system_tools: %s", e)
        return _fail_outcome(ctx)

    action = system_tools.SIMPLE_ACTIONS.get(key)
    if action is None:
        logger.warning("routines: unknown SIMPLE_ACTIONS key '%s'", key)
        return _fail_outcome(ctx)

    text = action()
    if not text:
        return _fail_outcome(ctx)

    # Raw SIMPLE_ACTIONS callables just return a string -- no TTS side
    # effect of their own (only intent_handlers._handle_command's
    # fallback path speaks them, via _quick()). This module speaks it
    # here so it comes out in the right order, right after this step ran.
    _speak(ctx, text)
    return {"text": text, "spoken": True, "ok": True}


def _run_intent_or_skill_step(step: dict, ctx: dict) -> dict:
    name = step.get("name")

    # Imported lazily, inside this function, rather than at module top --
    # sara.orchestrator.intent_handlers imports THIS module (to call
    # run_routine() from its "run_routine" intent handler), so importing
    # it back at our own module top would be a circular import.
    try:
        from sara.orchestrator.intent_handlers import _INTENT_HANDLERS
    except Exception as e:
        logger.warning("routines: could not import intent_handlers: %s", e)
        return _fail_outcome(ctx)

    handler = _INTENT_HANDLERS.get(name)
    if handler is None:
        logger.warning("routines: no handler registered for '%s'", name)
        return _fail_outcome(ctx)

    match = _build_step_match(name, step.get("args") or {})
    result = handler(match, ctx)
    if not result:
        # Handler declined (e.g. missing a required arg) or returned
        # nothing useful -- not a crash, just nothing to report. It did
        # NOT speak in this case, so the fail note still needs speaking.
        return _fail_outcome(ctx)

    # The handler already spoke `result` itself before returning (every
    # built-in handler and every skill's handle() does this -- see the
    # module docstring). Do NOT speak it again here.
    return {"text": result, "spoken": True, "ok": True}


_STEP_RUNNERS = {
    "simple_action": _run_simple_action_step,
    "intent": _run_intent_or_skill_step,
    "skill": _run_intent_or_skill_step,
}


def run_routine(name: str, ctx: dict) -> list[dict]:
    """
    Runs a saved routine's steps sequentially, speaking each step's result
    (or skip-note) in order as it completes, with a short natural pause
    between steps. Never raises.

    Returns a list of {"text": str, "spoken": bool, "ok": bool} dicts, one
    per step, in order. "spoken" is always True in the returned list --
    every step's text has already been spoken (or was skipped and its
    skip-note was spoken instead) by the time this function returns.
    Callers should push each "text" to the UI transcript but must NOT
    call tts.speak() on it again. "ok" is False for a skipped/failed step.
    """
    db = ctx.get("db") if ctx else None
    definition = None
    if db is not None and hasattr(db, "get_routine"):
        try:
            definition = db.get_routine(name)
        except Exception as e:
            logger.error("run_routine: get_routine('%s') failed: %s", name, e)

    if not definition:
        message = f"I couldn't find a routine called '{name}'."
        _speak(ctx, message)
        return [{"text": message, "spoken": True, "ok": False}]

    steps = definition.get("steps") or []
    results: list[dict] = []

    for i, step in enumerate(steps):
        step_type = (step or {}).get("type") if isinstance(step, dict) else None
        runner = _STEP_RUNNERS.get(step_type)
        try:
            if runner is None:
                logger.warning(
                    "run_routine('%s') step %d has invalid/missing type %r",
                    name, i, step_type,
                )
                outcome = _fail_outcome(ctx)
            else:
                outcome = runner(step, ctx)
        except Exception as e:  # noqa: BLE001 -- one bad step must never abort the routine
            logger.warning("run_routine('%s') step %d raised: %s", name, i, e)
            print(f"[Routines] '{name}' step {i} raised: {e}")
            outcome = _fail_outcome(ctx)

        results.append(outcome)
        if i < len(steps) - 1:
            _pause()

    return results


def resolve_routine_name(db, requested_name: str) -> Optional[str]:
    """
    Exact match first (case-insensitive, against both the routine's
    `name` and its `label`); falls back to a fuzzy match (same difflib
    approach sara/audio/stt/engine.py already uses for wake-word
    matching) so "run good morning" or a slightly-misheard STT transcript
    still finds "good_morning". Returns the canonical routine `name` (the
    PreferencesDB key), or None if nothing is close enough.
    """
    if db is None or not requested_name or not hasattr(db, "list_routines"):
        return None

    try:
        all_routines = db.list_routines()
    except Exception as e:
        logger.error("resolve_routine_name: list_routines failed: %s", e)
        return None

    if not all_routines:
        return None

    requested = requested_name.strip().lower()
    if not requested:
        return None

    # lowercase candidate text (name or label) -> canonical routine name
    candidates: dict[str, str] = {}
    for routine in all_routines:
        r_name = routine.get("name")
        if not r_name:
            continue
        candidates[r_name.lower()] = r_name
        label = routine.get("label")
        if label:
            candidates[label.lower()] = r_name

    if requested in candidates:
        return candidates[requested]

    close = difflib.get_close_matches(
        requested, candidates.keys(), n=1, cutoff=_FUZZY_CUTOFF
    )
    return candidates[close[0]] if close else None