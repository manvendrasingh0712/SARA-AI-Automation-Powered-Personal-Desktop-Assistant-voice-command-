"""Tolerant JSON parsing and strict validation of LLM extraction output."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from . import guard
from .schema import classify_predicate
from .util import MAX_TEXT, clamp01

_FENCE = re.compile(r"```(?:json|JSON)?")
_ENTITY_TYPES = frozenset({"person", "place", "org", "thing"})
_NAME_MAX = 80
_WORD_MAX = 12
_CAPS = {"entities": 50, "facts": 50, "events": 30}
_NULLS = frozenset({"", "null", "none", "unknown", "n/a"})


def parse_json(raw: str) -> dict | None:
    """First JSON object found in ``raw`` (fences and surrounding prose tolerated)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = _FENCE.sub("", raw)
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    return None


def to_epoch(value: Any) -> float | None:
    """ISO-8601 string (or epoch seconds) to a float timestamp; None when unparsable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 1e8 else None
    if not isinstance(value, str) or value.strip().lower() in _NULLS:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _turn(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    return value if isinstance(value, int) and value > 0 else None


def _blocked(item: dict, skip: set[str], min_confidence: float | None) -> bool:
    if _text(item.get("category")).lower() in skip:
        return True
    return min_confidence is not None and clamp01(item.get("confidence"), 0.7) < min_confidence


def _items(payload: dict, key: str) -> list:
    value = payload.get(key)
    return value[: _CAPS[key]] if isinstance(value, list) else []


def validate(payload: dict, *, min_confidence: float, skip_categories: set[str]) -> dict:
    """Filter and normalise a parsed payload.

    Returns {"entities": [{name, type}], "facts": [{turn, subject, predicate, object, time,
    confidence, importance}], "events": [{turn, summary, time, entities, importance}],
    "dropped": int}.
    """
    out: dict = {"entities": [], "facts": [], "events": [], "dropped": 0}
    if not isinstance(payload, dict):
        return out
    skip = {c.strip().lower() for c in skip_categories}
    for item in _items(payload, "entities"):
        name = _text(item.get("name")) if isinstance(item, dict) else ""
        if not name or len(name) > _NAME_MAX or not guard.item_is_safe(name):
            out["dropped"] += 1
            continue
        etype = _text(item.get("type")).lower()
        out["entities"].append({"name": name, "type": etype if etype in _ENTITY_TYPES else "thing"})
    for item in _items(payload, "facts"):
        if not isinstance(item, dict):
            out["dropped"] += 1
            continue
        subject, raw_pred, obj = _text(item.get("subject")), _text(item.get("predicate")), _text(item.get("object"))
        if not (subject and raw_pred and obj) or _blocked(item, skip, min_confidence):
            out["dropped"] += 1
            continue
        predicate, _ = classify_predicate(raw_pred)
        key = re.sub(r"[\s\-]+", "_", raw_pred.lower())
        if predicate == "note" and key != "note":
            if any(len(part) > _WORD_MAX for part in key.split("_")):
                out["dropped"] += 1
                continue
            obj = f"{key.replace('_', ' ')}: {obj}"
        if len(obj) > MAX_TEXT or len(subject) > _NAME_MAX or not guard.item_is_safe(f"{subject} {raw_pred} {obj}"):
            out["dropped"] += 1
            continue
        out["facts"].append({
            "turn": _turn(item.get("turn")), "subject": subject, "predicate": predicate, "object": obj,
            "time": to_epoch(item.get("time")), "confidence": clamp01(item.get("confidence"), 0.7),
            "importance": clamp01(item.get("importance"), 0.5),
        })
    for item in _items(payload, "events"):
        summary = _text(item.get("summary")) if isinstance(item, dict) else ""
        if not summary or len(summary) > MAX_TEXT or _blocked(item, skip, min_confidence if "confidence" in item else None):
            out["dropped"] += 1
            continue
        names = [n for n in (_text(e) for e in item.get("entities") or [] if isinstance(e, str)) if n]
        if any(len(n) > _NAME_MAX for n in names) or not guard.item_is_safe(" ".join([summary, *names])):
            out["dropped"] += 1
            continue
        out["events"].append({
            "turn": _turn(item.get("turn")), "summary": summary, "time": to_epoch(item.get("time")),
            "entities": names[:8], "importance": clamp01(item.get("importance"), 0.5),
        })
    return out