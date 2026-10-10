"""Deterministic temporal-expression parser (English, Hinglish, Hindi); no network."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from .lexicon import tokens

__all__ = ["parse_window"]

_WEEKDAY_RAW = (
    "monday somvar somvaar सोमवार",
    "tuesday mangalvar mangalvaar मंगलवार",
    "wednesday budhvar budhvaar बुधवार",
    "thursday guruvar guruvaar brihaspativar वीरवार गुरुवार बृहस्पतिवार",
    "friday shukravar shukravaar शुक्रवार",
    "saturday shanivar shanivaar शनिवार",
    "sunday ravivar ravivaar itwar रविवार",
)
_WD: dict[str, int] = {}
for _idx, _line in enumerate(_WEEKDAY_RAW):
    for _word in _line.split():
        for _tok in tokens(_word):
            _WD[_tok] = _idx
            if _tok.isascii() and _tok.endswith("day"):
                _WD[_tok + "s"] = _idx


def _p(*phrases: str) -> tuple[str, ...]:
    return tuple(" ".join(tokens(p)) for p in phrases)


def _w(raw: str) -> frozenset[str]:
    return frozenset(t for w in raw.split() for t in tokens(w))


_LAST_WEEK = _p("last week", "previous week", "pichle hafte", "pichle hafta", "pichhle hafte",
                "pichle saptah", "पिछले हफ्ते", "पिछले हफ्ता", "पिछले सप्ताह")
_THIS_WEEK = _p("this week", "is hafte", "iss hafte", "इस हफ्ते", "इस सप्ताह")
_LAST_MONTH = _p("last month", "previous month", "pichle mahine", "pichhle mahine", "पिछले महीने")
_THIS_MONTH = _p("this month", "is mahine", "iss mahine", "इस महीने")
_DAY_BEFORE = _p("day before yesterday", "parso", "parson", "परसों", "परसो")
_DAY_AFTER = _p("day after tomorrow")
_YESTERDAY = _w("yesterday")
_TOMORROW = _w("tomorrow")
_KAL = _w("kal कल")
_TODAY = _w("today aaj आज tonight")
_FUTURE = _w("will going gonna hoga hogi honge karunga karungi karenge jaunga jaungi jayenge "
             "upcoming scheduled होगा होगी होंगे करूँगा करूंगा करूँगी जाऊँगा जाऊंगा")
_NEXT = _w("next agle agla अगले अगला")
_THIS = _w("this is iss इस")
_AGO = _w("ago pehle pahle पहले")
_UNIT = {
    **dict.fromkeys(_w("day days din दिन"), "d"),
    **dict.fromkeys(_w("week weeks hafte hafta haftey हफ्ते हफ्ता हफ़्ते सप्ताह"), "w"),
    **dict.fromkeys(_w("month months mahine mahina महीने महीना"), "m"),
}
_NUMWORD = {"a": 1, "an": 1, "one": 1, "ek": 1, "एक": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
            "twelve": 12}


def _num(tok: str) -> int | None:
    if tok in _NUMWORD:
        return _NUMWORD[tok]
    try:
        return int(tok) if tok.isdigit() else None
    except ValueError:
        return None


def _ts(day: date) -> float:
    return datetime(day.year, day.month, day.day).timestamp()


def _span(first: date, after: date) -> tuple[float, float]:
    return _ts(first), _ts(after)


def _day(day: date) -> tuple[float, float]:
    return _span(day, day + timedelta(days=1))


def _month_back(today: date, months: int) -> tuple[float, float]:
    idx = today.year * 12 + (today.month - 1) - months
    year, month = divmod(idx, 12)
    first = date(year, month + 1, 1)
    return _span(first, first + timedelta(days=calendar.monthrange(year, month + 1)[1]))


def _has(padded: str, phrases: tuple[str, ...]) -> bool:
    return any(f" {p} " in padded for p in phrases)


def _parse(query: str, now: float) -> tuple[float, float] | None:
    toks = tokens(query)
    if not toks:
        return None
    tset = set(toks)
    padded = " " + " ".join(toks) + " "
    today = datetime.fromtimestamp(now).date()
    monday = today - timedelta(days=today.weekday())
    for i in range(len(toks) - 2):
        count = _num(toks[i])
        if count is None or toks[i + 2] not in _AGO:
            continue
        unit = toks[i + 1]
        if unit in _WD:
            return _day(monday - timedelta(days=7 * count - _WD[unit]))
        if unit in _UNIT:
            kind = _UNIT[unit]
            if kind == "d":
                return _day(today - timedelta(days=count))
            if kind == "w":
                start = monday - timedelta(days=7 * count)
                return _span(start, start + timedelta(days=7))
            return _month_back(today, count)
    weekday = next((_WD[t] for t in toks if t in _WD), None)
    if weekday is not None:
        if _has(padded, _LAST_WEEK):
            return _day(monday - timedelta(days=7 - weekday))
        if tset & _NEXT:
            return _day(today + timedelta(days=(weekday - today.weekday()) % 7 or 7))
        if tset & _THIS:
            return _day(monday + timedelta(days=weekday))
        return _day(today - timedelta(days=(today.weekday() - weekday) % 7 or 7))
    future = bool(tset & _FUTURE)
    if _has(padded, _DAY_AFTER):
        return _day(today + timedelta(days=2))
    if _has(padded, _DAY_BEFORE):
        return _day(today + timedelta(days=2 if future else -2))
    if tset & _TOMORROW:
        return _day(today + timedelta(days=1))
    if tset & _YESTERDAY:
        return _day(today - timedelta(days=1))
    if tset & _KAL:
        return _day(today + timedelta(days=1 if future else -1))
    if tset & _TODAY:
        return _day(today)
    if _has(padded, _LAST_WEEK):
        return _span(monday - timedelta(days=7), monday)
    if _has(padded, _THIS_WEEK):
        return _span(monday, monday + timedelta(days=7))
    if _has(padded, _LAST_MONTH):
        return _month_back(today, 1)
    if _has(padded, _THIS_MONTH):
        return _month_back(today, 0)
    return None


def parse_window(query: str, now: float) -> tuple[float, float] | None:
    """Epoch window ``[t0, t1)`` named by ``query`` relative to ``now``; None when absent."""
    try:
        return _parse(str(query or ""), float(now))
    except Exception:  # noqa: BLE001
        return None