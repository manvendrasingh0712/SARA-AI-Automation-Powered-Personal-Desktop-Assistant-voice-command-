"""
sara.core.context_line
One short per-turn note about the PC ("Active window: ...; battery 61 % (charging)") so questions like "kya chal raha
hai" or "battery kitni hai" can be answered without a tool call (audit part 2, O7). Opt-in (CONTEXT_AWARE_ENABLED), cached
for a few seconds, and the window title is dropped when it matches the privacy deny-list. It is added AFTER the history
in the prompt, so the Ollama prefix cache is untouched.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional

_DENY = re.compile(
    r"bank|banking|paypal|upi|paytm|phonepe|gpay|password|passwd|credential|keepass|bitwarden|1password|lastpass|"
    r"authenticator|otp|wallet|credit\s*card|debit\s*card|netbanking|incognito|private\s+browsing|inprivate|vpn|"
    r"sign\s*in|log\s*in|login",
    re.IGNORECASE,
)
_TTL_S = 5.0
_cache: dict = {"at": 0.0, "text": ""}
_lock = threading.Lock()


def redact_title(title: str, extra_deny: str = "") -> str:
    t = (title or "").strip()
    if not t:
        return ""
    if _DENY.search(t) or (extra_deny and re.search(extra_deny, t, re.IGNORECASE)):
        return ""
    return t[:80]


def _active_window_title() -> str:
    try:
        import win32gui

        return win32gui.GetWindowText(win32gui.GetForegroundWindow())
    except Exception:  # noqa: BLE001
        return ""


def _battery() -> Optional[tuple]:
    try:
        import psutil

        b = psutil.sensors_battery()
        return (int(b.percent), bool(b.power_plugged)) if b else None
    except Exception:  # noqa: BLE001
        return None


def build(cfg, *, title_fn: Callable[[], str] = _active_window_title, battery_fn: Callable[[], Optional[tuple]] = _battery,
          now: Callable[[], float] = time.monotonic) -> str:
    """The note, or "" when the feature is off / nothing is known. Never raises."""
    if not getattr(cfg, "CONTEXT_AWARE_ENABLED", False):
        return ""
    with _lock:
        if now() - _cache["at"] < _TTL_S and _cache["text"] is not None:
            return _cache["text"]
    parts = []
    try:
        title = redact_title(title_fn(), str(getattr(cfg, "CONTEXT_DENY_PATTERN", "") or ""))
        if title and not re.fullmatch(r"(?i)sara ai|program manager|task switching", title):
            parts.append(f'active window "{title}"')
    except Exception:  # noqa: BLE001
        pass
    try:
        bat = battery_fn()
        if bat:
            parts.append(f"battery {bat[0]}% ({'charging' if bat[1] else 'on battery'})")
    except Exception:  # noqa: BLE001
        pass
    text = ("What is on the PC right now: " + "; ".join(parts) + ". Use it only if the user asks about it.") if parts else ""
    with _lock:
        _cache.update(at=now(), text=text)
    return text


def reset_cache() -> None:
    with _lock:
        _cache.update(at=0.0, text="")
