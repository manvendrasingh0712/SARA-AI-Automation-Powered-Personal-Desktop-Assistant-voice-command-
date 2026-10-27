"""Hybrid Memory 2.0 retrieval: entity graph, predicate cues, time windows, lexical + vector."""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from typing import Any, Callable, Iterable

import numpy as np

from . import decay, get_store
from .lexicon import (
    KIN_GROUPS,
    STOP,
    has_history_cue,
    is_memory_question,
    predicates_in_query,
    tokens,
)
from .timeparse import parse_window
from .types import Mem2Hit

__all__ = ["format_block", "is_memory_question", "last_hits", "retrieve"]

logger = logging.getLogger(__name__)

_f = decay.to_float

_W_SEM, _W_REC, _W_STR, _W_ENT, _W_USE = 0.45, 0.20, 0.20, 0.10, 0.05
_EVIDENCE_SEM = 0.35
_RECENCY_DAYS = 180.0
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mem2-retr")
_TOUCH_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem2-touch")
_TOUCH_SLOTS = threading.BoundedSemaphore(8)
_QCACHE: "OrderedDict[tuple[int, str], np.ndarray]" = OrderedDict()
_QCACHE_MAX = 64
_QLOCK = threading.Lock()
_LAST: list[Mem2Hit] = []
_LAST_LOCK = threading.Lock()


def _row_tokens(row: dict) -> frozenset[str]:
    return frozenset(tokens(f"{row.get('text') or row.get('summary') or ''} {row.get('predicate') or ''}"))


def _is_user(row: dict) -> bool:
    return str(row.get("subject") or "user").lower() == "user"


def _lex(query_tokens: list[str], row_toks: frozenset[str]) -> float:
    """Fraction of content query tokens found (exact or 4+ char prefix) in the item."""
    if not query_tokens:
        return 0.0
    hit = 0
    for q in query_tokens:
        if q in row_toks or (len(q) >= 4 and any(len(t) >= 4 and (t.startswith(q) or q.startswith(t))
                                                 for t in row_toks)):
            hit += 1
    return hit / len(query_tokens)


def _query_vec(store: Any, embed: Callable[[str], Any] | None, text: str) -> np.ndarray | None:
    if embed is None:
        return None
    key = (id(store), text.lower()[:200])
    with _QLOCK:
        cached = _QCACHE.get(key)
        if cached is not None:
            _QCACHE.move_to_end(key)
            return cached
    raw = embed(text)
    if raw is None:
        return None
    vec = np.asarray(raw, dtype=np.float32)
    if vec.ndim != 1 or vec.size == 0 or not bool(np.all(np.isfinite(vec))):
        return None
    with _QLOCK:
        _QCACHE[key] = vec
        while len(_QCACHE) > _QCACHE_MAX:
            _QCACHE.popitem(last=False)
    return vec


def _stamp(row: dict, kind: str) -> float:
    return _f(row.get("ts") if kind == "events" else row.get("valid_from") or row.get("created_at"))


def _when(row: dict, kind: str) -> str:
    stamp = _stamp(row, kind)
    if stamp <= 0:
        return ""
    day = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
    return f"earlier, {day}" if row.get("status") == "superseded" else day


def _expand(rows: Iterable[dict], ents: dict[str, frozenset[str]], by_subject: dict[str, list[dict]]):
    """One hop: facts whose object names a known entity yield that entity's facts."""
    for row in rows:
        obj = frozenset(tokens(row.get("object_text")))
        for name, toks in ents.items():
            if toks <= obj and name != str(row.get("subject")):
                yield from by_subject[name]


