"""
sara.orchestrator.handlers.automation

Running a named routine (sara/core/routines.py). Split out of the
former monolithic intent_handlers.py -- see that module's docstring.
"""
from sara.core import routines

from ..command_helpers import _quick, _ack

_ROUTINE_FILLER = frozenset(
    "my the mera meri mere apna apni apne wala wali wale ka ki ke ko "
    "\u092e\u0947\u0930\u093e \u092e\u0947\u0930\u0940 \u092e\u0947\u0930\u0947 \u0905\u092a\u0928\u093e \u0905\u092a\u0928\u0940 \u0915\u093e \u0915\u0940 \u0915\u0947 \u0935\u093e\u0932\u093e \u0935\u093e\u0932\u0940".split()
)
_ROUTINE_WORDS_HI = {"\u0938\u0941\u092c\u0939": "morning", "\u0936\u093e\u092e": "evening", "\u0930\u093e\u0924": "night"}


def _clean_routine_name(requested: str) -> str:
    """"meri evening" -> "evening", "\u0938\u0941\u092c\u0939 \u0915\u093e" -> "morning": the pattern's capture still carries Hindi filler words."""
    words = [w for w in requested.split() if w.lower() not in _ROUTINE_FILLER]
    words = [_ROUTINE_WORDS_HI.get(w, w) for w in words]
    return " ".join(words) or requested


def _h_run_routine(match, ctx):
    if not match:
        return None
    requested = (match.group(1) or "").strip()
    if not requested:
        return _quick(ctx, "Which routine should I run?")

    db = ctx.get("db")
    resolved = routines.resolve_routine_name(db, requested)
    if not resolved:
        cleaned = _clean_routine_name(requested)
        if cleaned != requested:
            requested = cleaned
            resolved = routines.resolve_routine_name(db, cleaned)
    if not resolved:
        try:
            has_any = bool(db.list_routines())
        except Exception:
            has_any = True
        if not has_any:
            return _quick(ctx, "You have no routines yet. Create one in the Automation page, then I can run it by name.")
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

