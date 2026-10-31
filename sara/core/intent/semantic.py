"""
sara.core.intent.semantic
Semantic intent fallback (audit part 2, O1): when no regex matches, compare the sentence's embedding with example phrases of
every low-risk intent and, if one is clearly closest, use it instead of asking the language-model tool router (1-8 s).

  cosine >= tau_high and a clear margin over the runner-up  -> that intent
  otherwise                                                 -> None (the existing tool router / chat path runs)

Only intents of tier 0-1 (read-only / reversible, sara.core.security.tiers) can be returned, so a wrong guess can never be
destructive. Pure numpy; the embedding function is injected (the project's Ollama embedder in production).
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

EmbedFn = Callable[[str], Optional[Sequence[float]]]


class SemanticIntentIndex:
    def __init__(self, tau_high: float = 0.80, margin: float = 0.04, max_tier: int = 1):
        self.tau_high, self.margin, self.max_tier = tau_high, margin, max_tier
        self._labels: List[str] = []
        self._matrix: Optional[np.ndarray] = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._matrix is not None and len(self._labels) > 0

    def build(self, examples: Dict[str, Iterable[str]], embed: EmbedFn, tier_of: Callable[[str], int]) -> int:
        labels: List[str] = []
        rows: List[np.ndarray] = []
        for intent, phrases in examples.items():
            if intent == "chat" or tier_of(intent) > self.max_tier:
                continue
            for phrase in phrases:
                vec = embed(phrase)
                if vec is None:
                    continue
                v = np.asarray(vec, dtype=np.float32)
                n = float(np.linalg.norm(v))
                if n > 0:
                    labels.append(intent)
                    rows.append(v / n)
        with self._lock:
            self._labels = labels
            self._matrix = np.vstack(rows) if rows else None
        return len(labels)

    def classify(self, text: str, embed: EmbedFn) -> Tuple[Optional[str], float, float]:
        """(intent or None, best score, margin over the best OTHER intent)."""
        with self._lock:
            matrix, labels = self._matrix, self._labels
        if matrix is None:
            return None, 0.0, 0.0
        vec = embed(text)
        if vec is None:
            return None, 0.0, 0.0
        v = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(v))
        if n == 0:
            return None, 0.0, 0.0
        scores = matrix @ (v / n)
        order = np.argsort(-scores)
        best = int(order[0])
        best_intent, best_score = labels[best], float(scores[best])
        other = next((float(scores[i]) for i in order[1:] if labels[int(i)] != best_intent), 0.0)
        margin = best_score - other
        if best_score >= self.tau_high and margin >= self.margin:
            return best_intent, best_score, margin
        return None, best_score, margin
