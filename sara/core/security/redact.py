"""
sara.core.security.redact

Masks secrets (API keys, tokens, passwords, private keys, card numbers)
before text is logged or shown in diagnostics. Pattern-based and offline.

    redact("password: hunter2")  ->  "password: [REDACTED]"
"""
from __future__ import annotations

import re
from typing import List, Tuple

_PLACEHOLDER = "[REDACTED]"

_TOKEN_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?"
        r"(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/\-]{20,}=*", re.I)),
)

_KEY_VALUE = re.compile(
    r"\b(password|passwd|pwd|passcode|secret|api[_ -]?key|access[_ -]?token|"
    r"auth[_ -]?token|token)\b(\s*[:=]\s*)(\S+)",
    re.I,
)
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if flip:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        flip = not flip
    return total % 10 == 0


def _card_sub(match: "re.Match[str]") -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return _PLACEHOLDER
    return match.group(0)


def find_secret_kinds(text: str) -> List[str]:
    """Names of the secret types present in `text` (never the secrets)."""
    if not text:
        return []
    kinds = [name for name, pat in _TOKEN_RULES if pat.search(text)]
    if _KEY_VALUE.search(text):
        kinds.append("key_value")
    if any(_luhn_ok(re.sub(r"\D", "", m.group(0))) for m in _CARD.finditer(text)
           if 13 <= len(re.sub(r"\D", "", m.group(0))) <= 19):
        kinds.append("card_number")
    return kinds


def contains_secret(text: str) -> bool:
    return bool(find_secret_kinds(text))


def redact(text: str) -> str:
    """Return `text` with secrets replaced by [REDACTED]."""
    if not text:
        return text
    out = text
    for _name, pat in _TOKEN_RULES:
        out = pat.sub(_PLACEHOLDER, out)
    out = _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_PLACEHOLDER}", out)
    out = _CARD.sub(_card_sub, out)
    return out