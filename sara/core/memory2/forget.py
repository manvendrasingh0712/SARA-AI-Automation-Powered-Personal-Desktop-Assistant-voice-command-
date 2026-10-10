"""Memory 2.0 forgetting: precise phrase matching, spoken preview, retract and wipe."""
from __future__ import annotations

import logging
from typing import Any, Callable

from . import get_store
from .lexicon import CUE_TOKENS, STOP, predicates_in_query, tokens
from .types import Mem2Hit

__all__ = ["find_matches", "forget_all", "preview_text", "retract_hits"]

logger = logging.getLogger(__name__)

_SEMANTIC_MIN = 0.6
_EXAMPLE_CHARS = 70


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _match(content: list[str], row_toks: frozenset[str]) -> float:
    """Fraction of content tokens found in the item (exact or 4+ char prefix)."""
    hit = 0
    for word in content:
        if word in row_toks or (
            len(word) >= 4
            and any(len(t) >= 4 and (t.startswith(word) or word.startswith(t)) for t in row_toks)
        ):
            hit += 1
    return hit / len(content) if content else 0.0


def _hit(kind: str, row: dict, score: float) -> Mem2Hit:
    return Mem2Hit(
        kind=kind,
        id=int(row["id"]),
        text=str(row.get("text") or row.get("summary") or ""),
        score=round(score, 4),
        when="",
        confidence=_num(row.get("confidence"), 1.0),
        source_turn_id=row.get("source_turn_id"),
        status=str(row.get("status") or "active"),
        pinned=bool(row.get("pinned")),
    )


def find_matches(phrase: str, *, store: Any = None, embed: Callable[[str], Any] | None = None,
                 limit: int = 10) -> list[Mem2Hit]:
    """Active facts / events that the spoken ``phrase`` refers to; never unrelated items."""
    try:
        st = store if store is not None else get_store()
        if st is None:
            return []
        content = [t for t in tokens(phrase) if t not in STOP and t not in CUE_TOKENS and len(t) > 1]
        preds = set(predicates_in_query(phrase))
        if not content and not preds:
            return []
        rows = [("facts", r) for r in st.iter_rows("facts", ("active",))]
        rows += [("events", r) for r in st.iter_rows("events", ("active",))]
        scored: list[tuple[float, str, dict]] = []
        for kind, row in rows:
            toks = frozenset(tokens(f"{row.get('text') or row.get('summary') or ''} {row.get('predicate') or ''}"))
            ratio = _match(content, toks)
            if content and ratio >= 0.5:
                scored.append((ratio, kind, row))
        if not scored and preds:
            scored = [(0.8, "facts", r) for k, r in rows
                      if k == "facts" and r.get("predicate") in preds
                      and str(r.get("subject") or "user").lower() == "user"]
        if not scored:
            scored = _semantic(st, embed, phrase, rows)
        scored.sort(key=lambda item: -item[0])
        return [_hit(kind, row, score) for score, kind, row in scored[: max(1, int(limit))]]
    except Exception as exc:  # noqa: BLE001
        logger.error("[Memory2] forget lookup failed (%s)", type(exc).__name__)
        return []


def _semantic(st: Any, embed: Callable[[str], Any] | None, phrase: str, rows: list) -> list:
    """High-confidence vector matches, used only when no keyword / predicate match exists."""
    try:
        fn = embed if embed is not None else getattr(st, "_embed_text", None)
        vec = fn(phrase) if fn is not None else None
        if vec is None:
            return []
        by_key = {(k, int(r["id"])): r for k, r in rows}
        found = st.vector_candidates(vec, ("facts", "events"), 5, ("active",))
        return [(float(c), str(k), by_key[(str(k), int(i))]) for k, i, c in found
                if float(c) >= _SEMANTIC_MIN and (str(k), int(i)) in by_key]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Memory2] semantic forget lookup skipped (%s)", type(exc).__name__)
        return []


def preview_text(hits: list[Mem2Hit], lang: str = "english") -> str:
    """Spoken confirmation prompt: count plus up to three examples."""
    items = list(hits)
    if not items:
        return ""
    count = len(items)
    shown = "; ".join(" ".join(str(h.text).split())[:_EXAMPLE_CHARS] for h in items[:3])
    more = count - 3
    if str(lang).lower() in ("hinglish", "hindi"):
        tail = f" aur {more} baaki" if more > 0 else ""
        return f"Mujhe {count} yaadein mili: {shown}{tail}. Inhe delete kar doon? Yes ya cancel bolo."
    tail = f" and {more} more" if more > 0 else ""
    word = "memory" if count == 1 else "memories"
    return f"I found {count} {word}: {shown}{tail}. Delete them? Say yes or cancel."


def retract_hits(hits: list[Mem2Hit], *, store: Any = None) -> int:
    """Retract every hit; returns how many were retracted."""
    st = store if store is not None else get_store()
    if st is None:
        return 0
    done = 0
    for hit in hits:
        try:
            if st.retract(int(hit.id), str(hit.kind)):
                done += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Memory2] retract failed (%s)", type(exc).__name__)
    logger.info("[Memory2] retracted %d items", done)
    return done


def forget_all(*, store: Any = None) -> None:
    """Wipe every Memory 2.0 row; never raises."""
    try:
        st = store if store is not None else get_store()
        if st is not None:
            st.wipe_all()
            logger.info("[Memory2] all memories wiped")
    except Exception as exc:  # noqa: BLE001
        logger.error("[Memory2] wipe failed (%s)", type(exc).__name__)