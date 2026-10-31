"""
sara.audio.tts.spoken_forms
Dates, versions, units, ordinals, abbreviations, AM/PM and phone numbers -> words, BEFORE the plain-number pass in
`text_prep._normalize_numbers_for_tts`.

Audit part 2, D5. What the number pass alone produced on the snapshot:
    15/08/2026 -> "fifteen/eight/two thousand twenty-six"      31 October 2026 -> "<Devanagari number> October ..."
    v2.3.1 -> "v2.three point one"     16GB / 70kg / 5GHz left as written     512 GB -> "<Devanagari> GB"
    5th, 3rd left as written           e.g. / i.e. / Dr. left as written      3:15 PM -> "three fifteen PM"

Rules: one script per phrase. `lang` is "en", or "hi"/"hinglish" (Devanagari number words, as the number pass does), and
every word this module adds is in that same script, so a Devanagari number is never followed by a Latin unit.
Pure functions, no I/O.
"""
from __future__ import annotations

import re
from typing import Callable

_MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December")
_MONTHS_HI = ("जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर")
_MONTH_LOOKUP = {m.lower(): i for i, m in enumerate(_MONTHS_EN, 1)}
_MONTH_LOOKUP.update({m.lower()[:3]: i for i, m in enumerate(_MONTHS_EN, 1)})
_MONTH_LOOKUP["sept"] = 9

_ORD_EN = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
           9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
           15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth", 20: "twentieth",
           30: "thirtieth"}
_ONES_EN = ("", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_TENS_EN = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}

# unit -> (English singular, English plural, Hindi)
_UNITS = {
    "tb": ("terabyte", "terabytes", "टेराबाइट"), "gb": ("gigabyte", "gigabytes", "गीगाबाइट"),
    "mb": ("megabyte", "megabytes", "मेगाबाइट"), "kb": ("kilobyte", "kilobytes", "किलोबाइट"),
    "ghz": ("gigahertz", "gigahertz", "गीगाहर्ट्ज़"), "mhz": ("megahertz", "megahertz", "मेगाहर्ट्ज़"),
    "khz": ("kilohertz", "kilohertz", "किलोहर्ट्ज़"), "hz": ("hertz", "hertz", "हर्ट्ज़"),
    "kg": ("kilogram", "kilograms", "किलोग्राम"), "g": ("gram", "grams", "ग्राम"), "mg": ("milligram", "milligrams", "मिलीग्राम"),
    "km/h": ("kilometre per hour", "kilometres per hour", "किलोमीटर प्रति घंटा"), "kmph": ("kilometre per hour", "kilometres per hour", "किलोमीटर प्रति घंटा"),
    "km": ("kilometre", "kilometres", "किलोमीटर"), "cm": ("centimetre", "centimetres", "सेंटीमीटर"),
    "mm": ("millimetre", "millimetres", "मिलीमीटर"), "ml": ("millilitre", "millilitres", "मिलीलीटर"),
    "l": ("litre", "litres", "लीटर"), "mah": ("milliamp hour", "milliamp hours", "मिलीएम्प आवर"),
    "mbps": ("megabit per second", "megabits per second", "मेगाबिट प्रति सेकंड"),
}
# Single letters are only units when glued to the number ("70kg", "5g"), never after a space ("5 g" may be anything).
_GLUED_ONLY = {"g", "l"}
_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?P<num>\d+(?:\.\d+)?)(?P<sp>\s?)(?P<unit>km/h|kmph|mbps|mah|ghz|mhz|khz|tb|gb|mb|kb|kg|mg|km|cm|mm|ml|hz|g|l)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"(?<![\w.])[vV]?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(?![\w.])")
_DATE_NUM_RE = re.compile(r"(?<![\d./-])(?P<a>\d{1,2})[/\-.](?P<b>\d{1,2})[/\-.](?P<y>\d{4}|\d{2})(?![\d/-])")
_DATE_ISO_RE = re.compile(r"(?<![\d./-])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\d-])")
_MONTH_NAMES = "|".join(sorted({m for m in _MONTH_LOOKUP}, key=len, reverse=True))
_DATE_DMY_RE = re.compile(
    rf"(?<![\w])(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MONTH_NAMES})\.?(?:,?\s+(?P<y>\d{{4}}))?(?![\w])", re.IGNORECASE
)
_DATE_MDY_RE = re.compile(
    rf"(?<![\w])(?P<mon>{_MONTH_NAMES})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<y>\d{{4}}))?(?![\w\d])", re.IGNORECASE
)
_ORDINAL_RE = re.compile(r"(?<![\w])(\d{1,3})(st|nd|rd|th)(?![A-Za-z0-9])", re.IGNORECASE)
_AMPM_RE = re.compile(r"(?<![\w:])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s?(?P<ap>[AaPp])\.?[Mm]\.?(?![A-Za-z])")
_PHONE_RE = re.compile(
    r"(?P<lead>\b(?:call|phone|dial|number|mobile|contact|whatsapp|no\.?)\b[^\d+\n]{0,14}|(?:नंबर|मोबाइल|फोन)[^\d+\n]{0,10})"
    r"(?P<num>\+?\d[\d\s-]{6,16}\d)",
    re.IGNORECASE,
)


