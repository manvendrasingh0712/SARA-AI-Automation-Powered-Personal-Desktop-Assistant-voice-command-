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


# E2E test 2026-10-29: "mera naam kya hai" saved the user's name as
# "Kya Hai", and "mera naam Rahul hai" as "Rahul Hai". A question is never
# a name, a Hindi copula after the name is not part of it, and "call me
# later" / "call me a taxi" are not introductions.
_NAME_QUESTION_WORDS = frozenset({
    "kya", "kaun", "kon", "kaise", "kaisa", "kyu", "kyun", "kyon",
    "what", "who", "which", "why", "how", "whats", "what's",
})
_NOT_A_NAME = frozenset({
    "a", "an", "the", "later", "back", "tomorrow", "now", "soon", "when",
    "at", "in", "on", "after", "before", "please", "na", "to", "ki", "ke",
    "bhi", "toh", "abhi", "baad", "kal", "jab", "if", "once", "again",
})
_TRAILING_COPULA = frozenset({"hai", "h", "hain", "hu", "hoon", "hun", "he", "hy"})


def _capture_name_after(text: str, start_idx: int):
    remainder = text[start_idx:].strip()
    if not remainder:
        return None
    if "?" in remainder:
        return None
    remainder = re.split(r"[.!?,;]", remainder, maxsplit=1)[0]
    stop_match = _NAME_STOP_RE.search(remainder)
    if stop_match:
        remainder = remainder[: stop_match.start()]
    words = remainder.strip().split()
    while words and words[-1].lower() in _TRAILING_COPULA:
        words.pop()
    if not words:
        return None
    first = words[0].lower()
    if first in _NAME_QUESTION_WORDS or first in _NOT_A_NAME:
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


# Words that may surround an exit phrase without changing its meaning:
# "ok bye", "bye sara", "sara band karo", "ab band karo please".
_EXIT_FILLER = frozenset({
    "sara", "ok", "okay", "please", "now", "ab", "abhi", "ji", "hey", "hi",
    "toh", "to", "then", "for", "today", "so", "bas", "chalo", "thanks",
    "thank", "you", "tata", "see", "ya", "yaar", "can", "no", "just", "alright",
})


def _is_exit_command(text: str, phrases: set) -> bool:
    """True only when the utterance is an exit phrase and nothing else
    (besides address/filler words). E2E test 2026-10-29: the prefix/suffix
    match in _matches_phrase_set() made "spotify band karo", "gaana band
    karo", "wifi band karo", "quit spotify", "exit full screen" and
    "shutdown the computer" all quit SARA instead of doing what they said
    -- the same defect the STOP-WORD FIX removed for "stop"."""
    words = re.sub(r"[.!?,;]", " ", (text or "").lower()).split()
    if not words:
        return False
    for phrase in phrases:
        pw = phrase.split()
        n = len(pw)
        if n == 0 or n > len(words):
            continue
        for start in range(len(words) - n + 1):
            if words[start:start + n] != pw:
                continue
            rest = words[:start] + words[start + n:]
            if all(w in _EXIT_FILLER for w in rest):
                return True
    return False
