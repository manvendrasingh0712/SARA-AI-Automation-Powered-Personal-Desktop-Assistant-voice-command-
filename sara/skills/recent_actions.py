"""
sara/skills/recent_actions.py

"what have you done recently" / "abhi tak kya kiya" — summarises the newest
entries of sara/core/memory.py's persistent action_log table (written from the
two dispatch chokepoints in sara/orchestrator/dispatcher.py).

  * Past-tense wording: "opened an app", "changed the volume" (a table of
    known actions, with a generic verb->past-tense fallback for new ones).
  * Repeats are grouped: "changed the volume 3 times".
  * The newest action gets a relative time ("2 minutes ago" / "2 min pehle").
  * Filters by the words in the request: "today"/"aaj" (only today's
    actions), "failed"/"fail hue" (only the ones that didn't work).
  * The card is a timeline (icon state, text, relative time, count) and there
    are chips for the common follow-ups (including "Undo my last change",
    which is the existing undo_setting_change intent).
Only the last _FETCH_LIMIT log rows are considered (the table itself is capped
by sara/core/memory.py).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

from ._framework import SkillContext, SkillResult, skill

logger = logging.getLogger("sara.skills.recent_actions")

_MAX_SPOKEN = 5
_FETCH_LIMIT = 200

_PAST = {
    "reminder_add": "set a reminder", "reminder_list": "read out your reminders",
    "reminder_cancel": "cancelled a reminder", "set_timer": "set a timer",
    "set_alarm": "set an alarm", "start_stopwatch": "started the stopwatch",
    "stop_stopwatch": "stopped the stopwatch", "lap_stopwatch": "took a lap time",
    "take_note": "saved a note", "read_notes": "read your notes",
    "clear_notes": "cleared your notes", "add_todo": "added a to-do",
    "list_todos": "read your to-dos", "complete_todo": "completed a to-do",
    "delete_todo": "deleted a to-do", "play_youtube": "played a YouTube video",
    "play_next_youtube": "skipped to the next video", "play_spotify": "played music on Spotify",
    "web_search": "searched the web", "summarize_url": "summarised a web page",
    "open_url": "opened a website", "clipboard_read": "read the clipboard",
    "clipboard_write": "copied text to the clipboard", "screenshot_describe": "described the screen",
    "calculator": "did a calculation", "system_info": "checked system info",
    "set_volume": "changed the volume", "set_brightness": "changed the brightness",
    "mute": "muted the sound", "unmute": "unmuted the sound", "open_app": "opened an app",
    "close_app": "closed an app", "typing_text": "typed some text", "press_key": "pressed a key",
    "find_file": "searched for a file", "open_file": "opened a file",
    "start_service": "started a service", "stop_service": "stopped a service",
    "restart_application": "restarted an app", "switch_to_application": "switched to an app",
    "move_resize_window": "moved a window", "always_on_top": "pinned a window on top",
    "fullscreen": "toggled fullscreen", "notify_on_file": "set up a file alert",
    "weather": "checked the weather", "news": "read the news", "time_query": "told you the time",
    "date_query": "told you the date", "memory_recall": "recalled a memory",
    "memory_forget_specific": "forgot one memory", "memory_forget_all": "wiped my memories",
    "calendar_today": "read today's calendar", "calendar_create": "created a calendar event",
    "run_routine": "ran a routine", "why_proactive": "explained a suggestion",
    "why_decision": "explained a decision", "undo_setting_change": "undid a setting change",
    "switch_mode": "switched mode", "daily_briefing": "gave your daily briefing",
    "tell_joke": "told a joke", "notes_qa": "answered from your notes",
    "notes_summary": "summarised your notes", "notes_quiz": "quizzed you from your notes",
    "notes_refresh": "refreshed your notes", "recent_actions": "listed my recent actions",
    "self_diagnostics": "ran a system check", "check_streak": "checked your streak",
    "lock_pc": "locked the PC", "open_downloads": "opened Downloads",
}
_IRREGULAR = {"set": "set", "take": "took", "put": "put", "read": "read", "run": "ran",
              "make": "made", "get": "got", "go": "went", "send": "sent", "find": "found",
              "show": "showed", "say": "said", "tell": "told", "give": "gave", "turn": "turned"}


def past_tense(action_name: str) -> str:
    """'set_volume' -> 'changed the volume'; unknown 'verb_noun' -> 'verbed noun'."""
    name = (action_name or "something").strip()
    if name in _PAST:
        return _PAST[name]
    words = name.replace("-", "_").split("_")
    verb, rest = words[0].lower(), " ".join(words[1:])
    if verb in _IRREGULAR:
        past = _IRREGULAR[verb]
    elif verb.endswith("e"):
        past = verb + "d"
    elif re.search(r"[^aeiou]y$", verb):
        past = verb[:-1] + "ied"
    else:
        past = verb + "ed"
    return f"{past} {rest}".strip()


def _parse_ts(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def relative_time(ts: Optional[datetime], now: datetime, hinglish: bool) -> str:
    if ts is None:
        return ""
    secs = max(0, int((now - ts).total_seconds()))
    if secs < 60:
        return "abhi abhi" if hinglish else "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min pehle" if hinglish else f"{mins} minute{'s' if mins != 1 else ''} ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} ghante pehle" if hinglish else f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days == 1:
        return "kal" if hinglish else "yesterday"
    return f"{days} din pehle" if hinglish else f"{days} days ago"


def group_actions(entries: list) -> list:
    """Collapses CONSECUTIVE identical (name, outcome) rows.
    Returns [{'name','outcome','count','ts'(newest)}] oldest -> newest."""
    groups: list = []
    for e in entries:
        try:
            name = e.get("action_name") or "something"
            outcome = e.get("outcome")
            ts = _parse_ts(e.get("timestamp"))
        except AttributeError:
            logger.debug("Skipping malformed action_log entry: %r", e)
            continue
        if groups and groups[-1]["name"] == name and groups[-1]["outcome"] == outcome:
            groups[-1]["count"] += 1
            groups[-1]["ts"] = ts or groups[-1]["ts"]
        else:
            groups.append({"name": name, "outcome": outcome, "count": 1, "ts": ts})
    return groups


def describe_group(g: dict, hinglish: bool) -> str:
    phrase = past_tense(g["name"])
    if g["outcome"] == "fail":
        phrase = f"ran into a problem with {g['name'].replace('_', ' ')}"
    elif g["outcome"] == "skipped":
        phrase = f"looked at {g['name'].replace('_', ' ')}"
    if g["count"] > 1:
        phrase += f" {g['count']} baar" if hinglish else f" {g['count']} times"
    return phrase


@skill(
    name="recent_actions",
    patterns=[
        r"what have you done recently",
        r"what did you do recently",
        r"what have you been doing",
        r"what did you do today",
        r"show me (?:your |the )?(?:recent )?actions?(?: log)?",
        r"(?:which|what) (?:actions? )?(?:have )?failed",
        r"abhi tak (?:tumne |aap ne )?kya kiya(?: hai)?",
        r"aaj (?:tumne )?kya kiya(?: hai)?",
        r"tumne (?:abhi tak )?kya kya kiya(?: hai)?",
        r"tumne kya kiya(?: hai)?",
        r"kaunse? (?:kaam |action )?fail hue",
    ],
    gate=("done recently", "did you do", "been doing", "action", "abhi tak", "kya kiya",
          "kya kya kiya", "failed", "fail hue", "aaj"),
    description="Summarises what Sara did recently (grouped, with times; filter by today / failures)",
    category="system",
    examples=("what have you done recently", "abhi tak kya kiya", "which actions failed"),
)
def handle(match, ctx: SkillContext):
    db = ctx.db
    if db is None or not hasattr(db, "get_recent_actions"):
        return SkillResult(text=ctx.t("I don't have an action log available right now.",
                                      "Abhi mere paas action log available nahi hai."))
    try:
        entries = db.get_recent_actions(limit=_FETCH_LIMIT) or []
    except Exception as e:  # noqa: BLE001 -- must never crash the voice loop
        logger.exception("[RecentActions] get_recent_actions failed: %s", e)
        return SkillResult(text=ctx.t("Sorry, I couldn't look up my recent actions.",
                                      "Sorry, apne recent actions nahi dekh paayi."))

    low = ctx.user_input.lower()
    only_failed = "fail" in low
    only_today = ("today" in low) or bool(re.search(r"\baaj\b", low))
    now = datetime.now()
    hinglish = ctx.lang == "hinglish"

    if only_today:
        entries = [e for e in entries
                   if (t := _parse_ts(e.get("timestamp") if isinstance(e, dict) else None))
                   and t.date() == date.today()]
    if only_failed:
        entries = [e for e in entries if isinstance(e, dict) and e.get("outcome") == "fail"]

    groups = group_actions(entries)
    if not groups:
        if only_failed:
            msg = ctx.t("Nothing has failed recently.", "Haal hi mein kuch fail nahi hua.")
        elif only_today:
            msg = ctx.t("I haven't done anything today yet.", "Aaj maine abhi tak kuch nahi kiya.")
        else:
            msg = ctx.t("I haven't logged any actions yet.", "Maine abhi tak koi action log nahi kiya.")
        return SkillResult(text=msg)

    shown = groups[-_MAX_SPOKEN:]
    phrases = [describe_group(g, hinglish) for g in shown]
    when = relative_time(shown[-1]["ts"], now, hinglish)
    if when:
        phrases[-1] += f" ({when})"
    lead = (ctx.t("Here's what failed recently: ", "Ye haal hi mein fail hua: ") if only_failed else
            ctx.t("Here's what I did today: ", "Aaj maine ye kiya: ") if only_today else
            ctx.t("Here's what I've done recently: ", "Maine haal hi mein ye kiya: "))

    timeline = [{
        "state": {"success": "ok", "fail": "fail", "skipped": "skip"}.get(g["outcome"], "unknown"),
        "text": describe_group(g, hinglish),
        "when": relative_time(g["ts"], now, hinglish),
        "count": g["count"],
    } for g in reversed(shown)]  # newest first on screen

    chips = [ctx.t("Undo my last change", "Undo my last change")]
    chips.insert(0, ctx.t("What failed?", "Kaunse fail hue?") if not only_failed
                 else ctx.t("What did you do recently?", "Abhi tak kya kiya?"))
    return SkillResult(
        text=lead + "; ".join(phrases) + ".",
        card={"type": "timeline", "title": ctx.t("Recent actions", "Recent actions"), "items": timeline},
        chips=chips,
    )