def _candidates(rows, facts, events, qset, preds, window):
    """Stage 1: entity / kinship / predicate / time candidates -> {key: flags}."""
    cand: dict[tuple[str, int], dict[str, float]] = {}

    def add(key: tuple[str, int], **flags: float) -> None:
        slot = cand.setdefault(key, {"ent": 0.0, "pred": 0.0, "time": 0.0})
        for name, value in flags.items():
            slot[name] = max(slot[name], value)

    by_subject: dict[str, list[dict]] = {}
    for row in facts:
        if not _is_user(row):
            by_subject.setdefault(str(row.get("subject")), []).append(row)
    ents = {name: frozenset(tokens(name)) for name in by_subject}
    linked = [name for name, toks in ents.items() if toks and toks <= qset]
    kin_rows = [r for g in KIN_GROUPS if g & qset for r in facts if _is_user(r) and g & _row_tokens(r)]
    unresolved = any(g & qset for g in KIN_GROUPS) and not kin_rows and not linked
    for name in linked:
        for row in by_subject[name]:
            add(("facts", int(row["id"])), ent=1.0)
        for row in facts:
            if ents[name] <= frozenset(tokens(row.get("object_text"))):
                add(("facts", int(row["id"])), ent=1.0)
        for row in events:
            if ents[name] <= _row_tokens(row):
                add(("events", int(row["id"])), ent=1.0)
    for row in kin_rows:
        add(("facts", int(row["id"])), ent=1.0)
    if not linked and not kin_rows:
        for row in facts:
            if _is_user(row) and row.get("predicate") in preds:
                add(("facts", int(row["id"])), pred=1.0)
    seeds = [rows[k] for k in cand if k[0] == "facts" and k in rows]
    for row in _expand(seeds, ents, by_subject):
        add(("facts", int(row["id"])), ent=1.0)
    for key in cand:
        if key[0] == "facts" and rows.get(key, {}).get("predicate") in preds:
            cand[key]["pred"] = 1.0
    if window:
        for row in events:
            if window[0] <= _f(row.get("ts")) < window[1]:
                add(("events", int(row["id"])), time=1.0)
    return cand, unresolved, bool(linked or kin_rows)


def _search(store: Any, text: str, now: float, k: int, floor: float,
            embed: Callable[[str], Any] | None) -> list[Mem2Hit]:
    qtoks = tokens(text)
    qset = set(qtoks)
    statuses = ("active", "superseded") if has_history_cue(text) else ("active",)
    facts = store.iter_rows("facts", statuses)
    events = store.iter_rows("events", ("active",))
    rows = {("facts", int(r["id"])): r for r in facts}
    rows.update({("events", int(r["id"])): r for r in events})
    preds = predicates_in_query(text)
    window = parse_window(text, now)
    cand, unresolved, has_entity = _candidates(rows, facts, events, qset, preds, window)
    if unresolved:
        return []
    content = [t for t in qtoks if t not in STOP and len(t) > 1]
    sem_map: dict[tuple[str, int], float] = {}
    if not (preds or has_entity or window) or len(cand) < k:
        for key, row in rows.items():
            if key not in cand and _lex(content, _row_tokens(row)) >= _EVIDENCE_SEM:
                cand[key] = {"ent": 0.0, "pred": 0.0, "time": 0.0}
        try:
            vec = _query_vec(store, embed, text)
            if vec is not None:
                found = store.vector_candidates(vec, ("facts", "events"), max(k * 4, 20), statuses)
                sem_map = {(str(a), int(b)): float(c) for a, b, c in found}
                for key, cos in sem_map.items():
                    if key in rows and key not in cand and cos >= _EVIDENCE_SEM:
                        cand[key] = {"ent": 0.0, "pred": 0.0, "time": 0.0}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Memory2] vector stage skipped (%s)", type(exc).__name__)
    scored, tau = [], decay.tau_days()
    for key, flags in cand.items():
        row = rows.get(key)
        if row is None:
            continue
        sem = min(1.0, max(sem_map.get(key, 0.0), _lex(content, _row_tokens(row))))
        scored.append((key, row, flags, sem))
    sem_on = any(item[3] > 0 for item in scored)
    out = []
    for key, row, flags, sem in scored:
        if not (flags["ent"] or flags["pred"] or flags["time"] or sem >= _EVIDENCE_SEM):
            continue
        kind = key[0]
        stamp = _stamp(row, kind)
        ref = max(stamp, _f(row.get("last_used_at")))
        uses = int(_f(row.get("use_count")))
        pinned = bool(row.get("pinned"))
        rec = math.exp(-max(0.0, now - stamp) / 86400.0 / _RECENCY_DAYS)
        stren = decay.strength(_f(row.get("importance"), 0.5), ref, uses, pinned, now, tau)
        match = min(1.0, 0.7 * flags["ent"] + 0.6 * flags["pred"] + 0.6 * flags["time"])
        use = min(1.0, math.log1p(uses) / math.log1p(10))
        raw = _W_SEM * sem + _W_REC * rec + _W_STR * stren + _W_ENT * match + _W_USE * use
        score = raw if sem_on else raw / (1.0 - _W_SEM)
        if score < floor:
            continue
        hit = Mem2Hit(
            kind=kind, id=int(row["id"]), text=str(row.get("text") or row.get("summary") or ""),
            score=round(score, 4), when=_when(row, kind), confidence=_f(row.get("confidence"), 1.0),
            source_turn_id=row.get("source_turn_id"), status=str(row.get("status") or "active"),
            pinned=pinned,
        )
        out.append((score, stamp, hit))
    out.sort(key=lambda item: (-item[0], -item[1]))
    return [item[2] for item in out[:k]]


