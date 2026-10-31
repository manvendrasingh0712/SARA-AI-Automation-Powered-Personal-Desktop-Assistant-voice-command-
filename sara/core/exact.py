"""
sara.core.exact
Exact answers computed in code instead of by a 4B language model (audit part 2, O3): unit conversion, date
arithmetic, percentages, square roots. `answer(text, today=None)` returns a one-line spoken answer, or None when the
sentence is not one of these (the caller then lets the normal chat path handle it). Pure; no I/O.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Optional

# ── units ────────────────────────────────────────────────────────────────
# unit -> (dimension, factor to the dimension's base unit); temperature is handled separately.
_UNITS = {
    # length (m)
    "km": ("len", 1000.0), "kilometer": ("len", 1000.0), "kilometers": ("len", 1000.0), "kilometre": ("len", 1000.0), "kilometres": ("len", 1000.0),
    "m": ("len", 1.0), "meter": ("len", 1.0), "meters": ("len", 1.0), "metre": ("len", 1.0), "metres": ("len", 1.0),
    "cm": ("len", 0.01), "centimeter": ("len", 0.01), "centimeters": ("len", 0.01), "mm": ("len", 0.001),
    "mile": ("len", 1609.344), "miles": ("len", 1609.344), "foot": ("len", 0.3048), "feet": ("len", 0.3048), "ft": ("len", 0.3048),
    "inch": ("len", 0.0254), "inches": ("len", 0.0254), "yard": ("len", 0.9144), "yards": ("len", 0.9144),
    # mass (kg)
    "kg": ("mass", 1.0), "kilo": ("mass", 1.0), "kilos": ("mass", 1.0), "kilogram": ("mass", 1.0), "kilograms": ("mass", 1.0),
    "g": ("mass", 0.001), "gram": ("mass", 0.001), "grams": ("mass", 0.001),
    "pound": ("mass", 0.45359237), "pounds": ("mass", 0.45359237), "lb": ("mass", 0.45359237), "lbs": ("mass", 0.45359237),
    "ounce": ("mass", 0.028349523), "ounces": ("mass", 0.028349523), "oz": ("mass", 0.028349523),
    # volume (l)
    "l": ("vol", 1.0), "liter": ("vol", 1.0), "liters": ("vol", 1.0), "litre": ("vol", 1.0), "litres": ("vol", 1.0),
    "ml": ("vol", 0.001), "milliliter": ("vol", 0.001), "milliliters": ("vol", 0.001),
    "gallon": ("vol", 3.785411784), "gallons": ("vol", 3.785411784), "cup": ("vol", 0.2365882365), "cups": ("vol", 0.2365882365),
    # time (s)
    "second": ("time", 1.0), "seconds": ("time", 1.0), "sec": ("time", 1.0), "minute": ("time", 60.0), "minutes": ("time", 60.0),
    "min": ("time", 60.0), "mins": ("time", 60.0), "hour": ("time", 3600.0), "hours": ("time", 3600.0), "hr": ("time", 3600.0),
    "ghanta": ("time", 3600.0), "ghante": ("time", 3600.0), "day": ("time", 86400.0), "days": ("time", 86400.0), "din": ("time", 86400.0),
    "week": ("time", 604800.0), "weeks": ("time", 604800.0), "hafte": ("time", 604800.0),
    # speed (m/s)
    "kmph": ("speed", 1 / 3.6), "km/h": ("speed", 1 / 3.6), "kph": ("speed", 1 / 3.6), "mph": ("speed", 0.44704), "m/s": ("speed", 1.0),
    # data (bytes)
    "kb": ("data", 1024.0), "mb": ("data", 1024.0 ** 2), "gb": ("data", 1024.0 ** 3), "tb": ("data", 1024.0 ** 4),
}
_TEMP = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}
_NAMES = {"len": "", "mass": "", "vol": "", "time": "", "speed": "", "data": ""}
_SPOKEN = {
    "km": "kilometres", "kilometer": "kilometres", "kilometers": "kilometres", "kilometre": "kilometres", "kilometres": "kilometres",
    "m": "metres", "meter": "metres", "meters": "metres", "metre": "metres", "metres": "metres", "cm": "centimetres", "mm": "millimetres",
    "mile": "miles", "miles": "miles", "foot": "feet", "feet": "feet", "ft": "feet", "inch": "inches", "inches": "inches", "yard": "yards",
    "yards": "yards", "kg": "kilograms", "kilo": "kilograms", "kilos": "kilograms", "kilogram": "kilograms", "kilograms": "kilograms",
    "g": "grams", "gram": "grams", "grams": "grams", "pound": "pounds", "pounds": "pounds", "lb": "pounds", "lbs": "pounds",
    "ounce": "ounces", "ounces": "ounces", "oz": "ounces", "l": "litres", "liter": "litres", "liters": "litres", "litre": "litres",
    "litres": "litres", "ml": "millilitres", "milliliter": "millilitres", "milliliters": "millilitres", "gallon": "gallons",
    "gallons": "gallons", "cup": "cups", "cups": "cups", "second": "seconds", "seconds": "seconds", "sec": "seconds", "minute": "minutes",
    "minutes": "minutes", "min": "minutes", "mins": "minutes", "hour": "hours", "hours": "hours", "hr": "hours", "ghanta": "hours",
    "ghante": "hours", "day": "days", "days": "days", "din": "days", "week": "weeks", "weeks": "weeks", "hafte": "weeks",
    "kmph": "kilometres per hour", "km/h": "kilometres per hour", "kph": "kilometres per hour", "mph": "miles per hour",
    "m/s": "metres per second", "kb": "kilobytes", "mb": "megabytes", "gb": "gigabytes", "tb": "terabytes",
}


def _name(unit: str, value: float) -> str:
    """Spoken unit name, singular for exactly 1 ("1 hour", "1 foot")."""
    plural = _SPOKEN.get(unit, unit)
    if abs(value - 1.0) > 1e-9:
        return plural
    words = plural.split(" ")
    head = {"feet": "foot", "inches": "inch"}.get(words[0], words[0][:-1] if words[0].endswith("s") and words[0] != "kilohertz" else words[0])
    return " ".join([head] + words[1:])


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _temp(value: float, a: str, b: str) -> float:
    c = value if a == "c" else (value - 32) * 5 / 9 if a == "f" else value - 273.15
    return c if b == "c" else c * 9 / 5 + 32 if b == "f" else c + 273.15


_NUM = r"(-?\d+(?:\.\d+)?)"
_UNIT = r"([a-zA-Z]+(?:/[a-zA-Z]+)?)"
_CONV_RES = (
    re.compile(rf"^\W*(?:please\s+)?(?:convert\s+)?{_NUM}\s*{_UNIT}\s+(?:in|to|into|ko|mein|me)\s+{_UNIT}\W*$", re.I),
    re.compile(rf"^\W*(?:convert\s+)?{_NUM}\s*{_UNIT}\s+(?:ko\s+)?{_UNIT}\s+(?:mein|me)\s+(?:convert\s+)?(?:karo|kar\s+do|badlo)?\W*$", re.I),
    re.compile(rf"^\W*{_NUM}\s*{_UNIT}\s+(?:kitne|kitni|kitna)\s+{_UNIT}\s*(?:hote\s+hain|hota\s+hai|hoti\s+hai|hain|hai)?\W*$", re.I),
    re.compile(rf"^\W*how\s+many\s+{_UNIT}\s+(?:are\s+)?(?:in|is)\s+{_NUM}\s*{_UNIT}\W*$", re.I),
    re.compile(rf"^\W*how\s+many\s+{_UNIT}\s+(?:are\s+there\s+)?in\s+(?:a|an|one)\s+{_UNIT}\W*$", re.I),
)


def convert(text: str) -> Optional[str]:
    t = text.strip()
    for i, rx in enumerate(_CONV_RES):
        m = rx.match(t)
        if not m:
            continue
        g = m.groups()
        if i == 3:
            unit_to, value, unit_from = g[0], float(g[1]), g[2]
        elif i == 4:
            unit_to, value, unit_from = g[0], 1.0, g[1]
        else:
            value, unit_from, unit_to = float(g[0]), g[1], g[2]
        a, b = unit_from.lower(), unit_to.lower()
        if a in _TEMP and b in _TEMP and a != b and (a in ("c", "f", "k") or len(a) > 1):
            out = _temp(value, _TEMP[a], _TEMP[b])
            names = {"c": "degrees Celsius", "f": "degrees Fahrenheit", "k": "kelvin"}
            return f"{_fmt(value)} {names[_TEMP[a]]} is {_fmt(out)} {names[_TEMP[b]]}."
        if a in _UNITS and b in _UNITS and _UNITS[a][0] == _UNITS[b][0]:
            out = value * _UNITS[a][1] / _UNITS[b][1]
            return f"{_fmt(value)} {_name(a, value)} is {_fmt(out)} {_name(b, out)}."
    return None


# ── square roots, percentages ───────────────────────────────────────────────
_SQRT_RES = (
    re.compile(rf"^\W*(?:what(?:'s| is)\s+)?(?:the\s+)?square\s+root\s+of\s+{_NUM}\W*$", re.I),
    re.compile(rf"^\W*{_NUM}\s+ka\s+(?:square\s+root|vargmool)(?:\s+(?:batao|kya\s+hai|kitna\s+hai))?\W*$", re.I),
)


def square_root(text: str) -> Optional[str]:
    for rx in _SQRT_RES:
        m = rx.match(text.strip())
        if m:
            v = float(m.group(1))
            if v < 0:
                return "A negative number has no real square root."
            return f"The square root of {_fmt(v)} is {_fmt(math.sqrt(v))}."
    return None


_PCT_OUT_OF = re.compile(rf"^\W*(?:what\s+)?(?:percentage|percent|kitna\s+percent)\s+(?:is|of)\s+{_NUM}\s+out\s+of\s+{_NUM}\W*$", re.I)
_PCT_OUT_OF_HI = re.compile(rf"^\W*{_NUM}\s+out\s+of\s+{_NUM}\s+(?:ka\s+)?(?:kitna\s+)?(?:percent|percentage)\W*$", re.I)
_TIP = re.compile(rf"^\W*{_NUM}\s*(?:%|percent)\s+(?:tip|gst|tax|discount|vat)\s+(?:on|of|par)\s+{_NUM}\W*$", re.I)


def percent(text: str) -> Optional[str]:
    t = text.strip()
    m = _PCT_OUT_OF.match(t) or _PCT_OUT_OF_HI.match(t)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if b == 0:
            return "That is a division by zero, so it has no answer."
        return f"{_fmt(a)} out of {_fmt(b)} is {_fmt(a / b * 100)} percent."
    m = _TIP.match(t)
    if m:
        p, base = float(m.group(1)), float(m.group(2))
        what = re.search(r"(tip|gst|tax|discount|vat)", t, re.I).group(1).lower()
        amount = base * p / 100
        total = base - amount if what == "discount" else base + amount
        return f"{_fmt(p)} percent {what} on {_fmt(base)} is {_fmt(amount)}, so the total is {_fmt(total)}."
    return None


# ── dates ─────────────────────────────────────────────────────────────────
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def _spoken_date(d: date) -> str:
    return f"{_WEEKDAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year}"


_REL = {"kal": 1, "tomorrow": 1, "parso": 2, "aaj": 0, "today": 0, "yesterday": -1}
_DAYS_FROM = re.compile(rf"^\W*(?:what(?:'s| is)\s+(?:the\s+)?date\s+)?{_NUM}\s+days?\s+(?:from\s+(?:today|now)|later)(?:\s+(?:is|will\s+be))?\W*$", re.I)
_DAYS_HI = re.compile(rf"^\W*aaj\s+se\s+{_NUM}\s+din\s+(?:baad|pehle)\s+(?:kya\s+(?:date|tarikh)\s+(?:hogi|thi)|kaun\s+sa\s+din\s+(?:hoga|tha))?\W*$", re.I)
_TOMORROW_DATE = re.compile(r"^\W*(kal|parso|aaj)\s+(?:kitni|kya)\s+(?:tarikh|date)\s+(?:hogi|hai|thi)?\W*$", re.I)
_TOMORROW_DAY = re.compile(r"^\W*(kal|parso|aaj)\s+kaun\s+sa\s+din\s+(?:hoga|hai|tha)?\W*$", re.I)
_WHAT_DAY = re.compile(rf"^\W*what\s+day\s+(?:of\s+the\s+week\s+)?is\s+(?:it\s+on\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]+)\s*(\d{{4}})?\W*$", re.I)
_DAYS_IN_MONTH = re.compile(r"^\W*how\s+many\s+days\s+(?:are\s+)?in\s+([A-Za-z]+)(?:\s+(\d{4}))?\W*$", re.I)
_DAYS_IN_MONTH_HI = re.compile(r"^\W*([A-Za-z]+)\s+(?:mein|me)\s+kitne\s+din(?:\s+(?:hote\s+hain|hain))?\W*$", re.I)


def dates(text: str, today: Optional[date] = None) -> Optional[str]:
    today = today or date.today()
    t = text.strip()
    m = _DAYS_FROM.match(t)
    if m:
        d = today + timedelta(days=int(float(m.group(1))))
        return f"That is {_spoken_date(d)}."
    m = _DAYS_HI.match(t)
    if m:
        n = int(float(m.group(1)))
        d = today - timedelta(days=n) if "pehle" in t.lower() else today + timedelta(days=n)
        return f"Woh {_spoken_date(d)} hoga." if "pehle" not in t.lower() else f"Woh {_spoken_date(d)} tha."
    m = _TOMORROW_DATE.match(t)
    if m:
        d = today + timedelta(days=_REL[m.group(1).lower()])
        return f"{_spoken_date(d)}."
    m = _TOMORROW_DAY.match(t)
    if m:
        d = today + timedelta(days=_REL[m.group(1).lower()])
        return f"{_WEEKDAYS[d.weekday()]}."
    m = _WHAT_DAY.match(t)
    if m:
        mon = next((i for i, n in enumerate(_MONTHS, 1) if n.lower().startswith(m.group(2).lower()[:3])), 0)
        try:
            d = date(int(m.group(3) or today.year), mon, int(m.group(1)))
        except ValueError:
            return None
        return f"{_spoken_date(d)}."
    m = _DAYS_IN_MONTH.match(t) or _DAYS_IN_MONTH_HI.match(t)
    if m:
        mon = next((i for i, n in enumerate(_MONTHS, 1) if n.lower().startswith(m.group(1).lower()[:3]) and len(m.group(1)) >= 3), 0)
        if mon:
            year = int(m.groups()[1] or today.year) if m.lastindex and m.lastindex > 1 and m.group(2) else today.year
            nxt = date(year + (mon == 12), (mon % 12) + 1, 1)
            return f"{_MONTHS[mon - 1]} {year} has {(nxt - date(year, mon, 1)).days} days."
    return None


def answer(text: str, today: Optional[date] = None) -> Optional[str]:
    for fn in (convert, square_root, percent):
        out = fn(text)
        if out:
            return out
    return dates(text, today)
