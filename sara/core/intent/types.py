"""sara.core.intent.types - typed, immutable contracts shared between the intent
routing, semantic resolution, security and execution phases.

This module only holds data definitions and small helpers. It is stdlib-only and
must never import anything else from SARA.
"""
from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

CHAT_INTENT = "chat"
UNKNOWN_INTENT = "unknown"
CLARIFY_INTENT = "needs_clarification"
NON_ACTION_INTENTS = frozenset({CHAT_INTENT, UNKNOWN_INTENT, CLARIFY_INTENT})


class ExecPath(str, enum.Enum):
    HANDLER = "handler"
    SIMPLE_ACTION = "simple_action"
    UNBOUND = "unbound"
    NONE = "none"


class Resolver(str, enum.Enum):
    FAST = "fast"
    EMBEDDING = "embedding"
    SEMANTIC = "semantic"
    CONTEXT = "context"


class RouteState(str, enum.Enum):
    DIRECT = "direct"
    SEMANTIC = "semantic"
    AMBIGUOUS = "ambiguous"
    CHAT = "chat"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class EntitySpec:
    name: str
    type: str
    required: bool = True
    desc: str = ""
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentSpec:
    name: str
    desc: str
    entities: tuple[EntitySpec, ...] = ()
    named_groups: bool = False
    exec_path: ExecPath = ExecPath.UNBOUND
    tier: int = 2
    curated: bool = True
    tool_name: str | None = None

    @property
    def entity_names(self) -> tuple[str, ...]:
        return tuple(e.name for e in self.entities)

    @property
    def required_entities(self) -> tuple[EntitySpec, ...]:
        return tuple(e for e in self.entities if e.required)

    @property
    def is_zero_arg(self) -> bool:
        return not self.entities


# Instances are intentionally not hashable because `entities` is a mapping.
@dataclass(frozen=True)
class ResolvedCommand:
    intent: str
    entities: Mapping[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    normalized_text: str = ""
    language: str = "unknown"
    script: str = "unknown"
    confidence: float = 0.0
    command_likelihood: float = 0.0
    ambiguity: bool = False
    requires_confirmation: bool = False
    resolver: Resolver = Resolver.FAST
    state: RouteState = RouteState.DIRECT
    evidence: tuple[str, ...] = ()
    clarification: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within 0.0..1.0, got {self.confidence!r}")
        if not 0.0 <= self.command_likelihood <= 1.0:
            raise ValueError(
                f"command_likelihood must be within 0.0..1.0, got {self.command_likelihood!r}"
            )
        # Copy first so later mutation of the caller's dict cannot leak in;
        # object.__setattr__ is needed because the dataclass is frozen.
        object.__setattr__(self, "entities", MappingProxyType(dict(self.entities)))

    def entity(self, name: str, default: Any = None) -> Any:
        return self.entities.get(name, default)

    def with_changes(self, **kwargs: Any) -> ResolvedCommand:
        # dataclasses.replace calls __init__ again, so validation re-runs.
        return dataclasses.replace(self, **kwargs)

    @property
    def is_actionable(self) -> bool:
        return (
            self.intent not in NON_ACTION_INTENTS
            and self.state in (RouteState.DIRECT, RouteState.SEMANTIC)
            and not self.ambiguity
        )

    def to_log_dict(self, include_text: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "intent": self.intent,
            "resolver": self.resolver.value,
            "state": self.state.value,
            "language": self.language,
            "script": self.script,
            "confidence": self.confidence,
            "command_likelihood": self.command_likelihood,
            "ambiguity": self.ambiguity,
            "requires_confirmation": self.requires_confirmation,
            "entity_names": sorted(self.entities),
            "evidence": list(self.evidence),
        }
        # Privacy by default: user text and entity values are opt-in only.
        if include_text:
            data["raw_text"] = self.raw_text
            data["normalized_text"] = self.normalized_text
            data["entities"] = dict(self.entities)
        return data