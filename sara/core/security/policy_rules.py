"""
sara.core.security.policy_rules
Static tables, key-chord helpers and path classification used by policy.py.
Pure functions; the only I/O is path normalisation and one cached config lookup.
"""
from __future__ import annotations

import fnmatch
import ntpath
import os
import re
from pathlib import Path
from typing import Any, Iterator, Optional, Tuple

Parts = Tuple[str, ...]

CRITICAL_PROCESSES = frozenset(
    {"csrss", "winlogon", "wininit", "services", "lsass", "smss", "system", "dwm"}
)
PROTECTED_SERVICES = frozenset(
    {
        "windefend", "wdnissvc", "mpssvc", "eventlog", "rpcss", "dcomlaunch", "winmgmt",
        "windows defender", "windows defender antivirus service",
        "microsoft defender antivirus service", "windows defender firewall",
        "windows firewall", "windows event log", "remote procedure call",
        "dcom server process launcher", "windows management instrumentation",
    }
)

_KEY_ALIASES = {
    "windows": "win", "winkey": "win", "super": "win", "meta": "win", "cmd": "win",
    "control": "ctrl", "delete": "del",
}
_WIN_R = frozenset({"win", "r"})
_CTRL_ALT_DEL = frozenset({"ctrl", "alt", "del"})
_ALT_F4 = frozenset({"alt", "f4"})
_CHORD_SPLIT = re.compile(r"[\s,;]+")
_PLUS_SPACES = re.compile(r"\s*\+\s*")

_SECRET_FILE_PATTERNS = (
    "credentials*.json", "token*.json", "client_secret*.json", "*.pem", "*.key",
    "*.pfx", "*.p12", "id_rsa*", "id_ed25519*", "*.kdbx", "*.ppk",
)
_SECRET_DIRS = frozenset({".ssh", ".gnupg", ".aws", ".azure"})
_DB_NAMES = frozenset(
    stem + suffix
    for stem in ("sara_data.db", "telemetry.sqlite", "security.sqlite")
    for suffix in ("", "-wal", "-shm")
)
_BROWSER_MARKERS = frozenset(
    {"chrome", "edge", "brave-browser", "bravesoftware", "firefox", "chromium", "user data"}
)
_BROWSER_FILES = frozenset(
    {
        "login data", "login data for account", "cookies", "local state", "web data",
        "logins.json", "key4.db", "cert9.db", "cookies.sqlite",
    }
)
_SYSTEM_SEQUENCES = (
    ("system32", "config"), ("sysnative", "config"), ("windows", "repair"),
    ("microsoft", "credentials"), ("microsoft", "protect"), ("microsoft", "vault"),
)
_DRIVE_RE = re.compile(r"^[a-z]:$", re.IGNORECASE)

_layout: Optional[Tuple[Parts, Parts]] = None


def process_name(value: str) -> str:
    """Lower-case base name of a process, without a trailing .exe."""
    name = value.strip().strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def is_critical_process(value: str) -> bool:
    """True for Windows processes that must never be closed or restarted."""
    return process_name(value) in CRITICAL_PROCESSES


def is_protected_service(value: str) -> bool:
    """True for security-critical Windows services (by name or display name)."""
    return value.strip().strip("\"'").lower() in PROTECTED_SERVICES


def _chords(value: str) -> Iterator[frozenset]:
    text = _PLUS_SPACES.sub("+", value.lower())
    for raw in _CHORD_SPLIT.split(text):
        keys = frozenset(_KEY_ALIASES.get(k, k) for k in raw.split("+") if k)
        if keys:
            yield keys


def chord_blocked(value: str, repeat: int = 1) -> bool:
    """True when value holds win+r, ctrl+alt+del or a repeated alt+f4."""
    chords = list(_chords(value))
    if any(c == _WIN_R or _CTRL_ALT_DEL <= c for c in chords):
        return True
    alt_f4 = sum(1 for c in chords if c == _ALT_F4)
    return alt_f4 >= 2 or (alt_f4 == 1 and repeat > 1)


def path_parts(path: Any) -> Parts:
    """Normalised, lower-cased path components (env vars, ~, .., \\\\?\\, ADS resolved)."""
    raw = os.fspath(path)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    raw = raw.replace("\x00", "").strip().replace("/", "\\")
    if raw[:8].upper() == "\\\\?\\UNC\\":
        raw = "\\\\" + raw[8:]
    elif raw[:4] in ("\\\\?\\", "\\\\.\\"):
        raw = raw[4:]
    comps = [
        c if (i == 0 and _DRIVE_RE.match(c)) else c.split(":", 1)[0]
        for i, c in enumerate(raw.split("\\"))
    ]
    expanded = os.path.expandvars(os.path.expanduser("\\".join(comps)))
    norm = ntpath.normpath(expanded.replace("/", "\\")).lower()
    parts = (p if p == ".." else p.rstrip(" .") for p in norm.split("\\"))
    return tuple(p for p in parts if p)


def app_data_layout() -> Tuple[Parts, Parts]:
    """(SARA app-data root, notes folder) as normalised parts; empty when unknown."""
    global _layout
    if _layout is None:
        try:
            from config import app_data_subdir

            root = path_parts(Path(app_data_subdir("data")).parent)
            try:
                notes = path_parts(app_data_subdir("notes"))
            except Exception:
                notes = root + ("notes",)
            _layout = (root, notes)
        except Exception:
            return (), ()
    return _layout


def _has_sequence(parts: Parts, seq: Parts) -> bool:
    size = len(seq)
    return any(parts[i:i + size] == seq for i in range(len(parts) - size + 1))


def _in_app_data(parts: Parts) -> bool:
    root, notes = app_data_layout()
    if notes and parts[:len(notes)] == notes:
        return False
    return bool(root) and parts[:len(root)] == root


def is_sensitive_parts(parts: Parts) -> bool:
    """Classify normalised path components."""
    if not parts:
        return False
    name = parts[-1]
    if name == ".env" or name.startswith(".env."):
        return True
    if name in _DB_NAMES or any(fnmatch.fnmatchcase(name, p) for p in _SECRET_FILE_PATTERNS):
        return True
    if _SECRET_DIRS.intersection(parts):
        return True
    if any(_has_sequence(parts, seq) for seq in _SYSTEM_SEQUENCES):
        return True
    if name in _BROWSER_FILES and _BROWSER_MARKERS.intersection(parts[:-1]):
        return True
    return _in_app_data(parts)


def sensitive_path(path: Any) -> bool:
    """True for secrets, SARA data stores and system credential files; fail-safe on error."""
    try:
        return is_sensitive_parts(path_parts(path))
    except Exception:
        try:
            text = os.fspath(path)
            if isinstance(text, bytes):
                text = text.decode("utf-8", "ignore")
        except Exception:
            return True
        return "/" in text or "\\" in text