"""
sara.core.security.guard
Single decision point for every tool dispatch: argument validation, the
policy matrix, confirmation pendings and audit events. Never raises and never
imports the dispatcher. Spoken messages are fixed templates (no outside text).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from config import Config

try:
    from sara.core.security import policy, events
except Exception:  # fail-safe: T0/T1 run, T2/T3 deny
    policy = None
    events = None

try:
    from sara.core.security import tiers as _tiers
except Exception:  # tier lookup is optional: unknown -> fail-safe tier
    _tiers = None

logger = logging.getLogger(__name__)

ACTION_RUN = "run"
ACTION_CONFIRM = "confirm"
ACTION_DENY = "deny"
PENDING_ACTION = "guarded_tool"
ORIGIN_CONFIRMED = "user_confirmed"
_FAIL_TIER = 2
_HINGLISH = frozenset({"hinglish", "hindi"})
_LABELS = {
    "web_page": "a web page", "web_search": "a web page", "news": "a web page",
    "calendar": "your calendar",
    "notes": "your saved notes", "memory": "your saved notes", "reference": "your saved notes",
    "clipboard": "the clipboard", "screen": "the screen",
}
_LABELS_HI = {
    "a web page": "ek web page", "your calendar": "aapke calendar",
    "your saved notes": "aapke saved notes", "the clipboard": "clipboard",
    "the screen": "screen", "outside content": "bahar ke content",
}
_DENY_EN = "I won't do that on my own because it came from {label}. If you want it, tell me directly."
_DENY_HI = "Main ye apne aap nahi karungi kyunki ye {label} se aaya hai. Chahiye to mujhe seedha bolo."
_CONFIRM_EN = "{name} -- that request came from {label}. Say yes or cancel."
_CONFIRM_CLEAN_EN = "{name} -- shall I go ahead? Say yes or cancel."
_CONFIRM_HI = "{name} -- ye request {label} se aayi hai. Yes bolo ya cancel."
_CONFIRM_CLEAN_HI = "{name} -- kya main aage badhun? Yes bolo ya cancel."
_REPLAY_FAIL = {
    "english": "Sorry, I couldn't do that safely.",
    "hinglish": "Sorry, main ye safely nahi kar paayi.",
}


@dataclass(frozen=True)
class GuardOutcome:
    """Result of a guard check: action run | confirm | deny."""

    action: str
    reason: str
    tier: int
    message: str
    pending: Optional[dict]


class PlanStepBlocked(Exception):
    """Raised by a plan dispatcher when the guard denies a step."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _mode_off() -> bool:
    try:
        return str(getattr(Config, "SECURITY_MODE", "standard")).strip().lower() == "off"
    except Exception:
        return False


def _tier(tool: str) -> int:
    try:
        if _tiers is not None:
            return int(_tiers.tier_of(tool))
    except Exception:
        pass
    return _FAIL_TIER


def _is_hinglish(lang: str) -> bool:
    return str(lang or "").strip().lower() in _HINGLISH


def _label(sources: tuple[str, ...], lang: str) -> str:
    label = next((_LABELS[s] for s in sources if s in _LABELS), "outside content")
    return _LABELS_HI[label] if _is_hinglish(lang) else label


def _tool_words(tool: str) -> str:
    return str(tool or "").replace("_", " ").strip() or "that"


def _deny_message(sources: tuple[str, ...], lang: str) -> str:
    template = _DENY_HI if _is_hinglish(lang) else _DENY_EN
    return template.format(label=_label(sources, lang))


def _confirm_message(tool: str, sources: tuple[str, ...], lang: str) -> str:
    hi = _is_hinglish(lang)
    name = _tool_words(tool)
    if not sources:
        return (_CONFIRM_CLEAN_HI if hi else _CONFIRM_CLEAN_EN).format(name=name)
    return (_CONFIRM_HI if hi else _CONFIRM_EN).format(name=name, label=_label(sources, lang))


def _args_from_match(match: Any) -> dict:
    groups: tuple = ()
    try:
        groups = tuple(match.groups()) if match is not None else ()
    except Exception:
        groups = ()
    return {
        "g1": groups[0] if len(groups) > 0 else None,
        "g2": groups[1] if len(groups) > 1 else None,
    }


def _log(kind: str, **kwargs: Any) -> None:
    try:
        if events is not None:
            events.log_event(kind, **kwargs)
    except Exception:
        return


def _outcome(action: str, reason: str, tier: int, message: str = "",
             pending: Optional[dict] = None) -> GuardOutcome:
    return GuardOutcome(action, reason, tier, message, pending)


