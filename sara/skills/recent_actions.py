"""
sara/skills/recent_actions.py

"what have you done recently" / "abhi tak kya kiya hai" -- speaks a
short summary of the last few entries in sara/core/memory.py's new
action_log table (written from the two dispatch chokepoints in
sara/orchestrator/intent_handlers.py -- see that module's docstring).

Implemented as a skill (not a hand-added intent_handlers.py entry) so
it plugs into the existing sara/skills/ auto-discovery mechanism
(INTENT_NAME + PATTERNS + handle()) instead of needing a new regex
pattern hand-added to sara/core/intent's pattern table -- keeping this
feature self-contained in one file, same as streak.py.
"""
import logging

logger = logging.getLogger("sara.skills.recent_actions")

INTENT_NAME = "recent_actions"
PATTERNS = [
    r"what have you done recently",
    r"what did you do recently",
    r"what have you been doing",
    r"show me (?:your |the )?(?:recent )?actions?(?: log)?",
    r"abhi tak (?:tumne |aap ne )?kya kiya (?:hai)?",
    r"tumne (?:abhi tak )?kya kya kiya(?: hai)?",
    r"tumne kya kiya (?:hai)?",
]
GATE = (
    "done recently", "did you do", "been doing", "action", "abhi tak",
    "kya kiya", "kya kya kiya",
)

_MAX_SPOKEN = 5


def _describe(entry: dict) -> str:
    name = (entry.get("action_name") or "something").replace("_", " ").strip()
    outcome = entry.get("outcome")
    if outcome == "success":
        return name
    if outcome == "fail":
        return f"tried {name} but it didn't work"
    if outcome == "skipped":
        return f"looked at {name}"
    return name


def handle(match, ctx):
    tts = ctx["tts"]
    db = ctx.get("db")

    if db is None or not hasattr(db, "get_recent_actions"):
        text = "I don't have an action log available right now."
        tts.speak(text, fast=True)
        return text

    try:
        recent = db.get_recent_actions(limit=_MAX_SPOKEN)
    except Exception as e:  # noqa: BLE001 -- must never crash the voice loop
        logger.exception(f"[RecentActions] get_recent_actions failed: {e}")
        text = "Sorry, I couldn't look up my recent actions."
        tts.speak(text, fast=True)
        return text

    if not recent:
        text = "I haven't done anything yet in this session."
        tts.speak(text, fast=True)
        return text

    parts = [_describe(entry) for entry in recent]
    text = "Here's what I've done recently: " + "; ".join(parts) + "."
    tts.speak(text, fast=True)
    return text