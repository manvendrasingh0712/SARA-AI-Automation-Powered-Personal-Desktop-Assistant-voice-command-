"""Per-user vocabulary hint for Whisper's prompt.

E2E 2026-10-30: Whisper tiny turned names into near-misses ("Jaipur" ->
"Jypur"/"jippur", "Arijit Singh" -> "Arogyt sing"). A bigger model did not
help (medium: 21.9 % vs 24.6 % word errors on noisy speech, 3x slower, 1 GB+
VRAM) -- but listing the names in the prompt did: English word errors
5.7 % -> 2.1 % on clean audio, 9.3 % -> 7.7 % at 8 dB noise, +10 ms per
decode, and no vocabulary word appeared on silence, noise or ordinary
speech (0/18 clips). The list is short on purpose (Whisper keeps only the
last ~224 prompt tokens).

Sources, in priority order: STT_EXTRA_VOCABULARY (.env), the user's own
name and relatives' names and the people/places/orgs in Memory 2.0, then
well-known Indian cities and a few brands Whisper garbles. Never raises; cached for 60 s.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Iterable, List

__all__ = ["vocabulary_hint", "build_vocabulary"]

_CITIES = (
    "Jaipur", "Delhi", "Mumbai", "Bangalore", "Pune", "Hyderabad", "Chennai", "Kolkata",
    "Ahmedabad", "Lucknow", "Chandigarh", "Udaipur", "Jodhpur", "Kota", "Indore", "Bhopal",
)
_BRANDS = ("YouTube", "Spotify", "WhatsApp", "Netflix", "Instagram")
_CACHE_S = 60.0
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "key": None, "value": ""}
_WORD = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,30}$|^[ऀ-ॿ ]{2,30}$")
_SKIP = frozenset({"user", "me", "i", "you", "sara", "a", "an", "the", "none", "null"})


def _clean(items: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in items:
        word = " ".join(str(raw or "").split()).strip(" ,.;:")
        low = word.lower()
        if not word or low in seen or low in _SKIP or not _WORD.match(word):
            continue
        seen.add(low)
        out.append(word)
    return out


def _from_config(cfg: Any) -> List[str]:
    extra = str(getattr(cfg, "STT_EXTRA_VOCABULARY", "") or "")
    return [w for w in re.split(r"[,;\n]", extra) if w.strip()]


def _from_memory() -> List[str]:
    names: List[str] = []
    try:
        from sara.core import memory2

        store = memory2.get_store()
        if store is None:
            return names
        by_id = {int(e["id"]): e for e in store.iter_rows("entities") if e.get("id") is not None}
        # people / places / orgs mentioned most often first
        ranked = sorted(by_id.values(), key=lambda e: -int(e.get("mention_count") or 0))
        names.extend(str(e.get("name") or "") for e in ranked)
        for fact in store.iter_rows("facts", ("active",)):
            pred = str(fact.get("predicate") or "")
            obj = str(fact.get("object_text") or fact.get("object") or "")
            if pred == "name_is" or (pred.startswith("has_") and pred != "has_pet"):
                names.insert(0, obj)  # who the user is / who their people are: first
    except Exception:
        pass
    return names


def build_vocabulary(cfg: Any = None, max_words: int = 24) -> List[str]:
    """The ordered, de-duplicated word list (most useful first)."""
    if cfg is None:
        from config import Config as cfg  # noqa: N813
    words: List[str] = []
    words.extend(_from_config(cfg))
    words.extend(_from_memory())
    words.extend(_CITIES)
    words.extend(_BRANDS)
    return _clean(words)[: max(0, int(max_words))]


def vocabulary_hint(cfg: Any = None) -> str:
    """" Names that may come up: A, B, C." (leading space) or "" when off/empty."""
    try:
        if cfg is None:
            from config import Config as cfg  # noqa: N813
        if not bool(getattr(cfg, "STT_VOCABULARY_PROMPT", True)):
            return ""
        now = time.monotonic()
        with _lock:
            if now - _cache["at"] < _CACHE_S and _cache["key"] is cfg:
                return _cache["value"]
        words = build_vocabulary(cfg, int(getattr(cfg, "STT_VOCABULARY_MAX_WORDS", 24)))
        value = f" Names that may come up: {', '.join(words)}." if words else ""
        with _lock:
            _cache.update(at=now, key=cfg, value=value)
        return value
    except Exception:
        return ""
