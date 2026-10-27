"""Disjoint LinUCB (numpy only): Sherman-Morrison updates and atomic state files."""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

logger = logging.getLogger("sara.router.linucb")

STATE_VERSION = 1
_REINVERT_EVERY = 500
_COND_CHECK_EVERY = 50
_COND_LIMIT = 1e8


class LinUCB:
    """One ridge-regression arm per tier; score = mean + alpha * uncertainty."""

    def __init__(
        self,
        arms: Sequence[str],
        dim: int,
        alpha: float = 0.8,
        reg: float = 1.0,
        schema: int = 1,
    ) -> None:
        self.arms = list(arms)
        self.dim = int(dim)
        self.alpha = float(alpha)
        self.reg = float(reg)
        self.schema = int(schema)
        self._lock = threading.RLock()
        self._a = {a: np.eye(self.dim) * self.reg for a in self.arms}
        self._inv = {a: np.eye(self.dim) / self.reg for a in self.arms}
        self._b = {a: np.zeros(self.dim) for a in self.arms}
        self._since = {a: 0 for a in self.arms}
        self.counts: Dict[str, int] = {a: 0 for a in self.arms}

    def _vec(self, x: Any) -> np.ndarray:
        v = np.asarray(x, dtype=float).reshape(-1)
        if v.shape[0] != self.dim:
            raise ValueError("feature dimension mismatch")
        return v

    def scores(self, x: Any) -> Dict[str, float]:
        """UCB score of every arm."""
        v = self._vec(x)
        out: Dict[str, float] = {}
        with self._lock:
            for arm in self.arms:
                inv = self._inv[arm]
                mean = float((inv @ self._b[arm]) @ v)
                var = max(0.0, float(v @ inv @ v))
                out[arm] = mean + self.alpha * math.sqrt(var)
        return out

    def choose(self, x: Any, allowed: Sequence[str]) -> str:
        """Best-scoring arm among `allowed` (first wins ties)."""
        sc = self.scores(x)
        cands = [a for a in allowed if a in sc] or list(sc)
        return max(cands, key=lambda a: sc[a])

    def _reinvert(self, arm: str) -> None:
        a = self._a[arm]
        try:
            inv = np.linalg.inv(a)
        except np.linalg.LinAlgError:
            inv = np.linalg.pinv(a)
        self._inv[arm] = (inv + inv.T) / 2.0
        self._since[arm] = 0

    def update(self, arm: str, x: Any, reward: float) -> None:
        """Learn one (context, reward) sample for `arm`."""
        if arm not in self._a:
            return
        r = float(reward)
        if not math.isfinite(r):
            return
        v = self._vec(x)
        with self._lock:
            inv = self._inv[arm]
            iv = inv @ v
            denom = 1.0 + float(v @ iv)
            self._a[arm] = self._a[arm] + np.outer(v, v)
            self._b[arm] = self._b[arm] + r * v
            self.counts[arm] += 1
            self._since[arm] += 1
            if not (math.isfinite(denom) and denom > 1e-9):
                self._reinvert(arm)
                return
            self._inv[arm] = inv - np.outer(iv, iv) / denom
            n = self._since[arm]
            if n >= _REINVERT_EVERY or (
                n % _COND_CHECK_EVERY == 0 and np.linalg.cond(self._a[arm]) > _COND_LIMIT
            ):
                self._reinvert(arm)

    def to_state(self) -> dict:
        """JSON-serialisable snapshot."""
        with self._lock:
            return {
                "version": STATE_VERSION,
                "schema": self.schema,
                "dim": self.dim,
                "alpha": self.alpha,
                "reg": self.reg,
                "arms": {
                    a: {
                        "A": self._a[a].tolist(),
                        "b": self._b[a].tolist(),
                        "n": int(self.counts[a]),
                    }
                    for a in self.arms
                },
            }

    @classmethod
    def from_state(
        cls,
        state: Mapping[str, Any],
        *,
        arms: Optional[Sequence[str]] = None,
        dim: Optional[int] = None,
        schema: Optional[int] = None,
        alpha: Optional[float] = None,
    ) -> Optional["LinUCB"]:
        """Rebuild from a snapshot; None on schema / dim / arm mismatch."""
        try:
            if state.get("version") != STATE_VERSION:
                return None
            st_dim = int(state["dim"])
            st_schema = int(state["schema"])
            data = state["arms"]
            if dim is not None and st_dim != int(dim):
                return None
            if schema is not None and st_schema != int(schema):
                return None
            if arms is not None and set(data) != set(arms):
                return None
            obj = cls(
                list(arms) if arms is not None else list(data),
                st_dim,
                float(alpha) if alpha is not None else float(state.get("alpha", 0.8)),
                float(state.get("reg", 1.0)),
                st_schema,
            )
            for arm in obj.arms:
                entry = data[arm]
                a = np.array(entry["A"], dtype=float)
                b = np.array(entry["b"], dtype=float)
                if a.shape != (st_dim, st_dim) or b.shape != (st_dim,):
                    return None
                if not (np.isfinite(a).all() and np.isfinite(b).all()):
                    return None
                obj._a[arm] = a
                obj._b[arm] = b
                obj.counts[arm] = max(0, int(entry["n"]))
                obj._reinvert(arm)
            return obj
        except Exception:
            return None

    def save(self, path: Any, extra: Optional[Mapping[str, Any]] = None) -> None:
        """Atomic write (temp file + os.replace); `extra` keys are stored alongside."""
        state = self.to_state()
        if extra:
            state.update(extra)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, p)

    @classmethod
    def load(cls, path: Any, **expect: Any) -> Optional["LinUCB"]:
        """Load a saved model; missing -> None; corrupt -> renamed to <path>.bad, None."""
        p = Path(path)
        if not p.exists():
            return None
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("state is not an object")
        except Exception:
            try:
                os.replace(p, p.with_name(p.name + ".bad"))
            except OSError:
                pass
            logger.warning("[router] corrupt learner state moved aside; starting fresh")
            return None
        return cls.from_state(state, **expect)