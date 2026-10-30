"""Deterministic relation facts from a user's own sentence (no LLM).

Backs up LLM extraction for the one pattern a small local model keeps
missing (live E2E 2026-10-30): who a named person is to the user, and the
second clause about that person. "my sister's name is Priya and she lives
in Pune" gave only (user, has_sister, Priya); "mere dost Rohan ki job
Google mein hai" gave (Rohan, works_at, Google) but never said Rohan is a
friend -- so "where does my sister live?" had nothing to follow.

Only clear, explicit forms are matched. A name must be Capitalised or
introduced by "naam"/"name is"/"called", so "my sister is sick" or
"my friend is busy" are never read as names.
"""
from __future__ import annotations

import re

from .schema import _RELATION_ALIASES, RELATIONS

__all__ = ["relation_facts"]

_REL_WORDS = sorted(set(RELATIONS) | set(_RELATION_ALIASES), key=len, reverse=True)
_REL = r"(?P<rel>" + "|".join(re.escape(w) for w in _REL_WORDS) + r")"
_NAME = r"(?P<name>[A-Za-z][a-z]{1,20})"
_CAPNAME = r"(?P<name>[A-Z][a-z]{1,20})"
_NOT_NAMES = frozenset({
    "is", "a", "an", "the", "very", "so", "sick", "busy", "here", "there", "not", "also",
    "hai", "ka", "ki", "ke", "bhi", "ne", "ko", "se", "mein", "aur", "and", "but",
})

_PATTERNS = (
    # my sister's name is Priya / my sister is called Priya / my sister is named Priya
    re.compile(r"\bmy\s+" + _REL + r"(?:'s|’s)?\s+(?:name\s+is|is\s+called|is\s+named|named|called)\s+" + _NAME, re.I),
    # mere dost ka naam Rohan hai / meri behen ka naam Priya hai
    re.compile(r"\bmer[aei]\s+" + _REL + r"\s+(?:ka|ki)\s+naam\s+" + _NAME, re.I),
    # my sister Priya ... / mere dost Rohan ... (name must be Capitalised)
    re.compile(r"\b(?i:my|mer[aei])\s+(?i:" + _REL + r")\s+" + _CAPNAME + r"\b"),
    # my mother is Sunita (Capitalised)
    re.compile(r"\b(?i:my)\s+(?i:" + _REL + r")\s+is\s+" + _CAPNAME + r"\b"),
)

_PLACE = r"(?P<obj>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
# second clause about the same person: "... and she lives in Pune"
# (third-person verbs only, so "and I live in Jaipur" is never theirs)
_WHO = r"(?:(?:she|he|they|wo|woh|vo)\s+)?"
_FOLLOW = (
    ("lives_in", re.compile(r"\b" + _WHO + r"(?:lives|stays|is living)\s+in\s+" + _PLACE)),
    ("works_at", re.compile(r"\b" + _WHO + r"works\s+(?:at|in|for)\s+" + _PLACE)),
    ("studies_at", re.compile(r"\b" + _WHO + r"(?:studies|is studying)\s+(?:at|in)\s+" + _PLACE)),
)


def _canonical(rel: str) -> str:
    rel = rel.lower()
    return _RELATION_ALIASES.get(rel, rel)


def relation_facts(message: str) -> list[dict]:
    """[{subject, predicate, object}] found in ``message``; [] when unsure."""
    text = str(message or "")
    for pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        name = m.group("name")
        if name.lower() in _NOT_NAMES or _canonical(m.group("rel")) not in RELATIONS:
            continue
        name = name[:1].upper() + name[1:]
        out = [{"subject": "user", "predicate": f"has_{_canonical(m.group('rel'))}", "object": name}]
        tail = text[m.end():]
        for predicate, follow in _FOLLOW:
            fm = follow.search(tail)
            if fm:
                out.append({"subject": name, "predicate": predicate, "object": fm.group("obj")})
        return out
    return []
