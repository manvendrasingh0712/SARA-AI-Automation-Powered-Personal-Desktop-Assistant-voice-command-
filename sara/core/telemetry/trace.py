"""
sara.core.telemetry.trace
Per-turn latency tracing. Every conversational turn (voice or typed) gets one
record of monotonic stage stamps. Finished records live in a bounded in-memory
ring and are handed to store.TraceStore for SQLite persistence.

One module lock guards all registry state (marks arrive from the audio,
executor, TTS and GUI threads). Nothing blocks on I/O while holding it, and no
public function ever raises into its caller.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Optional

from config import Config

from .store import TraceStore

logger = logging.getLogger(__name__)

_STAGES = frozenset({
    "wake", "speech_start", "speech_end", "stt_done", "route_done", "llm_req",
    "llm_first_token", "llm_done", "tool_start", "tool_end", "tts_first_audio",
    "tts_done", "end",
})
_FIRST_WINS = frozenset({
    "wake", "speech_start", "speech_end", "stt_done", "llm_req",
    "llm_first_token", "tool_start", "tts_first_audio",
})
_PENDING_STAGES = frozenset({"wake", "speech_start", "speech_end", "stt_done"})
_OUTCOMES = frozenset({"ok", "error", "cancelled", "barge_in"})
_META_KEYS = frozenset({"lang", "route", "intent", "model", "error_code"})
_RECENT_KEYS = (
    "turn_id", "ts", "source", "route", "intent", "outcome", "stt_ms",
    "route_ms", "llm_ttft_ms", "llm_total_ms", "tool_ms", "tts_start_ms",
    "ttfa_ms", "total_ms",
)
_STAGE_AVG = (
    ("stt", "stt_ms"), ("route", "route_ms"), ("llm_ttft", "llm_ttft_ms"),
    ("tool", "tool_ms"), ("tts_start", "tts_start_ms"),
)
_PENDING_TTL_S = 120.0
_FINALISE_AFTER_END_S = 20.0
_MIN_SAMPLES = 5
_MAX_EXTRA = 16
_MAX_STR = 64


def _make_ring() -> deque:
    try:
        size = max(50, min(5000, int(getattr(Config, "TRACE_RING_SIZE", 500))))
    except (TypeError, ValueError):
        size = 500
    return deque(maxlen=size)


_lock = threading.Lock()
_now: Callable[[], float] = time.monotonic
_enabled = bool(getattr(Config, "TELEMETRY_ENABLED", True))
_active: dict[str, "_Turn"] = {}
_latest: Optional[str] = None
_pending: dict[str, float] = {}
_ring: deque = _make_ring()
_store = TraceStore(on_idle=lambda: _sweep())
_warned: set[str] = set()


def _warn_once(name: str, exc: Exception) -> None:
    if name not in _warned:
        _warned.add(name)
        logger.warning("[telemetry] %s failed (ignored): %s", name, exc)


def _safe(default: Callable[[], Any]) -> Callable:
    """Make a public function never raise: log once, return default()."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                _warn_once(fn.__name__, exc)
                return default()
        return wrapper
    return decorator


class _Turn:
    """Mutable state of one in-flight turn (guarded by _lock)."""

    __slots__ = (
        "turn_id", "ts", "source", "stamps", "meta", "extra", "tainted",
        "text_len", "text_hash", "text", "ended_at", "outcome",
    )

    def __init__(self, turn_id: str, source: str) -> None:
        self.turn_id = turn_id
        self.ts = time.time()
        self.source = source
        self.stamps: dict[str, float] = {}
        self.meta: dict[str, Any] = {}
        self.extra: dict[str, Any] = {}
        self.tainted = 0
        self.text_len: Optional[int] = None
        self.text_hash: Optional[str] = None
        self.text: Optional[str] = None
        self.ended_at: Optional[float] = None
        self.outcome: Optional[str] = None


def _clip(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_STR]


