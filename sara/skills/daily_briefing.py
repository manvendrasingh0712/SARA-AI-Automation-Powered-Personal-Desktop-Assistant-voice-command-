"""
sara.skills.daily_briefing
"Give me my daily briefing" — weather + upcoming reminders (with times) +
an optional exam countdown + news headline(s), spoken as one summary.

How it works
------------
Weather and news are independent network calls, started CONCURRENTLY through
network_utils._call_with_status() (exact ok/failed/cancelled status, own
timeouts, circuit breaker); reminders are a fast local read. Speech is
PROGRESSIVE: greeting + weather are spoken as soon as the weather is in, while
the news is still loading. A failed section is mentioned once at the end
("I couldn't fetch the news right now."); if nothing succeeded at all the
clear fallback is spoken; a Stop press makes it stay silent.

  * Successful weather/news are cached for 10 minutes (asking twice in a row
    is instant and doesn't burn API calls). Failures are never cached.
  * Reminders show their time when the reminder dict has one
    ("5:30 PM: Physics test").
  * Exam countdown: set Config.DAILY_BRIEFING_EXAMS = [("Physics", "2027-02-20")]
    or preference `briefing_exams` (JSON list of [name, "YYYY-MM-DD"]).
  * News: Config.DAILY_BRIEFING_NEWS_COUNT (default 1) headlines; topic from
    preference `briefing_news_topic` (default: top news).
  * Replies follow the reply language (English / Hinglish).
  * Follow-up: "aur detail do" / "more headlines" within 3 minutes reads three
    fresh headlines.
Not included: calendar events (needs the calendar API from
sara/orchestrator/handlers/calendar.py) and an automatic on-wake briefing
(lives in sara/orchestrator/proactive.py).
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime

from config import Config
from sara.tools import web as web_tools

from ._framework import SkillContext, SkillResult, skill

logger = logging.getLogger(__name__)

# ── network status API (exact); heuristic fallback if network_utils isn't patched ──
_FAILURE_PREFIXES = (
    "sorry", "i couldn't", "i could not", "i can't", "couldn't", "could not",
    "can't", "cannot", "unable",
)
_FAILURE_WORDS_RE = re.compile(
    r"\b(timed out|timeout|no internet|unavailable|not reachable)\b", re.IGNORECASE
)


def _looks_failed(text: str) -> bool:
    t = text.strip().lower()
    if t.startswith(_FAILURE_PREFIXES):
        return True
    return len(t) < 100 and bool(_FAILURE_WORDS_RE.search(t))


try:
    from sara.orchestrator.network_utils import _call_with_status
except ImportError:  # network_utils not patched yet
    from sara.orchestrator.network_utils import _call_with_timeout

    def _call_with_status(fn, *args, **kwargs):
        text = _call_with_timeout(fn, *args, **kwargs)
        if isinstance(text, str):
            if text.strip().lower() == "okay, stopped.":
                return "cancelled", text
            if _looks_failed(text):
                return "failed", text
        return "ok", text


_OK, _EMPTY, _FAILED, _CANCELLED = "ok", "empty", "failed", "cancelled"
_DEFAULT_LOCATION = "Ajmer,IN"
_REMINDERS_LOOKAHEAD_MINUTES = 24 * 60
_MAX_REMINDERS_SPOKEN = 5
_FETCH_DEADLINE_S = 12
_CACHE_TTL_S = 600
_FOLLOWUP_WINDOW_S = 180

_cache: dict = {}            # key -> (timestamp, text)
_last_briefing = {"at": 0.0, "topic": ""}


# ── small pure helpers ─────────────────────────────────────────────────────

def greeting(hour: int, lang: str) -> str:
    if lang == "hinglish":
        if hour < 12:
            return "Suprabhat!"
        if hour < 17:
            return "Namaste, good afternoon!"
        if hour < 21:
            return "Good evening!"
        return "Ye raha aapka briefing."
    if hour < 12:
        return "Good morning!"
    if hour < 17:
        return "Good afternoon!"
    if hour < 21:
        return "Good evening!"
    return "Here's your briefing."


def format_time(value) -> str:
    """'5:30 PM' from an ISO string / datetime; '' if it can't be parsed."""
    dt = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            return ""
    if dt is None:
        return ""
    return dt.strftime("%I:%M %p").lstrip("0")


_TIME_KEYS = ("due_at", "due", "remind_at", "time", "when", "datetime", "trigger_time")


