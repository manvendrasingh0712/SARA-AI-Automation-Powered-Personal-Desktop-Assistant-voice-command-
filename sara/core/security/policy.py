"""
sara.core.security.policy

Decision core for the action dispatcher (wired in by T6b; this module has no
dispatcher imports and no side effects).

    decision = decide("open_url", {"url": "https://example.com"})
    if decision.action == "allow":   run it with decision.args
    if decision.action == "confirm": ask the user first
    if decision.action == "deny":    refuse, speak user_message(decision, lang)

How a decision is made
----------------------
1. Argument validation (validate_args): type/size/control-character checks
   on every argument, plus per-intent checks (URL scheme, app and service
   names, key combos, sensitive file paths). A failure is always DENY.
2. The decision matrix: tier of the intent (tiers.py) x context of the turn
   x SECURITY_MODE.

   context  clean    nothing untrusted was read this turn
            tainted  untrusted content was ingested this turn (taint.py)
            flagged  ...and the detector thought it was an injection

   standard   T0     T1     T2     T3
     clean    allow  allow  allow  allow
     tainted  allow  allow  confirm confirm
     flagged  allow  confirm deny   deny

   strict     T0     T1     T2     T3
     clean    allow  allow  allow  confirm
     tainted  allow  confirm confirm deny
     flagged  allow  deny   deny   deny

   SECURITY_MODE=off allows everything and skips validation.
   Intents missing from tiers.py are T2 (UNKNOWN_TIER).

decide() never raises: any internal error is a DENY ("policy_error").

Argument validation only looks at keys that are present; it does not
require any key to exist, because argument shapes belong to the dispatcher.
"""
from __future__ import annotations

import logging
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from . import taint
from .detector import security_mode
from .tiers import T0, T1, T2, T3, tier_of

logger = logging.getLogger(__name__)

ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"

CLEAN = "clean"
TAINTED = "tainted"
FLAGGED = "flagged"

_A, _C, _D = ALLOW, CONFIRM, DENY

#: MATRIX[mode][context][tier]
MATRIX: Dict[str, Dict[str, Tuple[str, str, str, str]]] = {
    "standard": {
        CLEAN: (_A, _A, _A, _A),
        TAINTED: (_A, _A, _C, _C),
        FLAGGED: (_A, _C, _D, _D),
    },
    "strict": {
        CLEAN: (_A, _A, _A, _C),
        TAINTED: (_A, _C, _C, _D),
        FLAGGED: (_A, _D, _D, _D),
    },
}


# ── result types ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ArgCheck:
    ok: bool
    reason: str = ""
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    intent: str
    tier: int
    action: str
    reason: str
    context: str = CLEAN
    args: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.action == ALLOW

    @property
    def needs_confirmation(self) -> bool:
        return self.action == CONFIRM

    @property
    def denied(self) -> bool:
        return self.action == DENY


# ── generic argument checks ─────────────────────────────────────────────────

_MAX_ARG_CHARS = 4000
_MAX_DEPTH = 4
# C0 controls except tab, newline, carriage return; plus DEL.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _walk(value: Any, depth: int = 0) -> str:
    """Return a violation reason for `value`, or '' when it is fine."""
    if depth > _MAX_DEPTH:
        return "arg_bad_type"
    if value is None or isinstance(value, (bool, int, float)):
        return ""
    if isinstance(value, str):
        if len(value) > _MAX_ARG_CHARS:
            return "arg_too_long"
        if _CONTROL_RE.search(value):
            return "arg_control_chars"
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            problem = _walk(item, depth + 1)
            if problem:
                return problem
        return ""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                return "arg_bad_type"
            problem = _walk(item, depth + 1)
            if problem:
                return problem
        return ""
    return "arg_bad_type"


