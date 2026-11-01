"""
sara.orchestrator.handlers.timers

Reminders, timers, alarms, and the stopwatch. Split out of the former
monolithic intent_handlers.py -- see that module's docstring.
"""
import re
from datetime import datetime, timedelta
from sara.core.text.lazy_import import lazy_module

dateparser = lazy_module("dateparser")  # ~0.4 s at start-up otherwise

from sara.tools.reminders import play_alarm_beep
from sara.tools import system as system_tools

from ..calc_utils import _parse_duration_to_seconds
from .._shared_state import _CALENDAR_DATEPARSER_LANGUAGES
from ..command_helpers import _quick

def _h_reminder_add(match, ctx):
    if not match:
        return None
    return _quick(ctx, ctx["reminders"].add_reminder(match.group(1), match.group(2)))


def _h_reminder_list(match, ctx):
    return _quick(ctx, ctx["reminders"].list_reminders())


def _h_reminder_cancel(match, ctx):
    return _quick(ctx, ctx["reminders"].cancel_all_reminders())


def _h_set_timer(match, ctx):
    if not match:
        return None
    duration_text = match.group(1).strip()
    seconds = _parse_duration_to_seconds(duration_text)
    if not seconds:
        return _quick(
            ctx, f"Sorry, I couldn't understand the duration '{duration_text}'."
        )

    tts, ui_update = ctx["tts"], ctx["ui_update"]

    def _timer_done(msg: str):
        try:
            play_alarm_beep(repetitions=2)
        except Exception as e:
            print(f"[Warning] alarm beep failed: {e}")
        ui_update("status", "speaking")
        tts.speak(msg, fast=True)
        ui_update("transcript", "sara", f"\u23f0 {msg}")

    return _quick(ctx, system_tools.set_timer(seconds, duration_text, _timer_done))


# "साढ़े छह" = 6:30, "सवा छह" = 6:15, "पौने सात" = 6:45 (a quarter / half before the next hour)
_CLOCK_FRACTIONS = {
    "साढ़े": 30, "साढे": 30, "sadhe": 30, "saadhe": 30, "sade": 30,
    "सवा": 15, "sawa": 15, "sava": 15,
    "पौने": -15, "paune": -15, "pone": -15, "poune": -15,
}
_CLOCK_HALVES = {"डेढ़": "1:30", "डेढ": "1:30", "dedh": "1:30", "ढाई": "2:30", "dhai": "2:30", "dhaai": "2:30"}
_DAY_TOMORROW = ("kal", "कल")
_DAY_TODAY = ("aaj", "आज")
_AM_WORDS = ("subah", "savere", "सुबह", "सवेरे")
_PM_WORDS = ("shaam", "sham", "raat", "dopahar", "dophar", "शाम", "रात", "दोपहर")
_SKIP_WORDS = ("baje", "baj", "बजे", "के", "ka", "ke", "ko")


def _spoken_clock_time(text: str) -> str:
    """"kal subah 7 baje" / "सुबह छह बजे" / "सुबह साढ़े छह बजे" -> "tomorrow 7 am" / "6:30 am" (dateparser cannot read these)."""
    from sara.core.text.spoken_numbers import words_to_digits

    from ..calc_utils import _hindi_words_to_english

    # number WORDS first ("छह" / "chhe" / "saat" -> 6 / 7), then the clock words
    words = words_to_digits(_hindi_words_to_english(text), unit_aware=False).replace("\u0901", "").split()
    day = meridiem = ""
    keep = []
    frac = None
    for w in words:
        lw = w.lower()
        if lw in _CLOCK_FRACTIONS:
            frac = _CLOCK_FRACTIONS[lw]
        elif lw in _CLOCK_HALVES:
            keep.append(_CLOCK_HALVES[lw])
        elif frac is not None and w.isdigit():
            h = int(w)
            keep.append(f"{h}:{frac}" if frac > 0 else f"{(h - 2) % 12 + 1}:45")
            frac = None
        elif lw in _DAY_TOMORROW:
            day = "tomorrow"
        elif lw in _DAY_TODAY:
            day = "today"
        elif lw in _AM_WORDS:
            meridiem = "am"
        elif lw in _PM_WORDS:
            meridiem = "pm"
        elif lw in _SKIP_WORDS:
            continue
        else:
            keep.append(w)
    out = " ".join(keep)
    if meridiem and re.search(r"\d", out) and not re.search(r"(?i)\b[ap]m\b", out):
        out = re.sub(r"(\d{1,2}(?::\d{2})?)", lambda m: m.group(1) + " " + meridiem, out, count=1)
    return (day + " " + out).strip()