def _apply_meta(turn: _Turn, meta: dict[str, Any]) -> None:
    for key, val in meta.items():
        if val is None:
            continue
        if key == "tainted":
            turn.tainted = 1 if val else 0
        elif key in _META_KEYS:
            turn.meta[key] = _clip(val)
        elif key in turn.extra or len(turn.extra) < _MAX_EXTRA:
            turn.extra[str(key)[:_MAX_STR]] = _clip(val)


def _delta(end: Optional[float], start: Optional[float]) -> Optional[float]:
    if end is None or start is None or end < start:
        return None
    return round((end - start) * 1000.0, 1)


def _record(turn: _Turn) -> dict[str, Any]:
    """Derive the flat metrics row for a turn."""
    s = turn.stamps
    wake = s.get("wake")
    typed = turn.source == "text"
    tts_from = next(
        (s[k] for k in ("llm_first_token", "tool_end", "route_done") if k in s), None
    )
    return {
        "turn_id": turn.turn_id,
        "ts": turn.ts,
        "source": turn.source,
        "lang": turn.meta.get("lang"),
        "route": turn.meta.get("route"),
        "intent": turn.meta.get("intent"),
        "model": turn.meta.get("model"),
        "outcome": turn.outcome or "error",
        "error_code": turn.meta.get("error_code"),
        "tainted": turn.tainted,
        "text_len": turn.text_len,
        "text_hash": turn.text_hash,
        "text": turn.text,
        "speech_end_ms": None if typed else _delta(s.get("speech_end"), wake),
        "stt_ms": _delta(s.get("stt_done"), s.get("speech_end")),
        "route_ms": _delta(s.get("route_done"), wake if typed else s.get("stt_done")),
        "llm_ttft_ms": _delta(s.get("llm_first_token"), s.get("llm_req")),
        "llm_total_ms": _delta(s.get("llm_done"), s.get("llm_req")),
        "tool_ms": _delta(s.get("tool_end"), s.get("tool_start")),
        "tts_start_ms": _delta(s.get("tts_first_audio"), tts_from),
        "ttfa_ms": _delta(s.get("tts_first_audio"), wake if typed else s.get("speech_end")),
        "total_ms": _delta(s.get("end"), wake),
        "extra_json": (
            json.dumps(turn.extra, ensure_ascii=False, separators=(",", ":"))
            if turn.extra else None
        ),
    }


def _finalise_locked(turn: _Turn) -> dict[str, Any]:
    _active.pop(turn.turn_id, None)
    rec = _record(turn)
    _ring.append(rec)
    return rec


def new_turn_id() -> str:
    """Short unique id for a turn."""
    return uuid.uuid4().hex[:12]


def is_enabled() -> bool:
    return _enabled


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


@_safe(lambda: None)
def mark_pending(stage: str) -> None:
    """Stamp a pre-turn stage (voice path); the next begin_turn adopts it."""
    if not _enabled or stage not in _PENDING_STAGES:
        return
    now = _now()
    with _lock:
        if stage == "wake":
            _pending.clear()
        _pending[stage] = now


@_safe(lambda: "")
def begin_turn(source: str = "voice", **meta: Any) -> str:
    """Open a turn, adopting fresh pending marks; finalises any earlier turn."""
    global _latest
    turn_id = new_turn_id()
    if not _enabled:
        return turn_id
    text = meta.pop("text", None)
    turn = _Turn(turn_id, str(source)[:_MAX_STR])
    if text is not None:
        raw = str(text)
        turn.text_len = len(raw)
        turn.text_hash = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]
        if getattr(Config, "TRACE_STORE_TEXT", False):
            turn.text = raw
    _apply_meta(turn, meta)
    now = _now()
    with _lock:
        finished = [_finalise_locked(t) for t in list(_active.values())]
        if turn.source == "text":
            turn.stamps["wake"] = now
        else:
            for stage, at in _pending.items():
                if now - at <= _PENDING_TTL_S:
                    turn.stamps[stage] = at
            _pending.clear()
        _active[turn_id] = turn
        _latest = turn_id
    _store.submit(finished)
    return turn_id


