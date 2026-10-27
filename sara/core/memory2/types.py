"""Frozen result and hit types for Memory 2.0."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactResult:
    """Outcome of Memory2Store.upsert_fact."""

    fact_id: int
    action: str  # inserted | superseded_old | reinforced | duplicate
    superseded_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class Mem2Hit:
    """One retrieval hit (fact or event) returned to callers."""

    kind: str
    id: int
    text: str
    score: float
    when: str
    confidence: float
    source_turn_id: str | None
    status: str
    pinned: bool


@dataclass(frozen=True)
class ExtractResult:
    """Summary of one extraction pass."""

    turns: int
    facts: int
    events: int
    entities: int
    skipped: int
    ok: bool
    reason: str