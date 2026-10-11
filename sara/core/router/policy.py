"""Static (legacy-equivalent) rules, hard floors and cascade ordering."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Optional, Sequence

if TYPE_CHECKING:
    from .features import Features
    from .tiers import Tier

HIGH_COMPLEXITY = 0.55

# Copy of the legacy engine rule (engine._is_simple_query); a test keeps them equal.
_SIMPLE_QUERY_MAX_WORDS = 6
_SIMPLE_QUERY_PATTERNS = re.compile(
    r"\b(volume|brightness|mute|unmute|time|date|battery|pause|"
    r"resume|stop|skip|next|previous|louder|quieter|screenshot)\b",
    re.IGNORECASE,
)


def is_simple_query(text: str) -> bool:
    """Short known-vocabulary command; never raises."""
    try:
        if not text or not text.strip():
            return False
        words = text.strip().split()
        if len(words) > _SIMPLE_QUERY_MAX_WORDS:
            return False
        return bool(_SIMPLE_QUERY_PATTERNS.search(text))
    except Exception:
        return False


def family(tiers: Sequence["Tier"]) -> List["Tier"]:
    """Tiers of one backend (gemini wins when mixed), cheapest first."""
    ordered = sorted(tiers, key=lambda t: t.cost)
    if not ordered:
        return []
    backend = "gemini" if any(t.backend == "gemini" for t in ordered) else ordered[0].backend
    return [t for t in ordered if t.backend == backend]


def main_tier(tiers: Sequence["Tier"]) -> Optional["Tier"]:
    """The '*_main' tier of the family (second cheapest when unnamed)."""
    fam = family(tiers)
    if not fam:
        return None
    for tier in fam:
        if tier.name.endswith("_main"):
            return tier
    return fam[min(1, len(fam) - 1)]


def _pro_tier(fam: Sequence["Tier"]) -> Optional["Tier"]:
    for tier in fam:
        if tier.name.endswith("_pro"):
            return tier
    return None


def legacy_choice(feat: "Features", tiers: Sequence["Tier"]) -> str:
    """Tier the legacy engine rule executes: simple -> cheapest, else main."""
    fam = family(tiers)
    if not fam:
        return ""
    if feat.simple:
        return fam[0].name
    return main_tier(tiers).name


def static_choice(feat: "Features", tiers: Sequence["Tier"]) -> str:
    """Legacy rule plus the 'think hard' cue -> pro tier when it exists."""
    fam = family(tiers)
    if not fam:
        return ""
    pro = _pro_tier(fam)
    if feat.think_cue and pro is not None:
        return pro.name
    return legacy_choice(feat, tiers)


def floor_tier(feat: "Features", tiers: Sequence["Tier"]) -> str:
    """Cheapest tier the learner may use for this prompt."""
    fam = family(tiers)
    if not fam:
        return ""
    floor = fam[0]
    if feat.needs_tools or feat.complexity >= HIGH_COMPLEXITY:
        floor = max(floor, main_tier(tiers), key=lambda t: t.cost)
    if feat.lang != "en":
        base = fam[0]
        if base.backend == "ollama" and base.name.endswith("_fast") and len(fam) > 1:
            base = fam[1]
        floor = max(floor, base, key=lambda t: t.cost)
    return floor.name


def allowed_tiers(feat: "Features", tiers: Sequence["Tier"]) -> List[str]:
    """Names of the family tiers at or above the floor, cheapest first."""
    fam = family(tiers)
    floor = floor_tier(feat, tiers)
    names = [t.name for t in fam]
    return names[names.index(floor):] if floor in names else names


def adjacent(name: str, tiers: Sequence["Tier"]) -> List[str]:
    """Neighbouring tier names (one cheaper, one dearer) in the family."""
    names = [t.name for t in family(tiers)]
    if name not in names:
        return []
    i = names.index(name)
    return [names[j] for j in (i - 1, i + 1) if 0 <= j < len(names)]


def cascade_next(name: str, tiers: Sequence["Tier"]) -> Optional[str]:
    """Next tier up (higher cost) on the same backend, else None."""
    ordered = sorted(tiers, key=lambda t: t.cost)
    current = next((t for t in ordered if t.name == name), None)
    if current is None:
        return None
    for tier in ordered:
        if tier.backend == current.backend and tier.cost > current.cost:
            return tier.name
    return None