def reminder_items(upcoming) -> list:
    """[{'time': '5:30 PM' | '', 'text': str}] — malformed entries skipped."""
    items = []
    for r in list(upcoming or [])[:_MAX_REMINDERS_SPOKEN]:
        try:
            text = (r.get("text") or "").strip()
            when = next((format_time(r[k]) for k in _TIME_KEYS if r.get(k)), "")
        except AttributeError:
            logger.debug("Skipping malformed reminder entry: %r", r)
            continue
        if text:
            items.append({"time": when, "text": text})
    return items


def next_exam(exams, today: date):
    """Nearest exam on/after today -> (name, days_left) or None."""
    best = None
    for entry in exams or []:
        try:
            name, iso = entry[0], entry[1]
            days = (date.fromisoformat(str(iso)) - today).days
        except (TypeError, ValueError, IndexError):
            continue
        if days >= 0 and (best is None or days < best[1]):
            best = (str(name), days)
    return best


def _exams(ctx: SkillContext):
    raw = ctx.pref("briefing_exams")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except ValueError:
            pass
    return getattr(Config, "DAILY_BRIEFING_EXAMS", None) or []


# ── fetching ───────────────────────────────────────────────────────────────

def _fetch(label: str, key: str, fn, *args, use_cache: bool = True):
    now = time.time()
    if use_cache and key in _cache and now - _cache[key][0] < _CACHE_TTL_S:
        return _OK, _cache[key][1]
    try:
        status, value = _call_with_status(fn, *args)
    except Exception as e:  # noqa: BLE001
        logger.error("Daily briefing: %s fetch failed: %s", label, e)
        return _FAILED, ""
    if status == "cancelled":
        return _CANCELLED, ""
    if status != "ok":
        logger.warning("Daily briefing: %s fetch failed: %r", label, value)
        return _FAILED, ""
    text = "" if value is None else str(value).strip()
    if not text:
        return _EMPTY, ""
    _cache[key] = (now, text)
    return _OK, text


def _weather():
    location = getattr(Config, "DAILY_BRIEFING_LOCATION", _DEFAULT_LOCATION)
    return _fetch("weather", f"weather:{location}", web_tools.get_weather, location)


def _news(topic: str, count: int, use_cache: bool = True):
    return _fetch("news", f"news:{topic}:{count}", web_tools.get_news, topic, count,
                  use_cache=use_cache)


def _reminders(reminders):
    if reminders is None or not hasattr(reminders, "get_upcoming"):
        return _EMPTY, []
    try:
        return _OK, reminders.get_upcoming(_REMINDERS_LOOKAHEAD_MINUTES) or []
    except Exception as e:  # noqa: BLE001
        logger.error("Daily briefing: reminders fetch failed: %s", e)
        return _FAILED, []


def _result(future, label: str):
    try:
        return future.result(timeout=0)
    except Exception as e:  # noqa: BLE001 -- includes "not finished"
        logger.error("Daily briefing: %s did not finish: %r", label, e)
        return _FAILED, ""


# ── the skill ──────────────────────────────────────────────────────────────

