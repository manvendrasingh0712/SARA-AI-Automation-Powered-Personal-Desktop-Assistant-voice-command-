"""Baseline for the memory benchmark: the old vector-only RAG over raw user utterances."""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

TOP_K = 5
FLOOR = 0.30
_MAX_ITEMS = 5000


class VectorBaseline:
    """Cosine top-k over embedded utterances; forgetting deletes the single nearest utterance."""

    def __init__(self, embed: Callable[[str], Any], k: int = TOP_K, floor: float = FLOOR) -> None:
        self._embed = embed
        self._k = k
        self._floor = floor
        self._texts: List[str] = []
        self._vecs: List[np.ndarray] = []

    def _unit(self, text: str) -> Optional[np.ndarray]:
        try:
            value = self._embed(text)
            if value is None:
                return None
            vec = np.asarray(value, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vec))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[membench] baseline embed failed (%s)", type(exc).__name__)
            return None
        return vec / norm if vec.size and np.isfinite(norm) and norm > 0.0 else None

    def _sims(self, vec: np.ndarray) -> List[Tuple[int, float]]:
        return [(i, float(v @ vec)) for i, v in enumerate(self._vecs) if v.size == vec.size]

    def add(self, text: str) -> None:
        """Store one utterance (skipped when it cannot be embedded)."""
        vec = self._unit(text)
        if vec is None:
            return
        if len(self._texts) >= _MAX_ITEMS:
            self._texts.pop(0)
            self._vecs.pop(0)
        self._texts.append(text)
        self._vecs.append(vec)

    def forget(self, phrase: str) -> bool:
        """Delete the utterance nearest to ``phrase``; False when nothing was deleted."""
        vec = self._unit(phrase)
        pairs = self._sims(vec) if vec is not None else []
        if not pairs:
            return False
        best = max(pairs, key=lambda pair: (pair[1], -pair[0]))[0]
        del self._texts[best]
        del self._vecs[best]
        return True

    def query(self, text: str) -> List[str]:
        """Utterance texts with cosine >= floor, best first, at most k."""
        vec = self._unit(text)
        pairs = self._sims(vec) if vec is not None else []
        ranked = sorted(pairs, key=lambda pair: (-pair[1], pair[0]))
        return [self._texts[i] for i, score in ranked if score >= self._floor][: self._k]