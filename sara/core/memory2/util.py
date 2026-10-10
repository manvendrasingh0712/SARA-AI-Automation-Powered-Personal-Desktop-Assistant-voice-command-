"""Small pure helpers shared by the Memory 2.0 store modules."""
from __future__ import annotations

import re
import unicodedata

import numpy as np

MAX_TEXT = 200
_WS = re.compile(r"\s+")
_USER_ALIASES = frozenset({"user", "i", "me", "myself"})

_TEMPLATES: dict[str, str] = {
    "lives_in": "{s} lives in {o}",
    "works_at": "{s} works at {o}",
    "studies_at": "{s} studies at {o}",
    "name_is": "{s}'s name is {o}",
    "birthday": "{s}'s birthday is {o}",
    "preferred_language": "{s}'s preferred language is {o}",
    "wake_time": "{s} wakes up at {o}",
    "relationship_status": "{s}'s relationship status is {o}",
    "favorite_color": "{s}'s favorite color is {o}",
    "favorite_food": "{s}'s favorite food is {o}",
    "age": "{s}'s age is {o}",
    "likes": "{s} likes {o}",
    "dislikes": "{s} dislikes {o}",
    "knows": "{s} knows {o}",
    "uses": "{s} uses {o}",
    "interested_in": "{s} is interested in {o}",
    "has_pet": "{s} has a pet: {o}",
    "speaks": "{s} speaks {o}",
    "plays": "{s} plays {o}",
    "note": "{s}: {o}",
    "inferred": "{s} {o}",
}


def clamp01(value: object, default: float = 0.5) -> float:
    """Float clamped to [0, 1]; ``default`` for non-numeric or NaN input."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return min(1.0, max(0.0, number))


def norm_text(text: object) -> str:
    """Comparison key: NFKC, lowercase, collapsed spaces, trailing punctuation removed."""
    lowered = unicodedata.normalize("NFKC", str(text or "")).lower()
    return _WS.sub(" ", lowered).strip().strip(".!?,;:").strip()


def clean_text(text: object, name: str = "text") -> str:
    """Collapse whitespace; raise ValueError when empty or longer than MAX_TEXT."""
    cleaned = _WS.sub(" ", str(text if text is not None else "")).strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    if len(cleaned) > MAX_TEXT:
        raise ValueError(f"{name} longer than {MAX_TEXT} characters")
    return cleaned


def subject_label(subject: object) -> str:
    """Canonical subject name: user aliases collapse to "user"."""
    label = _WS.sub(" ", str(subject or "")).strip()
    return "user" if not label or label.lower() in _USER_ALIASES else label


def fact_text(subject: str, predicate: str, obj: str) -> str:
    """Human sentence for a fact, e.g. "user lives in Jaipur"."""
    template = _TEMPLATES.get(predicate)
    if template is None:
        template = "{s} " + predicate.replace("_", " ") + " {o}"
    return template.format(s=subject, o=obj)


def encode_vec(vec: np.ndarray) -> bytes:
    """float32 little-endian bytes for the embedding BLOB."""
    return np.asarray(vec, dtype=np.float32).tobytes()


def decode_vec(blob: object) -> np.ndarray | None:
    """Decode an embedding BLOB; None when missing, misaligned or non-finite."""
    if not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) == 0 or len(blob) % 4:
        return None
    vec = np.frombuffer(bytes(blob), dtype=np.float32)
    return vec if bool(np.all(np.isfinite(vec))) else None


def valid_id(value: object) -> bool:
    """True for a positive int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0