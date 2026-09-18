"""
sara.orchestrator.text_utils
Name-extraction and phrase-matching helpers used while parsing user
speech (e.g. "my name is ..." / exit/sleep/forget phrase sets).
"""

import re

from sara.orchestrator._constants import (
    _STRONG_NAME_PHRASES,
    _WEAK_NAME_PHRASES,
    _WEAK_NAME_BLOCKLIST,
)

# ----------------------------------------------------------------------------
# Name extraction
# ----------------------------------------------------------------------------

_NAME_STOP_RE = re.compile(
    r"\b(and|but|so|because|today|right now|here|there)\b", re.IGNORECASE
)


def _cap_word(word: str) -> str:
    if not word:
        return word
    return word[0].upper() + word[1:]


def _capture_name_after(text: str, start_idx: int):
    remainder = text[start_idx:].strip()
    if not remainder:
        return None
    remainder = re.split(r"[.!?,;]", remainder, maxsplit=1)[0]
    stop_match = _NAME_STOP_RE.search(remainder)
    if stop_match:
        remainder = remainder[: stop_match.start()]
    words = remainder.strip().split()
    if not words:
        return None
    name_words = words[:3]
    return " ".join(_cap_word(w) for w in name_words)


def _extract_name(text: str):
    lowered = text.lower()

    for phrase in _STRONG_NAME_PHRASES:
        if phrase in lowered:
            idx = lowered.index(phrase) + len(phrase)
            return _capture_name_after(text, idx)

    for phrase in _WEAK_NAME_PHRASES:
        if phrase in lowered:
            idx = lowered.index(phrase) + len(phrase)
            remainder = text[idx:].strip()
            if not remainder:
                continue
            first_word = remainder.split()[0].lower().strip(".,!?;")
            if first_word in _WEAK_NAME_BLOCKLIST:
                continue
            return _capture_name_after(text, idx)

    return None


def _matches_phrase_set(text: str, phrases: set) -> bool:
    cleaned = text.lower().strip().strip(".!?,")
    if cleaned in phrases:
        return True
    words = cleaned.split()
    if not words:
        return False
    for phrase in phrases:
        phrase_words = phrase.split()
        n = len(phrase_words)
        if n == 0 or n > len(words):
            continue
        if words[:n] == phrase_words or words[-n:] == phrase_words:
            return True
    return False