@skill(
    name="daily_briefing",
    patterns=[
        r"(?:give me |what'?s )?(?:my )?daily briefing",
        r"morning briefing",
        r"brief me(?: on my day)?",
        r"aaj ka (?:update|briefing)",
        r"mera din kaisa (?:hai|rahega)",
    ],
    gate=("briefing", "brief me", "aaj ka", "mera din"),
    description="Speaks weather, reminders (with times), an exam countdown and headlines as one briefing",
    category="daily",
    examples=("give me my daily briefing", "morning briefing", "aaj ka briefing"),
)
def handle(match, ctx: SkillContext):
    ctx.status("thinking")
    greet = greeting(datetime.now().hour, ctx.lang)
    topic = ctx.pref("briefing_news_topic") or ""
    news_count = max(1, int(getattr(Config, "DAILY_BRIEFING_NEWS_COUNT", 1) or 1))

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="briefing")
    spoken: list = []
    notes: list = []
    ok_sections = 0
    card: dict = {"type": "briefing", "greeting": greet, "weather": "", "reminders": [],
                  "news": [], "exam": None}
    try:
        weather_f = pool.submit(_weather)
        news_f = pool.submit(_news, topic, news_count)
        rem_state, upcoming = _reminders(ctx.reminders)  # local, while network runs

        # Weather first: speak greeting + weather as soon as it's available.
        wait([weather_f], timeout=_FETCH_DEADLINE_S)
        w_state, w_text = _result(weather_f, "weather")
        if w_state == _CANCELLED:
            return None
        head = [greet]
        if w_state == _OK:
            ok_sections += 1
            head.append(w_text)
            card["weather"] = w_text
        elif w_state == _FAILED:
            notes.append(ctx.t("I couldn't get the weather right now.",
                               "Abhi weather nahi mil paaya."))
        if len(head) > 1:
            line = " ".join(head)
            ctx.say(line)
            spoken.append(line)
        elif ctx.cancelled:
            return None

        # Reminders (+ exam countdown).
        if rem_state == _OK:
            ok_sections += 1
            items = reminder_items(upcoming)
            card["reminders"] = items
            if not upcoming:
                line = ctx.t("No reminders due in the next day.",
                             "Agle 24 ghante mein koi reminder nahi hai.")
            elif not items:
                line = ctx.t(f"You have {len(upcoming)} reminder(s) coming up.",
                             f"Aapke {len(upcoming)} reminder aane wale hain.")
            else:
                parts = [f"{i['time']}: {i['text']}" if i["time"] else i["text"] for i in items]
                line = ctx.t(f"You have {len(upcoming)} reminder(s) coming up: {'; '.join(parts)}",
                             f"Aapke {len(upcoming)} reminder aane wale hain: {'; '.join(parts)}")
                more = len(upcoming) - len(items)
                line += (ctx.t(f", and {more} more.", f", aur {more} aur.") if more > 0 else ".")
            ctx.say(line)
            spoken.append(line)
        elif rem_state == _FAILED:
            notes.append(ctx.t("I couldn't check your reminders.",
                               "Reminders check nahi ho paaye."))

        exam = next_exam(_exams(ctx), date.today())
        if exam:
            name, days = exam
            card["exam"] = {"name": name, "days": days}
            line = (ctx.t(f"Your {name} exam is today. All the best!", f"Aaj aapka {name} ka exam hai. All the best!")
                    if days == 0 else
                    ctx.t(f"{days} day{'s' if days != 1 else ''} left for {name}.",
                          f"{name} mein {days} din bache hain."))
            ctx.say(line)
            spoken.append(line)

        # News last (it has been loading all along).
        wait([news_f], timeout=_FETCH_DEADLINE_S)
        n_state, n_text = _result(news_f, "news")
        if n_state == _CANCELLED or ctx.cancelled:
            return None
        if n_state == _OK:
            ok_sections += 1
            card["news"] = [n_text]
            ctx.say(n_text)
            spoken.append(n_text)
        elif n_state == _FAILED:
            notes.append(ctx.t("I couldn't fetch the news right now.",
                               "Abhi news nahi mil paayi."))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # never block on a stuck worker

    if ok_sections == 0:
        text = ctx.t("Sorry, I couldn't put together your briefing right now.",
                     "Sorry, abhi aapka briefing taiyaar nahi kar paayi.")
        ctx.say(text)
        return SkillResult(text=text, speak=False)

    if notes:
        tail = " ".join(notes)
        ctx.say(tail)
        spoken.append(tail)

    _last_briefing.update(at=time.time(), topic=topic)
    return SkillResult(
        text=" ".join(spoken),
        speak=False,  # already spoken progressively
        card=card,
        chips=[ctx.t("More details", "Aur detail do"), ctx.t("What's my streak?", "Mera streak kya hai")],
    )


@skill(
    name="briefing_more",
    patterns=[
        r"^\s*(?:aur|more) (?:detail|details|headlines?)(?: do| batao| please)?\s*$",
        r"^\s*(?:tell me more|aur batao)(?: about (?:the )?news)?\s*$",
    ],
    gate=("detail", "headline", "tell me more", "aur batao"),
    description="'Aur detail do' — three more headlines right after a briefing",
    category="daily",
)
def handle_more(match, ctx: SkillContext):
    if time.time() - _last_briefing["at"] > _FOLLOWUP_WINDOW_S:
        return None  # no briefing just now: not for us
    ctx.ack("One moment, fetching more.", "Ek second, aur dekhti hoon.")
    state, text = _news(_last_briefing["topic"], 3, use_cache=False)
    if state == _CANCELLED:
        return None
    if state != _OK:
        return SkillResult(text=ctx.t("I couldn't fetch more headlines right now.",
                                      "Abhi aur headlines nahi mil paayi."))
    return SkillResult(text=text, card={"type": "briefing", "greeting": "", "weather": "",
                                        "reminders": [], "news": [text], "exam": None})