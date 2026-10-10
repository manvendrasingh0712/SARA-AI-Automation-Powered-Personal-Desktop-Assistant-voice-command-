"""
sara.gui.app.memory2_api
ApiMemory2Mixin -- JS-bridge methods for the Settings > Memory viewer (Memory 2.0).

Every method returns {"ok": True, "data": ...} or {"ok": False, "error": "<code>"} and never raises.
Reads work when Memory 2.0 is off ({"enabled": False, ...} with empty data); writes then return
the error "disabled". Memory text is never logged, only exception type names.
No relative imports on purpose: this file can be imported and tested on its own.
"""

import importlib
import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_LIST_KINDS = ("facts", "events", "entities", "archived")
_ROW_KINDS = ("facts", "events", "entities")
_EDIT_KINDS = ("facts", "events")
_MAX_QUERY = 80
_MAX_TEXT = 200
_MAX_OFFSET = 100000


def _ok(data):
    return {"ok": True, "data": data}


def _err(code):
    return {"ok": False, "error": code}


def _guard(name, func, *args):
    """Run func(*args); any exception becomes {"ok": False, "error": "failed"}."""
    try:
        return func(*args)
    except Exception as exc:
        logger.warning("[%s] failed (%s)", name, type(exc).__name__)
        return _err("failed")


def _clamp(value, lo, hi, default):
    """Return int(value) limited to [lo, hi]; `default` when it is not a number."""
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, number))


def _valid_id(value):
    """True for a positive int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _to_bool(value):
    """JS booleans, 0/1 and common strings -> bool; None when not interpretable."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    return None


def _number(value):
    """Finite float, or None for bools, non-numbers, NaN and infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _iso(value):
    """Epoch seconds/milliseconds or a string -> ISO string; None when unusable."""
    number = _number(value)
    if number is not None:
        try:
            seconds = number / 1000.0 if number > 1e12 else number
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        return value.strip()[:40]
    return None


def _store():
    """The Memory 2.0 store, or None when disabled or unavailable."""
    try:
        return importlib.import_module("sara.core.memory2").get_store()
    except Exception as exc:
        logger.warning("[memory2_api] store unavailable (%s)", type(exc).__name__)
        return None


def _item(row):
    """Store row -> viewer item (no utterance text, only the memory itself)."""
    kind = row.get("kind") if row.get("kind") in _ROW_KINDS else "facts"
    if kind == "facts":
        when, learned, value = row.get("valid_from"), row.get("created_at"), row.get("object_text")
    elif kind == "events":
        when, learned, value = row.get("ts"), row.get("created_at"), row.get("summary")
    else:
        when, learned, value = row.get("last_seen"), row.get("first_seen"), None
    turn = row.get("source_turn_id")
    mentions = _number(row.get("mention_count"))
    return {
        "id": row.get("id"),
        "kind": kind,
        "text": str(row.get("text") or "")[:300],
        "value": value if isinstance(value, str) else None,
        "status": str(row.get("status") or "active"),
        "confidence": _number(row.get("confidence")),
        "importance": _number(row.get("importance")),
        "pinned": bool(row.get("pinned")),
        "inferred": bool(row.get("inferred")),
        "when": _iso(when),
        "learned": _iso(learned),
        "source_turn_id": str(turn)[:40] if turn is not None else None,
        "mentions": int(mentions) if mentions is not None else None,
    }


def _clean_patch(kind, patch):
    """Validated patch (object_text / summary / importance / pinned only), or None."""
    if not isinstance(patch, dict) or not patch or len(patch) > 4:
        return None
    clean = {}
    for key, value in patch.items():
        if (key == "object_text" and kind == "facts") or (key == "summary" and kind == "events"):
            if not isinstance(value, str):
                return None
            text = " ".join(value.split())
            if not text or len(text) > _MAX_TEXT:
                return None
            clean[key] = text
        elif key == "importance":
            number = _number(value)
            if number is None:
                return None
            clean[key] = min(1.0, max(0.0, number))
        elif key == "pinned":
            flag = _to_bool(value)
            if flag is None:
                return None
            clean[key] = flag
        else:
            return None
    return clean


def _check(item_id, kind):
    """Error dict for a bad id/kind pair, else None."""
    if not _valid_id(item_id):
        return _err("invalid_id")
    if kind not in _EDIT_KINDS:
        return _err("invalid_kind")
    return None


def _list(kind, query, limit, offset):
    if kind not in _LIST_KINDS:
        return _err("invalid_kind")
    if query is None:
        query = ""
    if not isinstance(query, str) or len(query.strip()) > _MAX_QUERY:
        return _err("invalid_query")
    query = query.strip()
    limit = _clamp(limit, 1, 100, 50)
    offset = _clamp(offset, 0, _MAX_OFFSET, 0)
    store = _store()
    if store is None:
        return _ok({"enabled": False, "items": [], "total": 0, "has_more": False})
    rows = store.list_items(kind, status="active", query=query or None, limit=limit + 1, offset=offset)
    page = [row for row in rows[:limit] if isinstance(row, dict)]
    return _ok({
        "enabled": True,
        "items": [_item(row) for row in page],
        "total": offset + len(page),
        "has_more": len(rows) > limit,
    })


def _update(item_id, kind, patch):
    bad = _check(item_id, kind)
    if bad:
        return bad
    clean = _clean_patch(kind, patch)
    if clean is None:
        return _err("invalid_patch")
    store = _store()
    if store is None:
        return _err("disabled")
    try:
        if not store.update_item(item_id, kind, clean):
            return _err("not_found")
    except ValueError:
        return _err("invalid_patch")
    return _ok({"id": item_id, "kind": kind})


def _forget(item_id, kind):
    bad = _check(item_id, kind)
    if bad:
        return bad
    store = _store()
    if store is None:
        return _err("disabled")
    if not store.retract(item_id, kind):
        return _err("not_found")
    return _ok({"id": item_id, "kind": kind})


def _pin(item_id, kind, pinned):
    bad = _check(item_id, kind)
    if bad:
        return bad
    flag = _to_bool(pinned)
    if flag is None:
        return _err("invalid_pinned")
    store = _store()
    if store is None:
        return _err("disabled")
    if not store.set_pinned(item_id, kind, flag):
        return _err("not_found")
    return _ok({"id": item_id, "kind": kind, "pinned": flag})


def _stats():
    store = _store()
    if store is None:
        return _ok({"enabled": False})
    data = dict(store.stats())
    data["enabled"] = True
    return _ok(data)


class ApiMemory2Mixin:

    def mem2_list(self, kind="facts", query="", limit=50, offset=0):
        return _guard("mem2_list", _list, kind, query, limit, offset)

    def mem2_update(self, item_id, kind, patch):
        return _guard("mem2_update", _update, item_id, kind, patch)

    def mem2_forget(self, item_id, kind):
        return _guard("mem2_forget", _forget, item_id, kind)

    def mem2_pin(self, item_id, kind, pinned):
        return _guard("mem2_pin", _pin, item_id, kind, pinned)

    def mem2_stats(self):
        return _guard("mem2_stats", _stats)