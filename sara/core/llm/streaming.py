"""
sara.core.llm.streaming
Token/sentence-boundary helpers used while streaming LLM output for TTS.
"""
from __future__ import annotations



import re
from typing import NamedTuple, Optional


# ══════════════════════════════════════════════════════════════════════
# Module-level compiled regexes
# ══════════════════════════════════════════════════════════════════════

_SENT_END_RE = re.compile(r"([.!?।॥])\s+")
_MD_STRIP_RE = re.compile(r"(\*{1,3}|#{1,6}|`{1,3}|_{1,2}|~~|\|\|)")
_CLAUSE_RE = re.compile(r",\s+(and|but|so|yet|or|nor)\s+", re.IGNORECASE)
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

    m = _SEMI_RE.search(buffer_str)
    if m:
        head, tail = buffer_str[: m.start()].rstrip(), buffer_str[m.end() :]
        if head and " " in head:
            return [head], tail

    # Keep the conjunction instead of discarding it -- move it
    # (capitalized) onto the front of the next chunk.
    m = _CLAUSE_RE.search(buffer_str)
    if m:
        head = buffer_str[: m.start()].rstrip()
        connector = m.group(1)
        rest = buffer_str[m.end() :].lstrip()
        tail = f"{connector[:1].upper()}{connector[1:].lower()} {rest}"
        if head and " " in head:
            return [head], tail

    return [], buffer_str


def _clean_markdown(text: str) -> str:
    return _MD_STRIP_RE.sub("", text).strip()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)