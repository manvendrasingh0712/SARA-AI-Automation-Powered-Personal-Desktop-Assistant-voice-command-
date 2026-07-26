"""
sara.skills.daily_briefing
"Give me my daily briefing" — combines weather, upcoming reminders, and
one news headline into a single spoken summary.

This is the reference example for sara/skills/'s plugin architecture:
this file is the ONLY thing that had to be written to add this
capability. sara/skills/__init__.py auto-discovers it at startup and
wires INTENT_NAME/PATTERNS/GATE/handle() into the live intent matcher via
sara.core.intent.register_intent() and
sara.orchestrator.intent_handlers.register_handler() — no existing file
needed editing.
"""
from datetime import datetime

from config import Config
from sara.orchestrator.network_utils import _call_with_timeout
from sara.tools import web as web_tools

INTENT_NAME = "daily_briefing"

PATTERNS = [
    r"(?:give me |what'?s )?(?:my )?daily briefing",
    r"morning briefing",
    r"brief me(?: on my day)?",
    r"aaj ka (?:update|briefing)",
    r"mera din kaisa (?:hai|rahega)",
]

# Cheap pre-filter — same convention as sara/core/intent/patterns.py's
# _INTENT_GATES: at least one of these substrings must appear before the
# (more expensive) regex patterns above are even tried.
GATE = ("briefing", "brief me", "aaj ka", "mera din")


def _weather_line() -> str:
    # _call_with_timeout already returns a friendly string (never raises)
    # on timeout/failure — see sara/orchestrator/network_utils.py.
    location = getattr(Config, "DAILY_BRIEFING_LOCATION", "Ajmer,IN")
    return _call_with_timeout(web_tools.get_weather, location)


def _reminders_line(reminders) -> str:
    if reminders is None or not hasattr(reminders, "get_upcoming"):
        return ""
    try:
        upcoming = reminders.get_upcoming(24 * 60)  # next 24 hours
    except Exception:
        upcoming = []
    if not upcoming:
        return "No reminders due in the next day."
    items = "; ".join(r.get("text", "") for r in upcoming[:5])
    return f"You have {len(upcoming)} reminder(s) coming up: {items}."


def _news_line() -> str:
    return _call_with_timeout(web_tools.get_news, "", 1)


def handle(match, ctx):
    """
    Gathers all three pieces up front (each already has its own
    per-call network timeout via _call_with_timeout) and speaks them as
    one combined reply — same _quick()-style shape (status -> speak ->
    return the text) as every handler in
    sara/orchestrator/intent_handlers.py, just inlined here since that
    module's private _quick() isn't imported across the package boundary
    to keep this skill fully self-contained.
    """
    ui_update = ctx["ui_update"]
    tts = ctx["tts"]
    ui_update("status", "thinking")

    greeting = "Good morning!" if datetime.now().hour < 12 else "Here's your briefing."
    parts = [
        greeting,
        _weather_line(),
        _reminders_line(ctx.get("reminders")),
        _news_line(),
    ]
    text = " ".join(p for p in parts if p)

    ui_update("status", "speaking")
    tts.speak(text, fast=True)
    return text
