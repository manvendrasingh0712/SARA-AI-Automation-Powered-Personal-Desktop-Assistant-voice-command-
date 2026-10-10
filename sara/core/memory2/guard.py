"""Privacy guards for Memory 2.0: utterance sanitising and sensitive-item detection."""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

try:
    from sara.core.security.detector import scan as _scan
    from sara.core.security.redact import contains_secret as _contains_secret
    from sara.core.security.redact import redact as _redact
except Exception as _exc:  # noqa: BLE001 - extraction then fails closed
    _scan = _contains_secret = _redact = None
    logger.error("[Memory2] security modules unavailable (%s); extraction disabled", type(_exc).__name__)

_ALWAYS = re.compile(
    r"\b(?:passwords?|passcodes?|otp|cvv|ssn|credentials?)\b|आधार|पासवर्ड|ओटीपी", re.IGNORECASE
)
_NUMBERED = re.compile(r"\b(?:card|account|aadhaar|aadhar|pan|passport|pin)\b", re.IGNORECASE)
_NUMBERISH = re.compile(r"\d|\bnumber\b|\bno\.", re.IGNORECASE)
_MARKER = "[redacted]"
_START = re.compile(
    r"^\W*(?:(?:hey|hi|ok|okay|please|pls|sara|zara)\W+)*"
    r"(?:play|open|launch|search|google|call|send|set|remind|mute|unmute|stop|pause|resume|close"
    r"|shutdown|restart|volume|switch|goto|go\s+to|minimi[sz]e|maximi[sz]e|scroll|click|navigate"
    r"|sw\w{2,5}\s+to)\b",
    re.IGNORECASE,
)
_ANYWHERE = re.compile(
    r"\bremind\s+me\b|\byaad\s+dila\w*|\b(?:set|lagao|laga\s+do)\b.{0,20}\b(?:alarm|timer|reminder)\b"
    r"|\bplay\b.{0,60}\bon\s+(?:youtube|spotify)\b",
    re.IGNORECASE,
)
_END = re.compile(
    r"\b(?:chalao|chala\s+do|bajao|baja\s+do|play\s+kar\w*|lagao|laga\s+do|khol\w*(?:\s+(?:do|de))?"
    r"|band\s+kar\w*)\W*$",
    re.IGNORECASE,
)


_QUESTION = re.compile(
    r"^\W*(?:(?:hey|hi|ok|okay|please|pls|sara|zara)\W+)*"
    r"(?:(?:do|did|can|could|will|would|are|is)\s+(?:you|it)\b|what(?:'s|\s+is)\b|who(?:'s|\s+is)\b"
    r"|where\s+is\b|how\s+(?:do|to|can)\b|kya\b|kaun\b|kab\b|kahan\b|kaise\b|kyun\b|kitn[ae]\b)",
    re.IGNORECASE,
)


_SMALLTALK = re.compile(
    r"^\W*(?:thanks|thank|thx|ty|shukriya|dhanyavaad|dhanyawad|bye|ok|okay|cool|nice|accha|theek)\b",
    re.IGNORECASE,
)


def is_command(text: str) -> bool:
    """True for questions, assistant commands and short pleasantries: never user facts."""
    value = text.strip()
    return value.endswith(("?", "？")) or bool(
        _START.search(value) or _QUESTION.search(value) or _ANYWHERE.search(value) or _END.search(value)
        or (_SMALLTALK.search(value) and len(value.split()) <= 4)
    )


def available() -> bool:
    """True when the security stack imported correctly."""
    return _scan is not None and _redact is not None and _contains_secret is not None


def is_sensitive_text(text: str) -> bool:
    """Keyword guard: secrets words, or card/account/ID words next to numbers."""
    return bool(_ALWAYS.search(text) or (_NUMBERED.search(text) and _NUMBERISH.search(text)))


def sanitize_utterance(text: object) -> str | None:
    """Redacted utterance, or None when it must be skipped entirely (flagged or empty)."""
    try:
        raw = str(text or "")
        if not raw.strip() or is_command(raw) or getattr(_scan(raw), "flagged", False):
            return None
        clean = _redact(raw)
        if not isinstance(clean, str):
            return None
        if not clean.replace(_MARKER, "").strip(" \t\r\n.,:;-"):
            return None
        return clean
    except Exception:  # noqa: BLE001
        return None


def item_is_safe(text: object) -> bool:
    """False when an extracted item contains a redaction marker, a secret or a sensitive keyword."""
    try:
        value = str(text or "")
        if _MARKER in value.lower() or is_sensitive_text(value):
            return False
        return not _contains_secret(value)
    except Exception:  # noqa: BLE001
        return False