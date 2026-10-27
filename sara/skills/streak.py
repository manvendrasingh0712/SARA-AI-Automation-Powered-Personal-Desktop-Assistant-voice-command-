"""
sara.skills.streak
"How many days in a row have we talked?" — reads the streak that
PreferencesDB.record_interaction_day() maintains (called once per day, at
wake, from sara/orchestrator/core_wiring.py). Milestone ANNOUNCEMENTS are
handled proactively by sara/orchestrator/proactive.py; this skill is only the
on-demand query. The milestone list itself is read from
PreferencesDB._STREAK_MILESTONES so it is defined in exactly one place.

Reads the raw `streak_count` / `streak_longest` preferences directly:
PreferencesDB.get_streak_count() swallows errors and returns 0, which made a
failed lookup indistinguishable from "day one". A missing value is now
reported neutrally ("no streak recorded yet"), never as "first day".

Shows: current streak, longest streak, distance to the next milestone,
badges earned, and a 7-day strip (a streak of N consecutive days means the
last N days are filled, so no per-day history is needed).
"""
from __future__ import annotations

from ._framework import SkillContext, SkillResult, skill

_DEFAULT_MILESTONES = (3, 7, 14, 30, 50, 100, 200, 365)


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def streak_stats(count: int, longest: int, milestones=_DEFAULT_MILESTONES) -> dict:
    """Pure function -> everything the reply and the card need."""
    longest = max(longest, count)
    milestones = sorted(milestones)
    nxt = next((m for m in milestones if m > count), None)
    prev = max([m for m in milestones if m <= count], default=0)
    if nxt is None:
        progress = 1.0
    else:
        progress = max(0.0, min(1.0, (count - prev) / float(nxt - prev)))
    week = [(6 - i) < count for i in range(7)]  # oldest -> today
    return {
        "count": count,
        "longest": longest,
        "next_milestone": nxt,
        "days_to_next": (nxt - count) if nxt else 0,
        "progress": round(progress, 3),
        "week": week,
        "badges": [m for m in milestones if m <= longest],
    }


@skill(
    name="check_streak",
    patterns=[
        r"(?:what'?s |check )?my (?:talk )?streak",
        r"how many days in a row",
        r"(?:mera |apna )?streak (?:kya hai|batao|kitna hai)",
    ],
    gate=("streak", "in a row"),
    description="Tells you how many days in a row you've talked to Sara",
    category="social",
    examples=("what's my streak", "how many days in a row", "mera streak kya hai"),
)
def handle(match, ctx: SkillContext):
    db = ctx.db
    raw_count = ctx.pref("streak_count")
    if db is None or raw_count is None:
        return SkillResult(text=ctx.t(
            "I don't have a streak recorded yet.",
            "Abhi tak koi streak record nahi hui hai.",
        ))

    count = _int(raw_count)
    milestones = getattr(db, "_STREAK_MILESTONES", _DEFAULT_MILESTONES)
    stats = streak_stats(count, _int(ctx.pref("streak_longest")), tuple(milestones))

    if count <= 1:
        text = ctx.t("This is our first day talking — let's build a streak!",
                     "Aaj hamara pehla din hai — chalo streak banate hain!")
    elif count < 7:
        text = ctx.t(f"We've talked {count} days in a row. Off to a good start!",
                     f"Hum lagataar {count} din se baat kar rahe hain. Achhi shuruaat!")
    else:
        text = ctx.t(f"We've talked {count} days in a row. Nice consistency!",
                     f"Hum lagataar {count} din se baat kar rahe hain. Kamaal ki consistency!")

    if stats["next_milestone"]:
        left = stats["days_to_next"]
        text += " " + ctx.t(
            f"{left} more day{'s' if left != 1 else ''} to reach {stats['next_milestone']}.",
            f"{stats['next_milestone']} tak bas {left} din aur.",
        )
    else:
        text += " " + ctx.t("You've passed every milestone!", "Aapne saare milestones paar kar liye!")
    if stats["longest"] > count:
        text += " " + ctx.t(f"Your longest streak is {stats['longest']} days.",
                            f"Aapki sabse lambi streak {stats['longest']} din ki hai.")

    card = dict(stats, type="streak", title=ctx.t("Talk streak", "Talk streak"))
    return SkillResult(
        text=text,
        card=card,
        chips=[ctx.t("Daily briefing", "Daily briefing"), ctx.t("Tell me a joke", "Ek joke sunao")],
    )