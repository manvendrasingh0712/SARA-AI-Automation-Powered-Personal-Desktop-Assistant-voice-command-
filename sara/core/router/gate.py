"""Promotion gate for 'on' mode and the auto-revert quality check."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Tuple

GATE_PATH = Path(__file__).resolve().parents[3] / "bench" / "router" / "results" / "gate.json"


def evaluate(labelled: int, needed: int, path: Optional[Path] = None) -> Tuple[bool, str]:
    """(ok, message): enough labelled samples AND the offline gate file says passed."""
    missing = []
    if labelled < needed:
        missing.append(f"only {labelled} of {needed} labelled samples collected so far")
    try:
        data = json.loads((path or GATE_PATH).read_text(encoding="utf-8"))
        if not (isinstance(data, dict) and data.get("passed") is True):
            missing.append("the offline benchmark gate has not passed")
    except FileNotFoundError:
        missing.append("the offline benchmark has not been run yet (gate file missing)")
    except Exception:
        missing.append("the offline benchmark gate file cannot be read")
    return (not missing), ("ready" if not missing else "; ".join(missing))


def quality_dropped(store: Any, window: int, baseline_min: int, drop: float) -> bool:
    """True when the last `window` learner-chosen turns are > `drop` below the static baseline."""
    store.flush(1.0)
    learner_avg, learner_n = store.avg_quality(True, window)
    base_avg, base_n = store.avg_quality(False, window)
    if learner_n < window or base_n < baseline_min:
        return False
    if learner_avg is None or base_avg is None:
        return False
    return learner_avg < base_avg * (1.0 - drop)