@_safe(lambda: None)
def mark(stage: str, turn_id: Optional[str] = None, **kv: Any) -> None:
    """Stamp `stage` on the given (default: latest) active turn."""
    if not _enabled or stage not in _STAGES:
        return
    now = _now()
    rec = None
    with _lock:
        turn = _active.get(turn_id or _latest)
        if turn is None:
            return
        if stage not in _FIRST_WINS or stage not in turn.stamps:
            turn.stamps[stage] = now
        if kv:
            _apply_meta(turn, kv)
        if stage == "tts_done" and turn.ended_at is not None:
            rec = _finalise_locked(turn)
    if rec is not None:
        _store.submit([rec])


@_safe(lambda: None)
def annotate(turn_id: Optional[str] = None, **meta: Any) -> None:
    """Attach lang / route / intent / model / tier / error_code / tainted ..."""
    if not _enabled or not meta:
        return
    with _lock:
        turn = _active.get(turn_id or _latest)
        if turn is not None:
            _apply_meta(turn, meta)


@_safe(lambda: None)
def end_turn(outcome: str = "ok", turn_id: Optional[str] = None, **kv: Any) -> None:
    """Close the turn (first call wins); persists once tts_done is also known."""
    if not _enabled:
        return
    now = _now()
    rec = None
    with _lock:
        turn = _active.get(turn_id or _latest)
        if turn is None or turn.ended_at is not None:
            return
        turn.ended_at = now
        turn.stamps["end"] = now
        turn.outcome = outcome if outcome in _OUTCOMES else "ok"
        _apply_meta(turn, kv)
        if "tts_done" in turn.stamps:
            rec = _finalise_locked(turn)
    if rec is not None:
        _store.submit([rec])
    else:
        _store.ensure_thread()


@_safe(lambda: None)
def _sweep() -> None:
    """Finalise turns that ended more than 20 s ago without a tts_done."""
    now = _now()
    with _lock:
        due = [
            t for t in _active.values()
            if t.ended_at is not None and now - t.ended_at >= _FINALISE_AFTER_END_S
        ]
        records = [_finalise_locked(t) for t in due]
    _store.submit(records)


@_safe(list)
def get_recent(n: int = 10) -> list[dict[str, Any]]:
    """Newest-first metric rows; never contains text."""
    _sweep()
    with _lock:
        rows = list(_ring)[-n:] if n > 0 else []
    return [{k: r[k] for k in _RECENT_KEYS} for r in reversed(rows)]


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; None below 5 samples."""
    if len(values) < _MIN_SAMPLES:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100.0 * len(ordered)) - 1)]


def _column(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [r[key] for r in rows if r[key] is not None]


@_safe(dict)
def get_summary(window_turns: int = 50) -> dict[str, Any]:
    """Percentiles and stage averages over the newest `window_turns` turns."""
    _sweep()
    with _lock:
        rows = list(_ring)[-max(1, int(window_turns)):]
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r["route"] or "unknown", []).append(r)
    by_route = {
        route: {
            "count": len(group),
            "ttfa_p50": _percentile(_column(group, "ttfa_ms"), 50),
            "ttfa_p95": _percentile(_column(group, "ttfa_ms"), 95),
            "llm_ttft_p50": _percentile(_column(group, "llm_ttft_ms"), 50),
        }
        for route, group in groups.items()
    }
    stage_avg = {}
    for name, key in _STAGE_AVG:
        vals = _column(rows, key)
        stage_avg[name] = round(sum(vals) / len(vals), 1) if vals else None
    ttfa = _column(rows, "ttfa_ms")
    return {
        "count": len(rows),
        "ttfa_p50": _percentile(ttfa, 50),
        "ttfa_p95": _percentile(ttfa, 95),
        "by_route": by_route,
        "stage_avg": stage_avg,
        "dropped_traces": _store.dropped,
    }


def dropped_count() -> int:
    """Records dropped because the persistence queue was full."""
    return _store.dropped