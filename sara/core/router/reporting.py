"""Router statistics for the API / GUI."""
from __future__ import annotations

from typing import Any, Dict, Sequence, Tuple

from .policy import main_tier
from .tiers import Tier

SHOW_AFTER_SAMPLES = 50


def build_stats(
    mode: str,
    tiers: Sequence[Tier],
    store: Any,
    days: int,
    gate_state: Tuple[bool, str],
    reverted: bool,
) -> Dict[str, Any]:
    """The stats dict promised by the router contract."""
    samples = store.count()
    usage = store.usage(days)
    main = main_tier(tiers)
    saved = None
    if main is not None:
        cheaper = {t.name for t in tiers if t.cost < main.cost}
        saved = sum(n for name, n in usage.items() if name in cheaper)
    return {
        "mode": mode,
        "tiers": [
            {"name": t.name, "backend": t.backend, "model": t.model, "cost": t.cost}
            for t in tiers
        ],
        "samples": samples,
        "labelled": store.count(labelled_only=True),
        "usage": usage,
        "avg_latency_ms": store.avg_latency(days),
        "calls_saved_vs_main": saved,
        "shown": samples >= SHOW_AFTER_SAMPLES,
        "gate": {"ok": bool(gate_state[0]), "message": str(gate_state[1])},
        "reverted": bool(reverted),
    }