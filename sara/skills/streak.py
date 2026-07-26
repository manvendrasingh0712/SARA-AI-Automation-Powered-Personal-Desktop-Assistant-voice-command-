"""
sara.skills.streak
"How many days in a row have we talked?" — reads the streak PreferencesDB
tracks via record_interaction_day() (called once per day, at wake, from
sara/orchestrator/core_wiring.py). Milestone announcements (3/7/14/30/...
days) are handled separately and proactively by
sara/orchestrator/proactive.py's streak trigger; this skill is just the
on-demand "what's my streak right now" query.
"""

INTENT_NAME = "check_streak"

PATTERNS = [
    r"(?:what'?s |check )?my (?:talk )?streak",
    r"how many days in a row",
    r"(?:mera |apna )?streak (?:kya hai|batao|kitna hai)",
]

GATE = ("streak",)


def handle(match, ctx):
    ui_update = ctx["ui_update"]
    tts = ctx["tts"]
    db = ctx.get("db")

    count = 0
    if db is not None and hasattr(db, "get_streak_count"):
        try:
            count = db.get_streak_count()
        except Exception as e:
            print(f"[Streak] get_streak_count failed: {e}")

    if count <= 1:
        text = "This is our first day talking — let's build a streak!"
    elif count < 7:
        text = f"We've talked {count} days in a row. Off to a good start!"
    else:
        text = f"We've talked {count} days in a row. Nice consistency!"

    ui_update("status", "speaking")
    tts.speak(text, fast=True)
    return text
