"""
sara.core.text.spoken_numbers
Spoken number words (English, Hindi/Hinglish in Roman script, Hindi in Devanagari) -> digits.

The single table of number words for the whole project: intent routing (`normalize_intent_text`), duration parsing
(`sara.orchestrator.calc_utils`) and the memory date windows (`sara.core.memory2.timeparse`) all read it.

`words_to_digits(text, unit_aware=True)` is the routing mode. Hindi "do" is "two" AND "give/do" ("laga do", "kar
do"), "ek" is "one" AND "a/the same" ("ek ghar"), and "saath" is "sixty" AND "with". With `unit_aware=True` a
number word is therefore converted only when a unit or measure word follows it ("do minute baad", "pachaas percent",
"aadha ghanta"); "laga do" and "kar do" are never touched. Words that cannot be anything else (pachaas, pandrah,
paanch ...) are converted when a unit follows too, so "open one note" stays "open one note".
`unit_aware=False` converts every number word (used for arithmetic and duration text that is already known to be a
number).

Fractions become whole minutes so the existing duration parser can read them: "aadha ghanta" -> "30 minute",
"dedh ghanta" -> "90 minute", "dhai ghante" -> "150 minute", "sawa ghanta" -> "75 minute", "sade teen ghante" ->
"210 minute", "half an hour" -> "30 minutes", "quarter of an hour" -> "15 minutes".
"""
from __future__ import annotations

import re

# ── the table ────────────────────────────────────────────────────────────
ENGLISH_ONES = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
ENGLISH_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}

# Roman-script Hindi / Hinglish (spelling variants included)
HINDI_ROMAN = {
    "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "pach": 5, "chhe": 6,
    "chhah": 6, "che": 6, "saat": 7, "sat": 7, "aath": 8, "ath": 8, "nau": 9,
    "das": 10, "dus": 10, "gyarah": 11, "gyaarah": 11, "barah": 12, "baarah": 12, "terah": 13, "tera": 13,
    "chaudah": 14, "chaudha": 14, "pandrah": 15, "pandra": 15, "solah": 16, "satrah": 17, "atharah": 18,
    "athaarah": 18, "unnis": 19, "unees": 19, "bees": 20, "bis": 20, "ikkis": 21, "baais": 22, "teis": 23,
    "chaubis": 24, "pachis": 25, "pachchis": 25, "chhabbis": 26, "sattais": 27, "atthais": 28, "untis": 29,
    "tees": 30, "tis": 30, "paintees": 35, "paintis": 35, "chalis": 40, "paintalis": 45, "paitalis": 45,
    "pachaas": 50, "pachas": 50, "pachpan": 55, "saath": 60, "sath": 60, "painsath": 65, "sattar": 70,
    "pachattar": 75, "assi": 80, "nabbe": 90, "sau": 100,
}

# Devanagari
HINDI_DEVANAGARI = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "छे": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "पच्चीस": 25, "तीस": 30, "पैंतीस": 35,
    "चालीस": 40, "पैंतालीस": 45, "पचास": 50, "पचपन": 55, "साठ": 60, "सत्तर": 70, "अस्सी": 80, "नब्बे": 90,
    "सौ": 100,
}
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Words that are also ordinary words: converted only when a unit follows (also in unit_aware mode for English).
AMBIGUOUS_HI = {"do", "ek", "saath", "sath", "दो", "एक", "साठ", "sat", "ath", "pach", "che", "tin", "tera"}

# word -> minutes, for "<fraction> ghanta"
FRACTION_HOURS = {  # value in hours
    "aadha": 0.5, "adha": 0.5, "aadhe": 0.5, "आधा": 0.5, "आधे": 0.5,
    "dedh": 1.5, "derh": 1.5, "डेढ़": 1.5, "डेढ": 1.5,
    "dhai": 2.5, "dhaai": 2.5, "ढाई": 2.5,
    "sawa": 1.25, "सवा": 1.25,
}

UNIT_WORDS = {
    "minute", "minutes", "min", "mins", "mint", "minat", "मिनट", "ghante", "ghanta", "ghanton", "ghantey", "ghnta",
    "घंटे", "घंटा", "घण्टे", "घण्टा", "hour", "hours", "hr", "hrs", "second", "seconds", "sec", "secs", "sekand",
    "सेकंड", "सेकेंड", "baje", "बजे", "din", "dino", "days", "day", "percent", "%", "pratishat", "प्रतिशत", "degree",
    "degrees", "digri", "डिग्री", "saal", "mahine", "mahina", "hafte", "hafta", "week", "weeks", "month", "months",
}
_HOUR_UNITS = {"ghante", "ghanta", "ghanton", "ghantey", "ghnta", "घंटे", "घंटा", "घण्टे", "घण्टा", "hour", "hours", "hr", "hrs"}

# the dictionaries other modules import
NUMBER_WORDS = {**ENGLISH_ONES, **ENGLISH_TENS, **HINDI_ROMAN, **HINDI_DEVANAGARI}

