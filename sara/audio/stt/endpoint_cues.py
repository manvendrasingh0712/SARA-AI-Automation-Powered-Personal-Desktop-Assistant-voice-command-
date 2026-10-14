"""Language cues for adaptive endpointing: continuation words and lone fillers (pure)."""
from __future__ import annotations

import unicodedata

_MAX_CHARS = 400


def tokens(text: object) -> list[str]:
    """Lower-cased word tokens (letters, digits, combining marks); punctuation and emoji split."""
    if not isinstance(text, str) or not text:
        return []
    out: list[str] = []
    buf: list[str] = []
    for ch in unicodedata.normalize("NFC", text[-_MAX_CHARS:]).casefold():
        cat = unicodedata.category(ch)
        if cat[0] in "LNM":
            buf.append(ch)
        elif cat == "Cf":
            continue
        elif buf:
            out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return out


def _cues(*words: str) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(tokens(w)) for w in words)


CONTINUATION_CUES: dict[str, tuple[tuple[str, ...], ...]] = {
    "en": _cues(
        "and", "then", "to", "the", "for", "with", "or", "but", "because",
        "if", "so", "a", "an", "my", "of", "in", "on",
    ),
    "hinglish": _cues(
        "aur", "phir", "fir", "ke liye", "ko", "se", "ka", "ki", "ke", "mein",
        "me", "par", "pe", "matlab", "toh", "to", "ya", "jo", "kyunki",
    ),
    "hi": _cues(
        "और", "फिर", "के लिए", "को", "से", "का", "की", "के", "में", "पर",
        "मतलब", "तो", "या", "कि",
    ),
}

FILLERS = frozenset(
    t for w in ("um", "uh", "umm", "hmm", "mm", "er", "matlab", "अं", "उम", "हम्म")
    for t in tokens(w)
)


def _tail_match(toks: list[str], cues: tuple[tuple[str, ...], ...]) -> bool:
    return any(len(toks) >= len(c) and tuple(toks[-len(c):]) == c for c in cues if c)


def ends_with_continuation(text: str, lang: str) -> bool:
    """True when the text ends on a word that normally continues a sentence."""
    key = lang.strip().lower() if isinstance(lang, str) else "en"
    return _tail_match(tokens(text), CONTINUATION_CUES.get(key, CONTINUATION_CUES["en"]))


def ends_with_any_continuation(text: str) -> bool:
    """Continuation-cue check across English, Hinglish and Hindi."""
    toks = tokens(text)
    return any(_tail_match(toks, cues) for cues in CONTINUATION_CUES.values())


def starts_with_continuation(text: str) -> bool:
    """True when the text opens with a continuation word (in any supported language)."""
    toks = tokens(text)
    return any(
        len(toks) >= len(c) and tuple(toks[: len(c)]) == c
        for cues in CONTINUATION_CUES.values()
        for c in cues
        if c
    )


def is_lone_filler(text: str) -> bool:
    """True for one or two filler tokens only (um, uh, hmm, matlab, ...)."""
    toks = tokens(text)
    return 1 <= len(toks) <= 2 and all(t in FILLERS for t in toks)