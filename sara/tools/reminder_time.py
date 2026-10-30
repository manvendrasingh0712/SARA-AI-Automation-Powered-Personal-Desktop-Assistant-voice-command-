"""Reminder times in the way people say them (English, Hinglish, Hindi).

dateparser alone (live E2E 2026-10-30) read "tomorrow at 9" as tomorrow
01:30 -- the 9 was dropped, so the reminder would ring at the wrong time --
and could not read "tomorrow morning", "tonight", "7 baje", "5 baje shaam",
"kal subah" or "2 ghante" at all. parse_when() handles those explicitly and
returns None for anything else, so the caller can still try dateparser.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

__all__ = ["parse_when", "split_trailing_day"]

# Default clock time for a part of the day said without one.
_PART_HOURS = {
    "morning": 9, "subah": 9, "सुबह": 9,
    "afternoon": 14, "dopahar": 14, "dopehar": 14, "दोपहर": 14,
    "evening": 18, "shaam": 18, "sham": 18, "शाम": 18,
    "night": 21, "tonight": 21, "raat": 21, "रात": 21,
}
_PM_PARTS = {"afternoon", "dopahar", "dopehar", "दोपहर", "evening", "shaam", "sham", "शाम",
             "night", "tonight", "raat", "रात"}
# Reminders look ahead, so "kal" means tomorrow here.
_DAY_OFFSET = {
    "today": 0, "aaj": 0, "आज": 0, "tonight": 0,
    "tomorrow": 1, "kal": 1, "कल": 1,
    "day after tomorrow": 2, "parso": 2, "parson": 2, "परसों": 2,
}
_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "minute": 60, "minutes": 60, "min": 60,
    "mins": 60, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "ghanta": 3600,
    "ghante": 3600, "ghanton": 3600, "मिनट": 60, "घंटे": 3600, "घंटा": 3600,
}

_DAY_RE = re.compile(r"\b(day after tomorrow|tomorrow|today|tonight|kal|aaj|parso|parson)\b|(कल|आज|परसों)", re.I)
_PART_RE = re.compile(r"\b(morning|afternoon|evening|night|tonight|subah|dopahar|dopehar|shaam|sham|raat)\b|(सुबह|दोपहर|शाम|रात)", re.I)
_CLOCK_RE = re.compile(
    r"(?:\bat\s+)?\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|baje|बजे)?\b", re.I)
_DURATION_RE = re.compile(
    r"^\s*(?:in\s+|after\s+)?(\d+)\s*(" + "|".join(sorted(_UNIT_SECONDS, key=len, reverse=True))
    + r")\b(?:\s*(?:baad|me|mein|में|बाद))?\s*$", re.I)


def _first(m: Optional[re.Match]) -> Optional[str]:
    if not m:
        return None
    return (m.group(1) or m.group(2) or "").lower() or None


def parse_when(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """The moment ``text`` names, or None when it is not a form handled here."""
    now = now or datetime.now()
    raw = " ".join(str(text or "").split())
    if not raw:
        return None

    dm = _DURATION_RE.match(raw)
    if dm:
        return now + timedelta(seconds=int(dm.group(1)) * _UNIT_SECONDS[dm.group(2).lower()])

    day = _first(_DAY_RE.search(raw))
    part = _first(_PART_RE.search(raw))
    clock = _CLOCK_RE.search(raw)
    if clock and not (clock.group(3) or clock.group(2) or "at" in raw[clock.start():clock.start() + 3].lower()
                      or day or part):
        clock = None  # a bare number with nothing that marks it as a time
    if not (day or part or clock):
        return None

    offset = _DAY_OFFSET.get(day, 0) if day else 0
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        suffix = (clock.group(3) or "").lower().replace(".", "")
        if hour > 23 or minute > 59:
            return None
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        elif suffix not in ("am", "pm") and hour <= 12 and part in _PM_PARTS and hour < 12:
            hour += 12
        elif suffix not in ("am", "pm") and hour < 12 and not part and day is None:
            # "7 baje" with nothing else: the next 7 o'clock, morning or evening.
            first = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if first <= now:
                first = first.replace(hour=hour + 12)
            if first <= now:
                first = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=1)
            return first
    else:
        hour, minute = _PART_HOURS.get(part, 9), 0

    due = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now and not day:
        due += timedelta(days=1)  # "at 9" / "evening" already passed today -> next one
    return due


_TRAILING_DAY = re.compile(
    r"\s+((?:day after tomorrow|tomorrow|today|tonight|kal|aaj|parso)"
    r"(?:\s+(?:morning|afternoon|evening|night|subah|shaam|raat))?)\s*$", re.I)


def split_trailing_day(message: str) -> tuple[str, str]:
    """("call mom tomorrow", ...) -> ("call mom", "tomorrow"): the router's
    "remind me to X at Y" pattern leaves the day inside the task text."""
    m = _TRAILING_DAY.search(message or "")
    if not m:
        return message, ""
    return message[: m.start()].rstrip(), m.group(1)