def _is_hindi(lang: str) -> bool:
    return lang in ("hi", "hinglish")


def _year_words(y: int, lang: str, int_words: Callable[[int, str], str]) -> str:
    if _is_hindi(lang):
        return int_words(y, lang)
    if y == 2000:
        return "two thousand"
    if 2001 <= y <= 2009:
        return "two thousand " + _ONES_EN[y - 2000]
    if 1000 <= y <= 2099 and y % 100:
        hi_, lo = divmod(y, 100)
        lo_w = int_words(lo, lang) if lo >= 10 else "oh " + _ONES_EN[lo]
        return f"{int_words(hi_, lang)} {lo_w}"
    return int_words(y, lang)


def _ordinal_en(n: int, int_words: Callable[[int, str], str]) -> str:
    if n in _ORD_EN:
        return _ORD_EN[n]
    if n < 100:
        tens, ones = (n // 10) * 10, n % 10
        if ones == 0:
            return _TENS_EN[tens][:-1] + "ieth"
        return f"{_TENS_EN[tens]}-{_ORD_EN[ones]}"
    return int_words(n, "en") + "th"


def _day_words(d: int, lang: str, int_words) -> str:
    return int_words(d, lang) if _is_hindi(lang) else _ordinal_en(d, int_words)


def _date_words(d: int, m: int, y, lang: str, int_words) -> str:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return ""
    month = (_MONTHS_HI if _is_hindi(lang) else _MONTHS_EN)[m - 1]
    day = _day_words(d, lang, int_words)
    year = ""
    if y:
        yy = int(y)
        if yy < 100:
            yy += 2000
        year = " " + _year_words(yy, lang, int_words)
    if _is_hindi(lang):
        return f"{day} {month}{year}"
    return f"{day} of {month}{year}"


def _ampm_words(h: int, m, ap: str, lang: str, time_words: Callable[[int, int, str], str]) -> str:
    if not (1 <= h <= 12) or (m is not None and int(m) > 59):
        return ""
    minute = int(m) if m is not None else 0
    pm = ap.lower() == "p"
    if _is_hindi(lang):
        hour24 = (h % 12) + (12 if pm else 0)
        part = ("रात" if hour24 < 4 or hour24 >= 20 else "सुबह" if hour24 < 12 else "दोपहर" if hour24 < 16 else "शाम")
        return f"{part} {time_words(h, minute, lang)}"
    return f"{time_words(h, minute, lang)} {'P' if pm else 'A'} M"


def _spell_digits(num: str, lang: str, digit_word: Callable[[str, str], str]) -> str:
    plus = "plus " if num.strip().startswith("+") and not _is_hindi(lang) else ("प्लस " if num.strip().startswith("+") else "")
    digits = re.sub(r"\D", "", num)
    groups = []
    i = 0
    while i < len(digits):
        size = 4 if len(digits) - i == 4 else 3
        groups.append(" ".join(digit_word(d, lang) for d in digits[i:i + size]))
        i += size
    return plus + ", ".join(groups)


def apply(text: str, lang: str) -> str:
    """The spoken forms. Safe to call on any text; returns it unchanged when nothing matches."""
    if not text:
        return text
    from . import text_prep as tp   # lazy: text_prep imports this module

    iw, tw, dw = tp._int_to_words, tp._time_to_words, tp._digit_word
    hindi = _is_hindi(lang)

    # abbreviations (no digits needed)
    ab = (
        (r"\be\.g\.?(?=\s|,|$)", "उदाहरण के लिए" if hindi else "for example"),
        (r"\bi\.e\.?(?=\s|,|$)", "यानी" if hindi else "that is"),
        (r"\bDr\.(?=\s)", "डॉक्टर" if hindi else "Doctor"),
        (r"\bMr\.(?=\s)", "मिस्टर" if hindi else "Mister"),
        (r"\bMrs\.(?=\s)", "मिसेज़" if hindi else "Missus"),
        (r"\bProf\.(?=\s)", "प्रोफेसर" if hindi else "Professor"),
    )
    for pat, repl in ab:
        text = re.sub(pat, repl, text)

    if not re.search(r"\d", text):
        return text

    # dates first (they contain digits that later passes would split)
    def iso(m):
        w = _date_words(int(m["d"]), int(m["m"]), m["y"], lang, iw)
        return w or m.group(0)

    def numeric(m):
        a, b = int(m["a"]), int(m["b"])
        d, mo = (b, a) if (b > 12 and a <= 12) else (a, b)   # day first, unless that cannot be a month
        w = _date_words(d, mo, m["y"], lang, iw)
        return w or m.group(0)

    def named_dmy(m):
        mo = _MONTH_LOOKUP.get(m["mon"].lower().rstrip("."))
        w = _date_words(int(m["d"]), mo, m["y"], lang, iw) if mo else ""
        return w or m.group(0)

    def named_mdy(m):
        mo = _MONTH_LOOKUP.get(m["mon"].lower().rstrip("."))
        w = _date_words(int(m["d"]), mo, m["y"], lang, iw) if mo else ""
        return w or m.group(0)

    text = _DATE_ISO_RE.sub(iso, text)
    text = _DATE_NUM_RE.sub(numeric, text)
    text = _DATE_DMY_RE.sub(named_dmy, text)
    text = _DATE_MDY_RE.sub(named_mdy, text)

    # AM / PM clock times
    def clock(m):
        w = _ampm_words(int(m["h"]), m["m"], m["ap"], lang, tw)
        return w or m.group(0)

    text = _AMPM_RE.sub(clock, text)

    # phone numbers after a cue word: digit by digit in groups
    text = _PHONE_RE.sub(lambda m: m["lead"] + _spell_digits(m["num"], lang, dw), text)

    # versions
    def version(m):
        parts = [p for p in m.groups() if p is not None]
        sep = " दशमलव " if hindi else " point "
        words = sep.join(" ".join(dw(d, lang) for d in p) if len(p) > 1 and p.startswith("0") else iw(int(p), lang) for p in parts)
        return ("वर्ज़न " if hindi else "version ") + words

    text = _VERSION_RE.sub(version, text)

    # units glued to or next to a number
    def unit(m):
        key = m["unit"].lower()
        if key in _GLUED_ONLY and m["sp"]:
            return m.group(0)
        en1, enp, hi = _UNITS[key]
        num = m["num"]
        whole, _, frac = num.partition(".")
        words = iw(int(whole), lang)
        if frac:
            words += (" दशमलव " if hindi else " point ") + " ".join(dw(d, lang) for d in frac)
        plural = not (num in ("1", "1.0") )
        return f"{words} {hi if hindi else (enp if plural else en1)}"

    text = _UNIT_RE.sub(unit, text)

    # ordinals
    def ordinal(m):
        n = int(m.group(1))
        return iw(n, lang) if hindi else _ordinal_en(n, iw)

    text = _ORDINAL_RE.sub(ordinal, text)
    return text
