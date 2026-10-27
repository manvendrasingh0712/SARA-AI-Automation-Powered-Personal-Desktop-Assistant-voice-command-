"""
sara.core.security.policy
Decision matrix (tier x origin x taint x mode), argument validation, URL and
sensitive-path checks. Pure functions: no network, no model call, thread-safe.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional
from urllib.parse import unquote, urlsplit

from config import Config
from sara.core.security import policy_rules as rules
from sara.core.security import tiers

try:
    from sara.core.security import taint as _taint
except ImportError:  # taint.py is delivered by another task
    _taint = None

ORIGIN_USER_DIRECT = "user_direct"
ORIGIN_LLM_TOOL = "llm_tool"
ORIGIN_USER_CONFIRMED = "user_confirmed"
ORIGIN_PROACTIVE = "proactive"
ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"

_ORIGINS = frozenset({ORIGIN_USER_DIRECT, ORIGIN_LLM_TOOL, ORIGIN_USER_CONFIRMED, ORIGIN_PROACTIVE})
_MODES = frozenset({"standard", "strict", "off"})
_FAIL_TIER = 2
# (is-tainted, sources) function names: the contract first, then the legacy T5 names.
_TAINT_API = (("is_tainted", "taint_sources"), ("turn_tainted", "turn_sources"))

_TEXT_MAX = 600
_TYPING_MAX = 4000
_TITLE_MAX = 120
_URL_MAX = 2000
_MAX_ARGS = 32
_REPEAT_KEYS = ("count", "times", "repeat", "n")
_BAD_SCHEMES = frozenset(
    {"file", "javascript", "data", "vbscript", "ftp", "mailto", "about", "blob",
     "ws", "wss", "tel", "sms", "gopher"}
)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".localdomain", ".home.arpa")
_NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){0,3}$", re.IGNORECASE)
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_B64_RE = re.compile(r"[A-Za-z0-9+/=_\-]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_URL_TOKEN_RE = re.compile(
    r"^(?:[a-z][a-z0-9+.\-]*://|//|www\.|(?:javascript|data|vbscript|file|mailto|ftp|blob):)",
    re.IGNORECASE,
)
_PATH_START_RE = re.compile(r"^(?:[A-Za-z]:|[\\/])")


@dataclass(frozen=True)
class PolicyResult:
    decision: str
    reason: str
    tier: int
    origin: str
    tainted: bool
    sources: tuple[str, ...]


@dataclass(frozen=True)
class ArgCheck:
    ok: bool
    reason: str = ""
    field: str = ""


_OK = ArgCheck(True)


class _TooBig(Exception):
    """Raised when an argument container exceeds the inspection limit."""


def _safe_tier(tool: Any) -> int:
    try:
        return int(tiers.tier_of(tool))
    except Exception:
        return _FAIL_TIER


def _resolve_mode(mode: Optional[str]) -> str:
    raw = mode if mode is not None else getattr(Config, "SECURITY_MODE", "standard")
    resolved = str(raw).strip().lower()
    return resolved if resolved in _MODES else "standard"


def _taint_value(index: int, default: Any) -> Any:
    if _taint is None:
        return default
    for names in _TAINT_API:
        fn = getattr(_taint, names[index], None)
        if fn is None:
            continue
        try:
            return fn()
        except TypeError:
            continue
    return default


def _sources() -> tuple[str, ...]:
    try:
        return tuple(_taint_value(1, ()))
    except Exception:
        return ()


def _matrix(tier: int, origin: str, tainted: bool, mode: str) -> tuple[str, str]:
    t = max(0, min(3, tier))
    if mode == "off":
        return ALLOW, "mode_off"
    if origin == ORIGIN_USER_CONFIRMED:
        return ALLOW, "user_confirmed_allow"
    if mode == "strict":
        if origin == ORIGIN_USER_DIRECT:
            decision = ALLOW if t <= 1 else CONFIRM
        else:
            decision = ALLOW if t == 0 else (CONFIRM if t == 1 else DENY)
        return decision, f"strict_t{t}_{decision}"
    if origin == ORIGIN_USER_DIRECT:
        return ALLOW, "user_direct_allow"
    if origin == ORIGIN_LLM_TOOL and not tainted:
        decision = ALLOW if t <= 1 else CONFIRM
        return decision, f"llm_t{t}_{decision}"
    decision = ALLOW if t == 0 else (CONFIRM if t == 1 else DENY)
    prefix = "proactive" if origin == ORIGIN_PROACTIVE else "tainted_llm"
    return decision, f"{prefix}_t{t}_{decision}"


def decide(
    tool: str, origin: str, *, tainted: Optional[bool] = None, mode: Optional[str] = None
) -> PolicyResult:
    """Apply the decision matrix; never raises (fail-safe: T0/T1 allow, T2/T3 deny)."""
    known = isinstance(origin, str) and origin in _ORIGINS
    norm_origin = origin if known else ORIGIN_LLM_TOOL
    try:
        tier = int(tiers.tier_of(tool))
        resolved = _resolve_mode(mode)
        is_tainted = bool(_taint_value(0, False)) if tainted is None else bool(tainted)
        decision, reason = _matrix(tier, norm_origin, is_tainted, resolved)
        sources = _sources() if is_tainted else ()
        return PolicyResult(decision, reason, tier, norm_origin, is_tainted, sources)
    except Exception:
        tier = _safe_tier(tool)
        decision = ALLOW if tier < 2 else DENY
        return PolicyResult(decision, "internal_error", tier, norm_origin, bool(tainted), ())


def _exfil_shape(query: str) -> bool:
    for pair in query.split("&")[:50]:
        value = unquote(pair.partition("=")[2])
        if len(value) < 60:
            continue
        if _HEX_RE.fullmatch(value):
            return True
        if _B64_RE.fullmatch(value) and value.count("-") <= len(value) // 12:
            return True
    return False


def _check_url(url: Any, allow_local: bool) -> ArgCheck:
    if not isinstance(url, str):
        return ArgCheck(False, "url_not_string")
    text = url.strip()
    if not text:
        return ArgCheck(False, "url_empty")
    if len(text) > _URL_MAX:
        return ArgCheck(False, "url_too_long")
    if _CONTROL_RE.search(text) or "\t" in text or "\n" in text:
        return ArgCheck(False, "url_control_chars")
    if "\\" in text:
        return ArgCheck(False, "url_backslash")
    if text.startswith("//"):
        return ArgCheck(False, "url_scheme_relative")
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme in _BAD_SCHEMES:
        return ArgCheck(False, f"url_scheme_{scheme}")
    if scheme not in ("http", "https"):
        if "://" in text:
            return ArgCheck(False, "url_scheme_unsupported")
        parts = urlsplit("http://" + text)
    if "@" in parts.netloc:
        return ArgCheck(False, "url_credentials")
    host = unicodedata.normalize("NFKC", parts.hostname or "").lower().rstrip(".")
    if not host:
        return ArgCheck(False, "url_no_host")
    try:
        _ = parts.port
    except ValueError:
        return ArgCheck(False, "url_bad_port")
    if not allow_local:
        if ":" in host or _NUMERIC_HOST.match(host):
            return ArgCheck(False, "url_ip_literal")
        if host == "localhost" or host.endswith(_LOCAL_SUFFIXES) or "." not in host:
            return ArgCheck(False, "url_local_host")
    if _exfil_shape(parts.query):
        return ArgCheck(False, "url_exfil_shape")
    return _OK


def check_url(url: str, *, allow_local: bool = False) -> ArgCheck:
    """Accept only plain http(s) URLs without credentials, local hosts or exfil shapes."""
    try:
        return _check_url(url, allow_local)
    except Exception:
        return ArgCheck(False, "url_error")


def is_sensitive_path(path: "str | os.PathLike") -> bool:
    """True for secrets, SARA data stores and system credential files."""
    return rules.sensitive_path(path)


def _walk(field: str, value: Any, depth: int) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield field, value
    elif depth < 2 and isinstance(value, (Mapping, list, tuple)):
        items = list(value.values() if isinstance(value, Mapping) else value)
        if len(items) > _MAX_ARGS:
            raise _TooBig
        for item in items:
            yield from _walk(field, item, depth + 1)


def _looks_like_path(text: str) -> bool:
    return bool(_PATH_START_RE.match(text)) or "\\" in text or ".." in text


def _generic(value: str, limit: int) -> str:
    if _CONTROL_RE.search(value):
        return "control_chars"
    if len(value) > limit:
        return "too_long"
    text = value.strip()
    if text and not any(ch.isspace() for ch in text) and _URL_TOKEN_RE.match(text):
        result = check_url(text)
        return "" if result.ok else result.reason
    if len(text) <= _TEXT_MAX and "\n" not in value and _looks_like_path(text):
        return "sensitive_path" if rules.sensitive_path(text) else ""
    return ""


def _primary(pairs: list[tuple[str, str]], keys: tuple[str, ...]) -> Optional[tuple[str, str]]:
    for key in keys:
        for field, value in pairs:
            if field == key:
                return field, value
    return pairs[0] if pairs else None


def _repeat_count(args: Mapping[str, Any]) -> int:
    for key in _REPEAT_KEYS:
        raw = args.get(key)
        if raw is None:
            continue
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            continue
    return 1


def _tool_rule(tool: str, pairs: list[tuple[str, str]], args: Mapping[str, Any]) -> ArgCheck:
    if tool in ("typing_text", "press_key"):
        repeat = _repeat_count(args)
        for field, value in pairs:
            if rules.chord_blocked(value, repeat):
                return ArgCheck(False, "blocked_chord", field)
    elif tool in ("close_app", "restart_application"):
        for field, value in pairs:
            if rules.is_critical_process(value):
                return ArgCheck(False, "critical_process", field)
    elif tool in ("stop_service", "start_service"):
        for field, value in pairs:
            if rules.is_protected_service(value):
                return ArgCheck(False, "protected_service", field)
    elif tool in ("open_url", "summarize_url"):
        picked = _primary(pairs, ("url", "g1"))
        if picked is not None:
            result = check_url(picked[1])
            if not result.ok:
                return ArgCheck(False, result.reason, picked[0])
    elif tool == "calendar_create":
        picked = _primary(pairs, ("title", "g1"))
        if picked is not None and len(picked[1]) > _TITLE_MAX:
            return ArgCheck(False, "title_too_long", picked[0])
    elif tool == "find_file":
        for field, value in pairs:
            if rules.sensitive_path(value):
                return ArgCheck(False, "sensitive_path", field)
    return _OK


def _validate(tool: str, args: Optional[Mapping[str, Any]]) -> ArgCheck:
    if not args:
        return _OK
    if not isinstance(args, Mapping):
        return ArgCheck(False, "args_not_mapping")
    if len(args) > _MAX_ARGS:
        return ArgCheck(False, "too_many_args")
    try:
        pairs = [pair for key, value in args.items() for pair in _walk(str(key), value, 0)]
    except _TooBig:
        return ArgCheck(False, "too_many_args")
    limit = _TYPING_MAX if tool == "typing_text" else _TEXT_MAX
    for field, value in pairs:
        reason = _generic(value, limit)
        if reason:
            return ArgCheck(False, reason, field)
    return _tool_rule(tool, pairs, args)


def validate_args(tool: str, args: Optional[Mapping[str, Any]]) -> ArgCheck:
    """Generic and tool-specific argument checks; never raises."""
    try:
        return _validate(tool, args)
    except Exception:
        return ArgCheck(_safe_tier(tool) < 2, "validate_error")