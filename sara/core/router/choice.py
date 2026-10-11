"""Shared router value types and live Config helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .tiers import Tier


@dataclass(frozen=True)
class TierChoice:
    """One routing decision; `applied` is True only when the engine must use `tier`."""

    tier: Optional[Tier]
    static_tier: str
    mode: str
    propensity: float
    explore: bool
    reason: str
    decision_id: str
    applied: bool


def off_choice(reason: str) -> TierChoice:
    """The no-op decision: keep the legacy model selection, log nothing."""
    return TierChoice(None, "", "off", 1.0, False, reason, "", False)


def cfg() -> Any:
    """The live Config class."""
    from config import Config

    return Config


def num(name: str, default: float) -> float:
    """Numeric Config value read live, with a safe default."""
    try:
        return float(getattr(cfg(), name, default))
    except (TypeError, ValueError):
        return float(default)