def _strings(value: Any) -> list:
    """The str values of a scalar-or-list argument."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return []


# ── URLs ────────────────────────────────────────────────────────────────────

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
_URL_INTENTS = frozenset({"open_url", "summarize_url"})
_URL_KEYS = frozenset({"url"})
_MAX_URL_CHARS = 2048
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
_HOST_PORT_RE = re.compile(r"^[A-Za-z0-9.\-]+:\d+(?:[/?#]|$)")


def _check_url(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        return "empty_url"
    if len(stripped) > _MAX_URL_CHARS:
        return "arg_too_long"
    # Browsers ignore whitespace/control characters inside a URL scheme
    # ("java\tscript:"), so look for the scheme with them removed.
    compact = re.sub(r"[\s\x00-\x1f]+", "", stripped)
    match = _SCHEME_RE.match(compact)
    if not match:
        return ""  # no scheme: bare host such as "example.com"
    host_part = match.group(1).lower()
    # "localhost:8080" / "example.com:8443" are host:port, not schemes.
    # Plain words like "javascript:1" are still treated as schemes.
    if _HOST_PORT_RE.match(compact) and ("." in host_part or host_part == "localhost"):
        return ""
    if host_part not in _ALLOWED_URL_SCHEMES:
        return "bad_url_scheme"
    return ""


# ── app / service names ─────────────────────────────────────────────────────

_APP_INTENTS = frozenset(
    {"open_app", "close_app", "restart_application", "switch_to_application"}
)
_APP_KEYS = frozenset({"target", "app", "app_name", "application", "name"})
_APP_BAD_RE = re.compile(r"[&|;<>^`$\"%(){}\[\]\r\n\x00]")
_MAX_APP_CHARS = 100

_SERVICE_INTENTS = frozenset({"start_service", "stop_service"})
_SERVICE_KEYS = frozenset({"service", "service_name", "name", "target"})
_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.\- @]{1,80}$")


# ── key presses ─────────────────────────────────────────────────────────────

_KEY_ALIASES = {
    "windows": "win", "super": "win", "meta": "win", "cmd": "win",
    "lwin": "win", "rwin": "win", "control": "ctrl", "delete": "del",
    "escape": "esc", "return": "enter", "option": "alt",
}
_BLOCKED_COMBOS = (
    frozenset({"alt", "f4"}),            # close_active_window has its own intent
    frozenset({"ctrl", "alt", "del"}),
    frozenset({"ctrl", "shift", "esc"}),  # open_task_manager has its own intent
    frozenset({"ctrl", "esc"}),          # opens the Start menu
)
_PRESS_KEYS = frozenset({"key", "keys", "combo"})


def _check_key_combo(value: str) -> str:
    tokens = {
        _KEY_ALIASES.get(t, t)
        for t in re.split(r"[\s+,]+", value.lower())
        if t
    }
    # Any Windows-key combo (Win, Win+R, Win+X ...) is refused: Start or Run
    # followed by typing_text is a ready-made command-execution chain.
    if "win" in tokens:
        return "blocked_key_combo"
    for combo in _BLOCKED_COMBOS:
        if combo <= tokens:
            return "blocked_key_combo"
    return ""


# ── text-ish arguments ──────────────────────────────────────────────────────

_TEXT_LIMITS = {
    "typing_text": ({"text"}, 1000),
    "calendar_create": ({"title", "summary"}, 200),
}


# ── sensitive paths ─────────────────────────────────────────────────────────

_SENSITIVE_DIRS = frozenset({
    ".ssh", ".aws", ".gnupg", ".kube", ".azure", ".docker",
})
_SENSITIVE_FILES = frozenset({
    ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials.json", "token.json", "client_secret.json", "secrets.json",
    "secrets.yaml", "secrets.yml", "known_hosts", "authorized_keys",
    "ntuser.dat", "logins.json", "key3.db", "key4.db", "cert9.db", "wallet.dat",
})
_ENV_FILE_RE = re.compile(r"^\.env(?:\..+)?$")
_SENSITIVE_SUFFIXES = (
    ".pem", ".key", ".pfx", ".p12", ".kdbx", ".ppk", ".keystore", ".jks",
)
_SENSITIVE_PREFIXES = ("passwords.", "passwords ")
_SENSITIVE_FRAGMENTS = (
    "windows/system32/config",
    "appdata/local/google/chrome/user data",
    "appdata/local/microsoft/edge/user data",
    "appdata/local/bravesoftware",
    "appdata/roaming/mozilla/firefox/profiles",
    "appdata/roaming/microsoft/credentials",
    "appdata/local/microsoft/credentials",
    "appdata/roaming/microsoft/protect",
    "appdata/roaming/microsoft/windows/recent",
    "etc/shadow",
    "etc/sudoers",
    "library/keychains",
)
_PATH_KEYS = frozenset({
    "path", "file", "filename", "file_path", "folder", "directory", "dir",
    "source", "destination", "dest", "target_path",
})
_PATH_INTENT_KEYS = {
    "find_file": frozenset({"query", "name", "pattern", "filename"}),
    "notify_on_file": frozenset({"query", "name", "pattern", "filename"}),
}


def _path_parts(path: str) -> list:
    text = path.strip().strip("\"'").replace("\\", "/")
    if not text:
        return []
    text = posixpath.normpath(text)  # resolves "a/../b" so ".." cannot hide a segment
    parts = []
    for part in text.lower().split("/"):
        part = part.rstrip(". ")  # Windows ignores trailing dots and spaces
        if part:
            parts.append(part)
    return parts


def is_sensitive_path(path: Any) -> Optional[str]:
    """Short label if `path` names a credential/secret location, else None.

    Matches on folder names, file names and well-known profile paths (SSH,
    cloud and GPG keys, .env files, browser profiles, Windows credential
    stores, SARA's own credentials.json/token.json). Paths are normalized
    (slashes, case, "..", trailing dots) before matching. The label never
    contains the path itself.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    parts = _path_parts(path)
    if not parts:
        return None
    for part in parts:
        if part in _SENSITIVE_DIRS:
            return "sensitive_dir"
    base = parts[-1]
    if base in _SENSITIVE_FILES:
        return "sensitive_file"
    if _ENV_FILE_RE.match(base):
        return "env_file"
    if base.endswith(_SENSITIVE_SUFFIXES):
        return "key_file"
    if base.startswith(_SENSITIVE_PREFIXES):
        return "password_file"
    joined = "/" + "/".join(parts) + "/"
    for fragment in _SENSITIVE_FRAGMENTS:
        if "/" + fragment + "/" in joined:
            return "sensitive_location"
    return None


# ── argument validation ─────────────────────────────────────────────────────

def validate_args(intent: str, args: Any = None) -> ArgCheck:
    """Check the arguments of one intent. Returns a NEW dict in ArgCheck.args
    (the input is never modified); on failure ok is False and `reason` is a
    short code (never the offending value)."""
    if args is None:
        return ArgCheck(True, "", {})
    if not isinstance(args, Mapping):
        return ArgCheck(False, "arg_not_object", {})
    clean: Dict[str, Any] = dict(args)

    problem = _walk(clean)
    if problem:
        return ArgCheck(False, problem, {})

    for key, value in clean.items():
        strings = _strings(value)
        if not strings:
            continue

        if intent in _URL_INTENTS and key in _URL_KEYS:
            for text in strings:
                problem = _check_url(text)
                if problem:
                    return ArgCheck(False, problem, {})
            if isinstance(value, str):
                clean[key] = value.strip()

        elif intent in _APP_INTENTS and key in _APP_KEYS:
            for text in strings:
                if len(text) > _MAX_APP_CHARS or _APP_BAD_RE.search(text):
                    return ArgCheck(False, "bad_app_name", {})

        elif intent in _SERVICE_INTENTS and key in _SERVICE_KEYS:
            for text in strings:
                if not _SERVICE_RE.match(text.strip()):
                    return ArgCheck(False, "bad_service_name", {})

        elif intent == "press_key" and key in _PRESS_KEYS:
            if _check_key_combo(" ".join(strings)):
                return ArgCheck(False, "blocked_key_combo", {})

        text_rule = _TEXT_LIMITS.get(intent)
        if text_rule and key in text_rule[0]:
            if any(len(text) > text_rule[1] for text in strings):
                return ArgCheck(False, "arg_too_long", {})

        if key in _PATH_KEYS or key in _PATH_INTENT_KEYS.get(intent, ()):
            for text in strings:
                if is_sensitive_path(text):
                    return ArgCheck(False, "sensitive_path", {})

    return ArgCheck(True, "", clean)


# ── decision ────────────────────────────────────────────────────────────────

def _context(tainted: bool, flagged: bool) -> str:
    if flagged:
        return FLAGGED
    if tainted:
        return TAINTED
    return CLEAN


def decide(
    intent: Any,
    args: Any = None,
    *,
    tainted: Optional[bool] = None,
    flagged: Optional[bool] = None,
    cfg: Any = None,
) -> Decision:
    """Allow, confirm or deny one intent. `tainted`/`flagged` default to the
    current turn's taint record. Never raises."""
    name = intent if isinstance(intent, str) else ""
    try:
        tier = tier_of(name)
        mode = security_mode(cfg)
        if mode == "off":
            safe_args = dict(args) if isinstance(args, Mapping) else {}
            return Decision(name, tier, ALLOW, "security_off", CLEAN, safe_args)

        if tainted is None:
            tainted = taint.turn_tainted()
        if flagged is None:
            flagged = taint.turn_flagged()
        context = _context(bool(tainted), bool(flagged))

        check = validate_args(name, args)
        if not check.ok:
            return Decision(name, tier, DENY, "invalid_args:" + check.reason, context, {})

        action = MATRIX[mode][context][min(max(int(tier), T0), T3)]
        reason = "ok" if (action == ALLOW and context == CLEAN) else f"{context}_t{tier}"
        return Decision(name, tier, action, reason, context, check.args)
    except Exception:  # noqa: BLE001 - fail closed
        logger.exception("[security] policy error for intent=%r; denying", name)
        return Decision(name, T2, DENY, "policy_error", CLEAN, {})


# ── spoken explanations ─────────────────────────────────────────────────────

_MESSAGES = {
    "english": {
        "sensitive_path": "I can't access that location, it holds sensitive data.",
        "blocked_key_combo": "I won't press that key combination.",
        "bad_url_scheme": "I can only open normal web links.",
        "bad_app_name": "That app name doesn't look safe, so I won't use it.",
        "bad_service_name": "That service name doesn't look valid.",
        "invalid_args": "I couldn't safely use the details of that request.",
        "policy_error": "I couldn't safely check that request, so I'm not doing it.",
        "flagged": "I just read something that looked like instructions aimed at me, so I'm not doing that.",
        "tainted_confirm": "That came right after reading outside content. Do you want me to go ahead?",
        "default_deny": "I'm not able to do that right now.",
        "default_confirm": "Do you want me to go ahead with that?",
    },
    "hinglish": {
        "sensitive_path": "Main us location ko access nahi kar sakti, wahan sensitive data hota hai.",
        "blocked_key_combo": "Main wo key combination nahi dabaungi.",
        "bad_url_scheme": "Main sirf normal web links khol sakti hoon.",
        "bad_app_name": "Us app ka naam safe nahi lag raha, isliye main use nahi karungi.",
        "bad_service_name": "Us service ka naam sahi nahi lag raha.",
        "invalid_args": "Is request ki details main safely use nahi kar paayi.",
        "policy_error": "Is request ko main safely check nahi kar paayi, isliye nahi kar rahi.",
        "flagged": "Abhi maine kuch aisa padha jisme mere liye instructions the, isliye main ye nahi karungi.",
        "tainted_confirm": "Ye bahar ke content ko padhne ke turant baad aaya hai. Kya main aage badhoon?",
        "default_deny": "Main abhi ye nahi kar sakti.",
        "default_confirm": "Kya main ye kar doon?",
    },
}


def user_message(decision: Decision, lang: str = "english") -> str:
    """One short sentence to speak for a confirm/deny decision ('' for allow)."""
    if decision.action == ALLOW:
        return ""
    table = _MESSAGES["english" if (lang or "english").lower() == "english" else "hinglish"]
    reason = decision.reason
    if reason.startswith("invalid_args:"):
        return table.get(reason.split(":", 1)[1], table["invalid_args"])
    if reason == "policy_error":
        return table["policy_error"]
    if decision.context == FLAGGED:
        return table["flagged"]
    if decision.action == CONFIRM:
        return table["tainted_confirm"] if decision.context == TAINTED else table["default_confirm"]
    return table["default_deny"]
