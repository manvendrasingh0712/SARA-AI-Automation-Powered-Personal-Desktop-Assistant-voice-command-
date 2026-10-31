"""
sara.audio.tts.barge_classify
Pure classifier for speech heard while Sara is talking:
"backchannel" (hmm / okay: keep talking), "interrupt" (stop / correction /
command) or "ambiguous". English, Hinglish and Hindi (Devanagari).
"""
from __future__ import annotations

import unicodedata
from typing import Any, Optional

from config import Config

BACKCHANNEL = "backchannel"
INTERRUPT = "interrupt"
AMBIGUOUS = "ambiguous"

_BACKCHANNEL_PHRASES: frozenset[tuple[str, ...]] = frozenset(
    tuple(p.split())
    for p in (
        "hmm", "hm", "mm", "mhm", "ok", "okay", "yeah", "yes", "yep", "right",
        "sure", "uh huh", "i see", "got it", "cool", "nice",
        "haan", "han", "acha", "accha", "achha", "theek", "thik", "theek hai",
        "ji", "sahi",
        "हाँ", "हां", "हम्म", "अच्छा", "ठीक", "ठीक है", "जी", "सही",
    )
)

_STOP_PHRASES: frozenset[tuple[str, ...]] = frozenset(
    tuple(p.split())
    for p in (
        "stop", "wait", "no", "nope", "sorry", "actually", "pause", "cancel",
        "enough", "hold on", "stop it", "stop talking", "quiet", "be quiet", "shut up", "silence",
        "ruko", "ruk", "rukiye", "bas", "nahi", "nahin", "galat", "ek minute",
        "ek second", "chup", "chup ho ja", "chup ho jao", "chup raho", "bas karo", "bas kar", "bas kijiye",
        "रुको", "रुक", "बस", "नहीं", "गलत", "एक मिनट", "चुप", "चुप हो जा", "चुप हो जाओ", "बस करो",
    )
)
_MAX_STOP_LEN = max(len(p) for p in _STOP_PHRASES)


def _num(value: Any, default: float = 0.0) -> float:
    """Finite float or `default`."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and abs(out) != float("inf") else default


def _tokens(text: Any) -> list[str]:
    """Lower-cased words with punctuation/symbols/spaces removed."""
    if not isinstance(text, str):
        return []
    norm = unicodedata.normalize("NFC", text).lower()
    cleaned = "".join(
        " " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in norm
    )
    return cleaned.split()


def _all_backchannel(tokens: list[str]) -> bool:
    """True when the tokens split fully into backchannel words/phrases."""
    n = len(tokens)
    if n == 0:
        return False
    ok = [False] * (n + 1)
    ok[0] = True
    for i in range(n):
        if not ok[i]:
            continue
        for ln in (1, 2):
            if i + ln <= n and tuple(tokens[i : i + ln]) in _BACKCHANNEL_PHRASES:
                ok[i + ln] = True
    return ok[n]


def _has_stop_word(tokens: list[str]) -> bool:
    """True when any stop/correction word or phrase appears as whole words."""
    n = len(tokens)
    for i in range(n):
        for ln in range(1, _MAX_STOP_LEN + 1):
            if i + ln <= n and tuple(tokens[i : i + ln]) in _STOP_PHRASES:
                return True
    return False


def _is_command(text: str) -> bool:
    """True when the local intent detector maps `text` to a non-chat intent."""
    try:
        from sara.core.intent.engine import detect_intent

        intent, _match = detect_intent(text)
        return intent != "chat"
    except Exception:
        return False


def _ambiguous_ms(cfg: Any) -> float:
    src = cfg if cfg is not None else Config
    return _num(getattr(src, "BARGE_AMBIGUOUS_MS", 800), 800.0)


def _classify(text: Any, speech_ms: Any, cfg: Any) -> str:
    ms = _num(speech_ms)
    tokens = _tokens(text)
    if not tokens:
        return INTERRUPT if ms >= _ambiguous_ms(cfg) else AMBIGUOUS
    if _has_stop_word(tokens):
        return INTERRUPT
    if len(tokens) <= 2 and _all_backchannel(tokens):
        return BACKCHANNEL
    if len(tokens) >= 3 and not _all_backchannel(tokens):
        return INTERRUPT
    if _is_command(text):
        return INTERRUPT
    return AMBIGUOUS


def classify_barge(
    text: Optional[str],
    speech_ms: float,
    *,
    lang_hint: str = "en",
    cfg: Any = None,
) -> str:
    """Return "backchannel", "interrupt" or "ambiguous". Pure, never raises.
    `lang_hint` is accepted for call-site symmetry; the word lists already
    cover English, Hinglish and Hindi together."""
    try:
        return _classify(text, speech_ms, cfg)
    except Exception:
        return AMBIGUOUS


def resolve_ambiguous(speech_ms: float, cfg: Any = None) -> str:
    """Settle an "ambiguous" verdict by speech length alone."""
    try:
        return INTERRUPT if _num(speech_ms) >= _ambiguous_ms(cfg) else BACKCHANNEL
    except Exception:
        return BACKCHANNEL