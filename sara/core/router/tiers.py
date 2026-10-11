"""Model tiers for the learned router (cost = relative quota pressure, not money)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, List

logger = logging.getLogger("sara.router.tiers")

_BACKENDS = ("gemini", "ollama")
_DEFAULT_GEMINI = "gemini-2.5-flash"


@dataclass(frozen=True)
class Tier:
    """One selectable model."""

    name: str
    backend: str
    model: str
    cost: float
    enabled: bool = True


def _text(cfg: Any, key: str) -> str:
    return str(getattr(cfg, key, "") or "").strip()


def _default_tiers(cfg: Any) -> List[Tier]:
    backend = (_text(cfg, "LLM_BACKEND") or "gemini").lower()
    tiers: List[Tier] = []
    if backend == "ollama":
        main = _text(cfg, "OLLAMA_MODEL")
        fast = _text(cfg, "OLLAMA_FAST_MODEL")
        if fast and fast != main:
            tiers.append(Tier("local_fast", "ollama", fast, 0.0))
        if main:
            tiers.append(Tier("local_main", "ollama", main, 0.5))
        return tiers
    main = _text(cfg, "GEMINI_MODEL") or _DEFAULT_GEMINI
    fast = _text(cfg, "GEMINI_FAST_MODEL")
    pro = _text(cfg, "GEMINI_PRO_MODEL")
    if fast and fast != main:
        tiers.append(Tier("cloud_fast", "gemini", fast, 1.0))
    tiers.append(Tier("cloud_main", "gemini", main, 2.0))
    if pro:
        tiers.append(Tier("cloud_pro", "gemini", pro, 6.0))
    return tiers


def _parse_tiers(raw: str) -> List[Tier]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("ROUTER_TIERS must be a list")
    out: List[Tier] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("tier must be an object")
        backend = str(item["backend"]).strip().lower()
        name = str(item["name"]).strip()
        model = str(item["model"]).strip()
        if backend not in _BACKENDS or not name or not model:
            raise ValueError("invalid tier")
        out.append(
            Tier(name, backend, model, float(item["cost"]), bool(item.get("enabled", True)))
        )
    return out


def load_tiers(cfg: Any = None) -> List[Tier]:
    """Enabled tiers, cheapest first, de-duplicated by (backend, model)."""
    try:
        if cfg is None:
            from config import Config as cfg
        raw = _text(cfg, "ROUTER_TIERS")
        tiers = None
        if raw:
            try:
                tiers = _parse_tiers(raw)
            except Exception:
                logger.warning("[router] ROUTER_TIERS is malformed; using the default tiers")
        if tiers is None:
            tiers = _default_tiers(cfg)
    except Exception:
        logger.warning("[router] could not build the tier list; router disabled")
        return []
    seen = set()
    out: List[Tier] = []
    for tier in sorted((t for t in tiers if t.enabled), key=lambda t: t.cost):
        key = (tier.backend, tier.model)
        if key in seen:
            continue
        seen.add(key)
        out.append(tier)
    return out