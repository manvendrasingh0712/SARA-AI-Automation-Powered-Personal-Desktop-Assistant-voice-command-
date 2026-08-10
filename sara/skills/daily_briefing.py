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

OPTIMIZATIONS (this revision)
----------------------------------------
1. Weather and news are now fetched CONCURRENTLY (ThreadPoolExecutor)
   instead of one-after-another. Both are independent network calls,
   each with its own timeout via _call_with_timeout(); sequentially
   that meant a worst case of (weather_timeout + news_timeout) before
   the briefing could even start speaking. Now it's
   max(weather_timeout, news_timeout) instead. Reminders are looked up
   locally (fast DB read) while the two network calls are in flight,
   instead of after them.
2. _weather_line() / _news_line() now catch and log unexpected
   exceptions instead of letting one bad call take down the whole
   briefing -- _call_with_timeout() is documented to already return a
   friendly string rather than raise, but this is a defensive second
   layer so a future change to that helper (or an unrelated bug)
   degrades to "skip this one line" instead of crashing handle().
3. handle() no longer assumes ctx["ui_update"] / ctx["tts"] are
   present -- it uses ctx.get(...) and guards each call, so a missing
   or misbehaving UI/TTS hook can't crash the whole intent handler
   (the briefing text is still returned either way).
4. tts.speak() is now wrapped in try/except and logged on failure
   (e.g. audio device error) instead of raising out of handle() --
   the caller still gets the briefing text back even if audio failed.
5. Greeting is now time-of-day aware (morning / afternoon / evening)
   instead of a binary "before/after noon" check.
6. _reminders_line() no longer silently swallows per-item errors --
   malformed reminder entries are skipped individually (and logged at
   debug level) rather than the whole line failing or crashing.
7. If more than _MAX_REMINDERS_SPOKEN reminders are due, the spoken
   line now says "...and N more" instead of silently showing only the
   first 5 with a total count that doesn't match what's read out.
8. If every single line (weather, reminders, news) comes back empty
   (e.g. total network outage), handle() now returns a clear spoken
   fallback instead of an empty/greeting-only string.
9. Magic numbers (reminder lookahead window, how many reminders to
   read aloud, default location) are now named constants at the top
   of the file instead of being inline literals.
10. Added module-level logging, consistent with sara/tools/web.py, so
    briefing failures are visible in logs instead of disappearing.

None of the above change PATTERNS, GATE, INTENT_NAME, or the overall
shape/wording of a normal successful briefing.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import Config
from sara.orchestrator.network_utils import _call_with_timeout
from sara.tools import web as web_tools

logger = logging.getLogger(__name__)

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

# OPTIMIZATION 9: named constants instead of inline magic numbers/literals.
_DEFAULT_LOCATION = "Ajmer,IN"
_REMINDERS_LOOKAHEAD_MINUTES = 24 * 60
_MAX_REMINDERS_SPOKEN = 5
_ALL_FAILED_FALLBACK_MSG = "Sorry, I couldn't put together your briefing right now."


def _weather_line() -> str:
    location = getattr(Config, "DAILY_BRIEFING_LOCATION", _DEFAULT_LOCATION)
    try:
        # _call_with_timeout already returns a friendly string (never
        # raises) on timeout/failure — see
        # sara/orchestrator/network_utils.py. The try/except here is a
        # defensive second layer (OPTIMIZATION 2), not the primary
        # safety net.
        return _call_with_timeout(web_tools.get_weather, location)
    except Exception as e:
        logger.error("Daily briefing: weather fetch failed: %s", e)
        return ""


def _news_line() -> str:
    try:
        return _call_with_timeout(web_tools.get_news, "", 1)
    except Exception as e:
        logger.error("Daily briefing: news fetch failed: %s", e)
        return ""


def _reminders_line(reminders) -> str:
    if reminders is None or not hasattr(reminders, "get_upcoming"):
        return ""
    try:
        upcoming = reminders.get_upcoming(_REMINDERS_LOOKAHEAD_MINUTES)
    except Exception as e:
        logger.error("Daily briefing: reminders fetch failed: %s", e)
        upcoming = []

    if not upcoming:
        return "No reminders due in the next day."

    # OPTIMIZATION 6: skip individual malformed entries instead of
    # letting one bad reminder blow up (or silently blank) the whole line.
    texts: list[str] = []
    for r in upcoming[:_MAX_REMINDERS_SPOKEN]:
        try:
            item_text = (r.get("text") or "").strip()
        except AttributeError:
            logger.debug("Skipping malformed reminder entry: %r", r)
            item_text = ""
        if item_text:
            texts.append(item_text)

    if not texts:
        return f"You have {len(upcoming)} reminder(s) coming up."

    items = "; ".join(texts)
    remaining = len(upcoming) - len(texts)
    # OPTIMIZATION 7: don't claim a total count that doesn't match what
    # was actually read out when there are more than _MAX_REMINDERS_SPOKEN.
    if remaining > 0:
        return f"You have {len(upcoming)} reminder(s) coming up: {items}, and {remaining} more."
    return f"You have {len(upcoming)} reminder(s) coming up: {items}."


def _greeting() -> str:
    """
    OPTIMIZATION 5: time-of-day aware greeting instead of a binary
    before/after-noon check.
    """
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning!"
    if hour < 17:
        return "Good afternoon!"
    if hour < 21:
        return "Good evening!"
    return "Here's your briefing."


def handle(match, ctx):
    """
    Gathers weather, reminders, and news, and speaks them as one
    combined reply — same _quick()-style shape (status -> speak ->
    return the text) as every handler in
    sara/orchestrator/intent_handlers.py, just inlined here since that
    module's private _quick() isn't imported across the package boundary
    to keep this skill fully self-contained.

    OPTIMIZATION 1: weather and news are independent network calls, so
    they're fetched concurrently instead of one after another, while
    reminders (a local/fast lookup) happens in the same window rather
    than being tacked on afterward.
    """
    # OPTIMIZATION 3: don't assume these keys exist in ctx.
    ui_update = ctx.get("ui_update")
    tts = ctx.get("tts")

    if ui_update:
        try:
            ui_update("status", "thinking")
        except Exception:
            logger.debug("daily_briefing: ui_update('thinking') failed.", exc_info=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        weather_future = pool.submit(_weather_line)
        news_future = pool.submit(_news_line)
        # Runs locally while the two network calls above are in flight.
        reminders_text = _reminders_line(ctx.get("reminders"))
        weather_text = weather_future.result()
        news_text = news_future.result()

    parts = [_greeting(), weather_text, reminders_text, news_text]
    text = " ".join(p for p in parts if p)

    # OPTIMIZATION 8: if literally everything failed, say so instead of
    # speaking just a bare greeting (or nothing).
    if not any([weather_text, reminders_text, news_text]):
        text = _ALL_FAILED_FALLBACK_MSG

    if ui_update:
        try:
            ui_update("status", "speaking")
        except Exception:
            logger.debug("daily_briefing: ui_update('speaking') failed.", exc_info=True)

    if tts:
        try:
            tts.speak(text, fast=True)
        except Exception as e:
            # OPTIMIZATION 4: audio failure shouldn't prevent the caller
            # from getting the briefing text back.
            logger.error("Daily briefing: TTS speak failed: %s", e)
    else:
        logger.debug("daily_briefing: no 'tts' in ctx; skipping speech.")

    return text