def _failsafe(tool: str, lang: str) -> GuardOutcome:
    tier = _tier(tool)
    if tier < 2:
        return _outcome(ACTION_RUN, "security_unavailable", tier)
    return _outcome(ACTION_DENY, "security_unavailable", tier, _deny_message((), lang))


def check_tool_call(
    tool: str,
    origin: str,
    *,
    args: Optional[Mapping[str, Any]] = None,
    match: Any = None,
    text: str = "",
    tool_name: Optional[str] = None,
    lang: str = "english",
    ttl_s: float = 60.0,
) -> GuardOutcome:
    """Decide whether a tool call runs, needs a yes, or is denied; never raises."""
    try:
        if _mode_off():
            return _outcome(ACTION_RUN, "mode_off", _tier(tool))
        if policy is None or events is None:
            return _failsafe(tool, lang)
        call_args = dict(args) if args is not None else _args_from_match(match)
        check = policy.validate_args(tool, call_args)
        if not check.ok:
            tier = _tier(tool)
            _log("arg_rejected", tool=tool, tier=tier, origin=origin, details=str(check.field)[:60])
            return _outcome(ACTION_DENY, "arg_rejected", tier, _deny_message((), lang))
        result = policy.decide(tool, origin)
        sources = tuple(result.sources)
        source_kind = ",".join(sources)
        if result.decision == policy.ALLOW:
            return _outcome(ACTION_RUN, result.reason, result.tier)
        if result.decision == policy.CONFIRM:
            pending = {
                "action": PENDING_ACTION, "target": tool, "tool": tool, "origin": origin,
                "text": text, "tool_name": tool_name,
                "arguments": dict(args) if args is not None else None,
                "sources": list(sources), "expires_at": time.time() + float(ttl_s),
            }
            _log("confirm_asked", tool=tool, tier=result.tier, origin=origin, source=source_kind)
            return _outcome(ACTION_CONFIRM, result.reason, result.tier,
                            _confirm_message(tool, sources, lang), pending)
        _log("blocked", tool=tool, tier=result.tier, origin=origin, source=source_kind,
             details=result.reason)
        return _outcome(ACTION_DENY, result.reason, result.tier, _deny_message(sources, lang))
    except Exception as exc:  # never raise into the caller
        logger.warning("guard failed: %s", type(exc).__name__)
        return _failsafe(tool, lang)


def replay_confirmed(
    pending: Mapping[str, Any],
    ctx: dict,
    *,
    handlers: Mapping[str, Callable],
    simple_actions: Mapping[str, Callable],
    detect_intent: Callable,
    build_fake_match: Callable,
    lang: str = "english",
) -> Optional[str]:
    """Run a call the user just confirmed; None when it cannot be replayed."""
    try:
        tool = str(pending.get("tool") or pending.get("target") or "")
        arguments = pending.get("arguments")
        text = str(pending.get("text") or "")
        if not tool or policy is None or events is None:
            return None
        handler = None
        match = None
        if tool not in simple_actions:
            handler = handlers.get(tool)
            match = _rebuild_match(
                tool, text, pending.get("tool_name"), arguments, detect_intent, build_fake_match
            )
            if handler is None or match is None:
                return None
        call_args = dict(arguments) if arguments is not None else _args_from_match(match)
        check = policy.validate_args(tool, call_args)
        if not check.ok:
            _log("arg_rejected", tool=tool, tier=_tier(tool), origin=ORIGIN_CONFIRMED,
                 details=str(check.field)[:60])
            return _replay_fail(lang)
        result = simple_actions[tool]() if handler is None else handler(match, ctx)
        _log("confirmed", tool=tool, tier=_tier(tool), origin=ORIGIN_CONFIRMED,
             source=",".join(str(x) for x in (pending.get("sources") or ())))
        return result if result is None or isinstance(result, str) else str(result)
    except Exception as exc:  # never raise into the caller
        logger.warning("guarded replay failed: %s", type(exc).__name__)
        return _replay_fail(lang)


def _rebuild_match(
    tool: str,
    text: str,
    tool_name: Optional[str],
    arguments: Optional[Mapping[str, Any]],
    detect_intent: Callable,
    build_fake_match: Callable,
) -> Any:
    """Re-detect from the original text, else rebuild from the tool arguments."""
    if text:
        intent, match = detect_intent(text)
        if intent == tool and match is not None:
            return match
    if tool_name and arguments is not None:
        return build_fake_match(tool_name, dict(arguments))
    return None


def _replay_fail(lang: str) -> str:
    return _REPLAY_FAIL["hinglish" if _is_hinglish(lang) else "english"]