_TOKEN_RE = re.compile(r"\S+")


def _strip(tok: str) -> tuple[str, str, str]:
    """(leading punctuation, bare word, trailing punctuation)."""
    m = re.match(r"^([^\w%ऀ-ॿ]*)(.*?)([^\w%ऀ-ॿ]*)$", tok, re.UNICODE)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", tok, "")


def _value(word: str):
    w = word.lower()
    if w in ENGLISH_ONES and w not in ("a", "an"):
        return ENGLISH_ONES[w]
    if w in ENGLISH_ONES:
        return 1
    if w in ENGLISH_TENS:
        return ENGLISH_TENS[w]
    if w in HINDI_ROMAN:
        return HINDI_ROMAN[w]
    if word in HINDI_DEVANAGARI:
        return HINDI_DEVANAGARI[word]
    return None


def _is_unit(word: str) -> bool:
    return word.lower() in UNIT_WORDS or word.endswith("%")


def _fmt(n) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def words_to_digits(text: str, *, unit_aware: bool = True) -> str:
    """Rewrite spoken numbers as digits. See the module docstring for the unit-aware rule."""
    if not isinstance(text, str) or not text:
        return text

    text = text.translate(DEVANAGARI_DIGITS)
    text = re.sub(r"\bhalf\s+(?:an?\s+)?hour\b", "30 minutes", text, flags=re.IGNORECASE)
    text = re.sub(r"\bhalf\s+(?:a\s+)?minute\b", "30 seconds", text, flags=re.IGNORECASE)
    text = re.sub(r"\bquarter(?:\s+of)?\s+(?:an?\s+)?hour\b", "15 minutes", text, flags=re.IGNORECASE)

    toks = _TOKEN_RE.findall(text)
    spans = [m.span() for m in _TOKEN_RE.finditer(text)]
    parts: list[str] = []
    last_end = 0
    i = 0
    n = len(toks)

    def word_at(k):
        return _strip(toks[k])[1] if 0 <= k < n else ""

    while i < n:
        start = spans[i][0]
        parts.append(text[last_end:start])
        pre, word, post = _strip(toks[i])
        low = word.lower()
        replaced = None
        consumed = 1

        # fractions of an hour: "aadha ghanta", "sade teen ghante", "dedh ghanta"
        if low in FRACTION_HOURS or word in FRACTION_HOURS:
            nxt = word_at(i + 1)
            if nxt.lower() in _HOUR_UNITS:
                replaced = f"{_fmt(FRACTION_HOURS.get(low, FRACTION_HOURS.get(word)) * 60)} minute"
                consumed = 2
        elif low in ("sade", "साढ़े", "साढे"):
            nxt, unit = word_at(i + 1), word_at(i + 2)
            val = _value(nxt) if nxt else None
            if val is not None and unit.lower() in _HOUR_UNITS:
                replaced = f"{_fmt((val + 0.5) * 60)} minute"
                consumed = 3

        if replaced is None:
            val = _value(word)
            if val is not None:
                extra = 0
                if low in ENGLISH_TENS:  # "forty five"
                    nxt_low = word_at(i + 1).lower()
                    if nxt_low in ENGLISH_ONES and ENGLISH_ONES[nxt_low] < 10 and nxt_low not in ("a", "an"):
                        val += ENGLISH_ONES[nxt_low]
                        extra = 1
                unit_follows = _is_unit(word_at(i + 1 + extra))
                ambiguous = low in AMBIGUOUS_HI or word in AMBIGUOUS_HI or low in ("a", "an")
                hindi = low in HINDI_ROMAN or word in HINDI_DEVANAGARI
                if ambiguous:
                    convert = unit_follows       # "a"/"an" and the ambiguous Hindi words always need a unit
                elif unit_aware:
                    # a Hindi word that is nothing else (pachaas, pandrah ...) is a number anywhere; English
                    # words need a unit ("open one note" stays)
                    convert = unit_follows or hindi
                else:
                    convert = True
                if convert:
                    replaced = _fmt(val)
                    consumed = 1 + extra
            elif "-" in word:  # "forty-five"
                a, _, b = word.partition("-")
                if a.lower() in ENGLISH_TENS and b.lower() in ENGLISH_ONES and ENGLISH_ONES[b.lower()] < 10:
                    if not unit_aware or _is_unit(word_at(i + 1)):
                        replaced = _fmt(ENGLISH_TENS[a.lower()] + ENGLISH_ONES[b.lower()])

        if replaced is not None:
            end_tok = i + consumed - 1
            post_last = _strip(toks[end_tok])[2]
            parts.append(pre + replaced + post_last)
            last_end = spans[end_tok][1]
            i += consumed
        else:
            parts.append(text[start:spans[i][1]])
            last_end = spans[i][1]
            i += 1

    parts.append(text[last_end:])
    return "".join(parts)

