"""
sara.core.security.guard
Single decision point for every tool dispatch: argument validation, the
policy matrix, confirmation pendings and audit events. Never raises and never
imports the dispatcher. Spoken messages are fixed templates (no outside text).
"""
from __future__ import annotations

import logging
import copy
import re
import secrets
import threading
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
_APPROVAL_LOCK = threading.Lock()
_APPROVALS: dict[str, tuple[dict[str, Any], float]] = {}
_APPROVALS_MAX = 256


def _registered_tool(tool: Any) -> bool:
    """Only names in the reviewed tier registry may enter an execution path."""
    try:
        return isinstance(tool, str) and tool in _tiers.TIERS
    except Exception:
        return False


def _approval_payload(pending: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(pending.get(key))
        for key in (
            "action", "target", "tool", "origin", "text", "tool_name",
            "arguments", "sources", "expires_at",
        )
    }


def _issue_approval(pending: dict[str, Any]) -> str:
    token = secrets.token_urlsafe(24)
    with _APPROVAL_LOCK:
        now = time.time()
        for old, (_, expires_at) in list(_APPROVALS.items()):
            if expires_at <= now:
                _APPROVALS.pop(old, None)
        if len(_APPROVALS) >= _APPROVALS_MAX:
            oldest = min(_APPROVALS, key=lambda key: _APPROVALS[key][1])
            _APPROVALS.pop(oldest, None)
        _APPROVALS[token] = (_approval_payload(pending), float(pending["expires_at"]))
    return token


def cancel_pending(pending: Any) -> None:
    """Invalidate an unconsumed approval when its confirmation is cancelled."""
    try:
        token = pending.get("approval_id") if isinstance(pending, Mapping) else None
        if isinstance(token, str):
            with _APPROVAL_LOCK:
                _APPROVALS.pop(token, None)
    except Exception:
        return


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


def _confirm_message(
    tool: str, sources: tuple[str, ...], lang: str, tier: int,
    arguments: Optional[Mapping[str, Any]] = None,
) -> str:
    hi = _is_hinglish(lang)
    name = _tool_words(tool)
    risk = _risk_label(tier)
    summary = _argument_summary(arguments)
    details = f" ({risk}, tier {tier}{'; ' + summary if summary else ''})"
    if not sources:
        if hi:
            return f"{name}{details} -- kya main aage badhun? Yes bolo ya cancel."
        return f"{name}{details} -- shall I go ahead? Say yes or cancel."
    if hi:
        return f"{name}{details} -- ye request {_label(sources, lang)} se aayi hai. Yes bolo ya cancel."
    return f"{name}{details} -- that request came from {_label(sources, lang)}. Say yes or cancel."


def _risk_label(tier: int) -> str:
    return {
        0: "read-only",
        1: "low-risk and reversible",
        2: "impactful and may be hard to undo",
        3: "destructive or irreversible",
    }.get(tier, "unknown risk")


