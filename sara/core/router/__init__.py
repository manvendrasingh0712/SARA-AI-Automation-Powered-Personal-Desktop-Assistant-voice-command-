"""Learned model router (shadow-first). Heavy modules load lazily."""
from __future__ import annotations

from typing import Any, Mapping, Optional

__all__ = ["get_router", "choose_tier"]


def get_router():
    """Process-wide Router singleton (created on first use)."""
    from .router import get_router as _get

    return _get()


def choose_tier(text: str, ctx: Optional[Mapping[str, Any]] = None):
    """Shortcut for get_router().choose(text, ctx)."""
    return get_router().choose(text, ctx)