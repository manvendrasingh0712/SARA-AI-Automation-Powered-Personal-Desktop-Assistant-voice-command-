"""
sara.core.security.taint

Per-turn taint tracking. Whenever untrusted content (web page, file, note,
clipboard, calendar entry, screenshot, recalled memory) is ingested, the
ingestion point calls mark_turn(source, flagged). Later code in the same
turn can ask turn_tainted() / turn_flagged() / turn_sources() to decide,
for example, whether a reply should be kept out of conversation history or
whether a risky action needs extra confirmation.

The record is keyed to the orchestrator's turn generation
(TURN_STATE.generation()), so it resets automatically when the next turn
begins. If the orchestrator cannot be imported (unit tests), generation 0
is used.

Tainted(str) is an optional value-level marker. Ordinary str operations
(slicing, concatenation, f-strings) return a plain str, so the per-turn
record above is the authoritative signal; use Tainted only when a value is
passed along unchanged.
"""
from __future__ import annotations

import threading
from typing import Any, List, Tuple

_lock = threading.Lock()
_state = {"gen": None, "sources": [], "flagged": False}


def _current_gen() -> int:
    try:
        from sara.orchestrator.state import TURN_STATE
        return int(TURN_STATE.generation())
    except (ImportError, AttributeError):
        return 0


def _sync_locked(gen: int) -> None:
    if _state["gen"] != gen:
        _state["gen"] = gen
        _state["sources"] = []
        _state["flagged"] = False


def mark_turn(source: str, flagged: bool = False) -> None:
    """Record that the current turn ingested untrusted content."""
    gen = _current_gen()
    with _lock:
        _sync_locked(gen)
        sources: List[str] = _state["sources"]
        if source not in sources:
            sources.append(source)
        if flagged:
            _state["flagged"] = True


def turn_sources() -> Tuple[str, ...]:
    gen = _current_gen()
    with _lock:
        _sync_locked(gen)
        return tuple(_state["sources"])


def turn_tainted() -> bool:
    return bool(turn_sources())


def turn_flagged() -> bool:
    """True if any content ingested this turn looked like an injection."""
    gen = _current_gen()
    with _lock:
        _sync_locked(gen)
        return bool(_state["flagged"])


def clear_turn() -> None:
    gen = _current_gen()
    with _lock:
        _state["gen"] = gen
        _state["sources"] = []
        _state["flagged"] = False


class Tainted(str):
    """A str that remembers it came from an untrusted source."""

    source: str
    flagged: bool

    def __new__(cls, value: str = "", source: str = "external", flagged: bool = False):
        obj = super().__new__(cls, value)
        obj.source = source
        obj.flagged = flagged
        return obj


def taint(text: Any, source: str = "external", flagged: bool = False) -> Tainted:
    return Tainted("" if text is None else str(text), source, flagged)


def is_tainted(value: Any) -> bool:
    return isinstance(value, Tainted)
