"""
sara.orchestrator.handlers.automation

Running a named routine (sara/core/routines.py). Split out of the
former monolithic intent_handlers.py -- see that module's docstring.
"""
from sara.core import routines

from ..command_helpers import _quick, _ack

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