def _touch_async(store: Any, hits: list[Mem2Hit], now: float) -> None:
    ids = [(h.kind, h.id) for h in hits]
    if not _TOUCH_SLOTS.acquire(blocking=False):
        return

    def _job() -> None:
        try:
            store.touch(ids, now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Memory2] touch failed (%s)", type(exc).__name__)
        finally:
            _TOUCH_SLOTS.release()

    try:
        _TOUCH_POOL.submit(_job)
    except Exception:  # noqa: BLE001
        _TOUCH_SLOTS.release()


def _remember(hits: list[Mem2Hit]) -> list[Mem2Hit]:
    with _LAST_LOCK:
        _LAST[:] = hits
    return hits


def retrieve(query: str, *, store: Any = None, now: float | None = None, k: int | None = None,
             embed: Callable[[str], Any] | None = None, timeout_ms: int | None = None,
             min_score: float | None = None) -> list[Mem2Hit]:
    """Ranked memories for ``query``; hard timeout, fail-open ([]) on any problem."""
    hits: list[Mem2Hit] = []
    st: Any = None
    stamp = time.time()
    try:
        text = str(query or "").strip()
        st = store if store is not None else get_store()
        if not text or st is None:
            return _remember([])
        stamp = time.time() if now is None else float(now)
        top = max(1, int(k if k is not None else decay.cfg_value("MEMORY2_TOPK", 5)))
        floor = _f(min_score if min_score is not None else decay.cfg_value("MEMORY2_MIN_SCORE", 0.25), 0.25)
        wait_ms = _f(timeout_ms if timeout_ms is not None
                     else decay.cfg_value("MEMORY2_RETRIEVAL_TIMEOUT_MS", 400), 400.0)
        fn = embed if embed is not None else getattr(st, "_embed_text", None)
        future = _POOL.submit(_search, st, text, stamp, top, floor, fn)
        try:
            hits = future.result(timeout=max(0.01, wait_ms / 1000.0))
        except FutureTimeout:
            future.cancel()
            logger.warning("[Memory2] retrieval timed out after %d ms", int(wait_ms))
            hits = []
    except Exception as exc:  # noqa: BLE001
        logger.error("[Memory2] retrieval failed (%s)", type(exc).__name__)
        hits = []
    if hits and st is not None:
        _touch_async(st, hits, stamp)
    return _remember(hits)


def last_hits() -> list[Mem2Hit]:
    """Thread-safe copy of the most recent retrieve() result."""
    with _LAST_LOCK:
        return list(_LAST)


def format_block(hits: list[Mem2Hit], max_chars: int = 600, max_items: int = 5) -> str:
    """Plain '- text (when)' lines, bounded; the caller applies the security wrapper."""
    lines: list[str] = []
    used = 0
    for hit in list(hits)[: max(0, max_items)]:
        body = " ".join(str(hit.text).split())
        line = f"- {body} ({hit.when})" if hit.when else f"- {body}"
        if used + len(line) + (1 if lines else 0) > max_chars:
            if not lines and max_chars > 1:
                lines.append(line[: max_chars - 1] + "…")
            break
        used += len(line) + (1 if lines else 0)
        lines.append(line)
    return "\n".join(lines)