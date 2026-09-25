"""
sara.orchestrator.handlers.calendar

Reading today's Google Calendar events and creating new ones. Split
out of the former monolithic intent_handlers.py -- see that module's
docstring.
"""
from datetime import datetime, timedelta
import dateparser

from sara.tools import calendar as calendar_tools

from ..command_helpers import _quick, _ack
from .._shared_state import _CALENDAR_DATEPARSER_LANGUAGES, _DEFAULT_MEETING_DURATION_MINUTES

def _h_calendar_today(match, ctx):
    """
    "aaj ka schedule batao" / "what's on my calendar today" -- reads
    today's real Google Calendar events via sara/tools/calendar.py and
    speaks a short human-readable summary. _ack() first since this is a
    real network call to the Calendar API, not an instant local lookup.
    """
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    events = calendar_tools.get_today_events()
    if not events:
        status = calendar_tools.get_calendar_status()
        if not status.get("connected"):
            return _quick(
                ctx,
                "Calendar isn't connected yet. Add credentials.json and try again.",
            )
        return _quick(ctx, "You have nothing on your calendar today.")

    lines = []
    for event in events[:5]:
        summary = event.get("summary") or "an event"
        start_iso = event.get("start", "")
        try:
            start_dt = datetime.fromisoformat(start_iso)
            when = start_dt.strftime("%I:%M %p")
        except (TypeError, ValueError):
            when = ""
        lines.append(f"{summary}{f' at {when}' if when else ''}")

    return _quick(ctx, "Here's today's schedule: " + "; ".join(lines) + ".")


def _h_calendar_create(match, ctx):
    """
    "kal 3 baje meeting set karo" / "schedule a meeting tomorrow at 5pm
    called <title>" -- parses the natural-language time via dateparser
    (exact same restricted-language pattern as sara/tools/reminders.py's
    add_reminder()) and creates a real Google Calendar event via
    sara/tools/calendar.py. ACCURACY FIRST: if the time can't be
    confidently parsed, this never guesses -- it asks the user to
    rephrase instead of creating an event at the wrong time.
    """
    if not match:
        return None

    groups = match.groupdict() if match.groupdict() else {}
    when_text = (groups.get("when") or "").strip()
    title = (groups.get("title") or "").strip()

    if not when_text:
        return _quick(ctx, "When would you like to schedule that meeting?")

    _ack(ctx)
    ctx["ui_update"]("status", "thinking")

    start_dt = dateparser.parse(
        when_text,
        languages=_CALENDAR_DATEPARSER_LANGUAGES,
        settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
    )

    if not start_dt:
        return _quick(
            ctx, f"I couldn't understand the time '{when_text}'. Could you rephrase it?"
        )

    if start_dt <= datetime.now():
        return _quick(ctx, "That time has already passed. Please give me a future time.")

    end_dt = start_dt + timedelta(minutes=_DEFAULT_MEETING_DURATION_MINUTES)
    result = calendar_tools.create_event(title or "Meeting", start_dt, end_dt)
    return _quick(ctx, result.get("message", "Sorry, I couldn't create that event."))


