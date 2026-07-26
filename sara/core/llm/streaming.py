"""
sara.core.llm.streaming
Token/sentence-boundary helpers used while streaming LLM output for TTS.
"""
from __future__ import annotations



import re
import threading
import time
from collections import deque
from typing import Iterator, List, NamedTuple, Optional, Tuple

from config import Config

# ══════════════════════════════════════════════════════════════════════
# Module-level compiled regexes
# ══════════════════════════════════════════════════════════════════════

_SENT_END_RE = re.compile(r"([.!?।॥])\s+")
_MD_STRIP_RE = re.compile(r"(\*{1,3}|#{1,6}|`{1,3}|_{1,2}|~~|\|\|)")
_CLAUSE_RE = re.compile(r",\s+(?:and|but|so|yet|or|nor)\s+", re.IGNORECASE)
_SEMI_RE = re.compile(r";\s+")

_ABBREV_SET: frozenset[str] = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "vs",
        "rev",
        "gen",
        "sgt",
        "cpl",
        "pvt",
        "lt",
        "col",
        "maj",
        "capt",
        "cmdr",
        "etc",
        "approx",
        "dept",
        "est",
        "govt",
        "inc",
        "ltd",
        "corp",
        "fig",
        "vol",
        "pp",
        "no",
        "st",
        "ave",
        "blvd",
        "rd",
        "rs",
        "usd",
        "eur",
        "gbp",
        "kg",
        "km",
        "cm",
        "mm",
        "mg",
        "lb",
        "oz",
        "ft",
        "yd",
        "mph",
        "kmh",
        "kph",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    }
)


# ══════════════════════════════════════════════════════════════════════
# Localized fallback messages (v7) — used instead of raw exception text
# anywhere a reply could reach speak_stream()/speak() and be read aloud.
# ══════════════════════════════════════════════════════════════════════

_STREAM_FAIL_MESSAGES = {
    "english": "Sorry, I'm having trouble reaching my brain right now — could you try that again in a moment?",
    "hindi": "Maafi chahta hoon, abhi thodi dikkat aa rahi hai — thodi der baad phir try karo.",
    "hinglish": "Sorry yaar, abhi thoda glitch ho raha hai — thodi der baad dobara try karna.",
}

_STREAM_INTERRUPTED_MESSAGES = {
    "english": "Hmm, my connection glitched mid-thought — that's all I've got for now.",
    "hindi": "Hmm, beech mein connection mein dikkat aa gayi — abhi itna hi keh sakta hoon.",
    "hinglish": "Hmm, beech mein thoda glitch ho gaya — abhi bas itna hi.",
}


# ══════════════════════════════════════════════════════════════════════
# Language-aware system prompt templates
# ══════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════
# Public result types
# ══════════════════════════════════════════════════════════════════════


class WarmupResult(NamedTuple):
    ok: bool
    error: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════
# Text utilities
# ══════════════════════════════════════════════════════════════════════


def _last_word_before(text: str, pos: int) -> str:
    i = pos - 1
    while i >= 0 and text[i] in ".!?, \t":
        i -= 1
    end = i + 1
    while i >= 0 and not text[i].isspace():
        i -= 1
    return text[i + 1 : end].lower().rstrip(".")


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []

    boundaries: list[int] = []

    for m in _SENT_END_RE.finditer(text):
        punct = m.group(1)
        pos = m.start()
        after = m.end()

        if punct in ("!", "?", "।", "॥"):
            boundaries.append(after)
            continue

        last = _last_word_before(text, pos)

        if last in _ABBREV_SET:
            continue

        if len(last) == 1 and last.isalpha():
            continue

        if last and last[-1].isdigit():
            next_i = after
            while next_i < len(text) and text[next_i] == " ":
                next_i += 1
            next_char = text[next_i] if next_i < len(text) else ""
            if next_char.isdigit():
                continue

        boundaries.append(after)

    if not boundaries:
        return [text]

    parts: list[str] = []
    prev = 0
    for b in boundaries:
        chunk = text[prev:b].rstrip()
        if chunk:
            parts.append(chunk)
        prev = b

    tail = text[prev:]
    if tail.strip():
        parts.append(tail)

    return parts or [text]


def _clause_flush(buffer_str: str) -> tuple[list[str], str]:
    if len(buffer_str) < 120:
        return [], buffer_str

    for pattern in (_SEMI_RE, _CLAUSE_RE):
        parts = pattern.split(buffer_str, maxsplit=1)
        if len(parts) == 2:
            head, tail = parts[0].rstrip(), parts[1]
            if head and " " in head:
                return [head], tail

    return [], buffer_str


def _clean_markdown(text: str) -> str:
    return _MD_STRIP_RE.sub("", text).strip()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