def _h_set_alarm(match, ctx):
    """
    "set an alarm for 7am" / "wake me up at 6:30" -- a CLOCK-TIME alarm,
    distinct from _h_set_timer()'s DURATION-based countdown above. Parses
    the target time via dateparser (same restricted-language pattern as
    _h_calendar_create()'s dateparser.parse() call further down this
    file), computes the delay until it next occurs, and hands the
    resulting seconds off to the EXACT SAME system_tools.set_timer() /
    play_alarm_beep() mechanism _h_set_timer() uses above -- an alarm is
    just a timer computed from a clock time, so the underlying
    scheduling/beeping logic is intentionally not duplicated here.
    """
    if not match:
        return None
    time_text = match.group(1).strip()
    if not time_text:
        return _quick(ctx, "What time would you like the alarm for?")

    target_dt = dateparser.parse(
        time_text,
        languages=_CALENDAR_DATEPARSER_LANGUAGES,
        settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
    )
    if not target_dt:
        spoken = _spoken_clock_time(time_text)
        if spoken and spoken != time_text:
            target_dt = dateparser.parse(
                spoken, languages=["en"],
                settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
            )
    if not target_dt:
        return _quick(ctx, f"Sorry, I couldn't understand the time '{time_text}'.")

    now = datetime.now()
    if target_dt <= now:
        # EDGE CASE: dateparser's PREFER_DATES_FROM="future" resolves
        # ambiguous DATES (e.g. "Friday") into the future, but a bare
        # clock time with no date component (e.g. "7am") has no date to
        # roll forward -- it can still resolve to today's 7am even after
        # that's already passed. Roll it onto tomorrow ourselves rather
        # than trust that setting to cover this case (behavior here can
        # vary by dateparser version), and rather than reject it the way
        # _h_calendar_create() does above -- "wake me up at 7am" said at
        # 8am clearly means tomorrow, not "that's not possible".
        target_dt += timedelta(days=1)

    seconds = (target_dt - now).total_seconds()
    if seconds <= 0:
        # Defensive: should be unreachable after the rollover above, but
        # set_timer() has no defined behavior for a non-positive delay,
        # so guard it explicitly rather than trust the arithmetic blindly.
        return _quick(ctx, f"Sorry, I couldn't understand the time '{time_text}'.")

    label = target_dt.strftime("%I:%M %p").lstrip("0")
    tts, ui_update = ctx["tts"], ctx["ui_update"]

    def _alarm_done(msg: str):
        try:
            play_alarm_beep(repetitions=2)
        except Exception as e:
            print(f"[Warning] alarm beep failed: {e}")
        ui_update("status", "speaking")
        tts.speak(msg, fast=True)
        ui_update("transcript", "sara", f"\u23f0 {msg}")

    return _quick(ctx, system_tools.set_timer(seconds, label, _alarm_done))


def _h_start_stopwatch(match, ctx):
    reply = system_tools.start_stopwatch()
    if reply.startswith("Stopwatch started"):
        _clock_card(ctx, "started")  # the Home Clock Card follows along
    return _quick(ctx, reply)


def _h_stop_stopwatch(match, ctx):
    reply = system_tools.stop_stopwatch()
    if reply.startswith("Stopwatch stopped"):
        _clock_card(ctx, "paused")
    return _quick(ctx, reply)


def _h_lap_stopwatch(match, ctx):
    return _quick(ctx, system_tools.lap_stopwatch())


def _clock_card(ctx, status: str) -> None:
    """Best effort: mirror the voice stopwatch on the Clock Card (js/clock-card.js)."""
    try:
        ctx["ui_update"]("stopwatch_state", {"status": status})
    except Exception:
        pass
