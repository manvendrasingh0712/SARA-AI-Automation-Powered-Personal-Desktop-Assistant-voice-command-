"""Router-mode runner: classifies rows with the real intent engine, never runs handlers."""

from __future__ import annotations

import logging
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence, Tuple

from sara.core.intent import detect_intent
from sara.core.tool_router import has_probable_tool_intent

from bench.dataset import Row

log = logging.getLogger("bench.runner")

ERROR_INTENT = "error"


@dataclass(frozen=True)
class Result:
    """Router outcome for one row."""

    row_id: str
    pred_intent: str
    pred_groups: Tuple[str, ...]
    gate_open: bool
    latency_ms: float


@contextmanager
def _isolated_tmp() -> Iterator[None]:
    """Point tempfile at a throw-away directory for the duration of a run."""
    previous = tempfile.tempdir
    with tempfile.TemporaryDirectory(prefix="sara_bench_") as tmp:
        tempfile.tempdir = tmp
        try:
            yield
        finally:
            tempfile.tempdir = previous


def _groups(match: Optional[re.Match]) -> Tuple[str, ...]:
    if match is None:
        return ()
    return tuple(g.strip().lower() for g in match.groups() if g is not None)


def _classify(text: str) -> Tuple[str, Tuple[str, ...], float]:
    """Return (intent, groups, latency_ms) with the LRU cache cleared first."""
    detect_intent.cache_clear()
    start = time.perf_counter()
    try:
        intent, match = detect_intent(text)
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        log.warning("detect_intent failed (%s)", type(exc).__name__)
        return ERROR_INTENT, (), elapsed
    elapsed = (time.perf_counter() - start) * 1000.0
    return intent, _groups(match), elapsed


def _gate(text: str) -> bool:
    try:
        return bool(has_probable_tool_intent(text))
    except Exception as exc:
        log.warning("has_probable_tool_intent failed (%s)", type(exc).__name__)
        return False


def run_router(rows: Sequence[Row], warmup: int = 20) -> List[Result]:
    """Classify every row; warm-up calls are untimed and discarded."""
    items = list(rows)
    if not items:
        return []
    results: List[Result] = []
    with _isolated_tmp():
        for i in range(max(0, warmup)):
            _classify(items[i % len(items)].text)
        for row in items:
            intent, groups, latency = _classify(row.text)
            gate = _gate(row.text) if intent == "chat" else False
            results.append(Result(row.id, intent, groups, gate, round(latency, 4)))
    return results