def _argument_summary(arguments: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(arguments, Mapping):
        return ""
    parts = []
    for key, value in arguments.items():
        label = str(key)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", label):
            continue
        if any(word in label.lower() for word in ("password", "secret", "token", "credential", "api_key")):
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            text = " ".join(str(value).split())[:64]
            if text:
                parts.append(f"{label}={text!r}")
        if len(parts) >= 3:
            break
    return ", ".join(parts)


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
    make_pending: bool = True,
) -> GuardOutcome:
    """Decide whether a tool call runs, needs a yes, or is denied; never raises."""
    try:
        if not _registered_tool(tool):
            tier = _FAIL_TIER
            _log("blocked", tool=str(tool or "")[:80], tier=tier,
                 origin=str(origin or "")[:40], details="unknown_tool")
            return _outcome(ACTION_DENY, "unknown_tool", tier,
                            "I couldn't verify that action, so I did not run it.")
        if policy is None or events is None:
            return _failsafe(tool, lang)
        call_args = dict(args) if isinstance(args, Mapping) else (
            args if args is not None else _args_from_match(match)
        )
        check = policy.validate_args(tool, call_args)
        if not check.ok:
            tier = _tier(tool)
            _log("arg_rejected", tool=tool, tier=tier, origin=origin, details=str(check.field)[:60])
            return _outcome(ACTION_DENY, "arg_rejected", tier, _deny_message((), lang))
        if _mode_off():
            return _outcome(ACTION_RUN, "mode_off", _tier(tool))
        result = policy.decide(tool, origin)
        sources = tuple(result.sources)
        source_kind = ",".join(sources)
        if result.decision == policy.ALLOW:
            return _outcome(ACTION_RUN, result.reason, result.tier)
        if result.decision == policy.CONFIRM:
            if not make_pending:
                _log("blocked", tool=tool, tier=result.tier, origin=origin,
                     source=source_kind, details="approval_required_no_interactive_channel")
                details = _argument_summary(call_args)
                scope = f"; {details}" if details else ""
                return _outcome(
                    ACTION_CONFIRM, "approval_required", result.tier,
                    f"{_tool_words(tool)} ({_risk_label(result.tier)}, tier {result.tier}{scope}) "
                    "requires separate approval; this routine step was skipped.",
                )
            ttl = float(ttl_s)
            if not 0 < ttl <= 300:
                ttl = 60.0
            pending = {
                "action": PENDING_ACTION, "target": tool, "tool": tool, "origin": origin,
                "text": text, "tool_name": tool_name,
                "arguments": copy.deepcopy(dict(call_args)),
                "sources": list(sources), "expires_at": time.time() + ttl,
            }
            pending["approval_id"] = _issue_approval(pending)
            _log("confirm_asked", tool=tool, tier=result.tier, origin=origin, source=source_kind)
            return _outcome(ACTION_CONFIRM, result.reason, result.tier,
                            _confirm_message(tool, sources, lang, result.tier, call_args), pending)
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
    cancel_event: Optional[threading.Event] = None,
) -> Optional[str]:
    """Run a call the user just confirmed; None when it cannot be replayed."""
    try:
        if cancel_event is not None and cancel_event.is_set():
            cancel_pending(pending)
            return None
        if not isinstance(pending, Mapping) or pending.get("action") != PENDING_ACTION:
            return None
        tool = str(pending.get("tool") or pending.get("target") or "")
        if (
            not tool or pending.get("target") != tool or not _registered_tool(tool)
            or pending.get("origin") not in ("llm_tool", "proactive", "user_direct")
        ):
            cancel_pending(pending)
            return None
        try:
            expires_at = float(pending.get("expires_at"))
        except (TypeError, ValueError, OverflowError):
            return None
        if not expires_at > time.time():
            cancel_pending(pending)
            return None
        token = pending.get("approval_id")
        if not isinstance(token, str) or not token:
            return None
        snapshot = _approval_payload(pending)
        with _APPROVAL_LOCK:
            issued = _APPROVALS.get(token)
            if issued is None or issued[1] <= time.time() or issued[0] != snapshot:
                _APPROVALS.pop(token, None)
                return None
            # Consume before invoking application code: a retry or duplicate
            # confirmation event can never execute this approval twice.
            _APPROVALS.pop(token, None)
        arguments = pending.get("arguments")
        text = str(pending.get("text") or "")
        if not tool or policy is None or events is None:
            return None
        handler = None
        match = None
        if tool not in simple_actions:
            handler = handlers.get(tool)
            tool_name = pending.get("tool_name")
            if arguments is not None and tool_name:
                from sara.core.tool_router import TOOL_NAME_TO_INTENT

                if TOOL_NAME_TO_INTENT.get(tool_name) != tool:
                    return None
                match = build_fake_match(tool_name, copy.deepcopy(arguments))
            else:
                match = _rebuild_match(
                    tool, text, tool_name, arguments, detect_intent, build_fake_match
                )
                if match is not None and isinstance(arguments, Mapping):
                    if _args_from_match(match) != dict(arguments):
                        return None
            if handler is None or match is None:
                return None
        call_args = dict(arguments) if arguments is not None else _args_from_match(match)
        check = policy.validate_args(tool, call_args)
        if not check.ok:
            _log("arg_rejected", tool=tool, tier=_tier(tool), origin=ORIGIN_CONFIRMED,
                 details=str(check.field)[:60])
            return _replay_fail(lang)
        if cancel_event is not None and cancel_event.is_set():
            return None
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
