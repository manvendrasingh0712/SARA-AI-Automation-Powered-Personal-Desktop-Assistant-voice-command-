"""
sara.tools.system.app_index
An in-memory index of installed apps (audit part 2, O17), so "open <app>" for an app that is not in the built-in alias
table does not have to drive Windows Search (Win+S, paste, Enter: ~2.8 s of fixed waits, and it can mis-fire).

Sources: Start Menu shortcuts (all users + current user), and UWP/Store apps from `Get-StartApps`. Built in a background
thread at start-up and refreshed every REFRESH_S. `resolve()` is a dictionary lookup plus a fuzzy match: well under 5 ms.
Only entries that come from these system-controlled locations are ever launched; the user's words never become a path.
"""
from __future__ import annotations

import difflib
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger("sara.app_index")

REFRESH_S = 900.0
_FUZZY_CUTOFF = 0.86
_SKIP = re.compile(r"uninstall|readme|release notes|license|documentation|help|website|manual|changelog", re.IGNORECASE)


@dataclass(frozen=True)
class AppEntry:
    name: str
    target: str            # path of a .lnk / .exe, or "shell:AppsFolder\<AppUserModelID>"


_lock = threading.Lock()
_entries: Dict[str, AppEntry] = {}
_built_at = 0.0
_started = False


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def start_menu_dirs() -> List[Path]:
    dirs = []
    for env in ("PROGRAMDATA", "APPDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def scan_shortcuts(dirs: Iterable[Path]) -> List[AppEntry]:
    out: List[AppEntry] = []
    for root in dirs:
        if not root.is_dir():
            continue
        for p in root.rglob("*.lnk"):
            if _SKIP.search(p.stem):
                continue
            out.append(AppEntry(p.stem, str(p)))
    return out


def scan_uwp(timeout_s: float = 8.0) -> List[AppEntry]:
    """Store apps via PowerShell Get-StartApps (about 1 s); empty list if PowerShell is unavailable."""
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
            capture_output=True, text=True, timeout=timeout_s, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Get-StartApps failed: %s", exc)
        return []
    out = []
    for line in res.stdout.splitlines():
        name, _, appid = line.partition("|")
        if name.strip() and appid.strip() and not _SKIP.search(name) and "\\" not in appid.strip()[:2]:
            out.append(AppEntry(name.strip(), "shell:AppsFolder\\" + appid.strip()))
    return out


def build(dirs: Optional[Iterable[Path]] = None, include_uwp: bool = True) -> int:
    global _built_at
    found = scan_shortcuts(dirs if dirs is not None else start_menu_dirs())
    if include_uwp:
        found += scan_uwp()
    table: Dict[str, AppEntry] = {}
    for e in found:
        key = _norm(e.name)
        if key and key not in table:       # shortcuts first: they are what the Start Menu shows
            table[key] = e
    with _lock:
        _entries.clear()
        _entries.update(table)
        _built_at = time.monotonic()
    return len(table)


def resolve(name: str) -> Optional[AppEntry]:
    """The installed app that `name` means, or None (exact normalised name, then a unique fuzzy match)."""
    key = _norm(name)
    if not key or len(key) < 3:
        return None
    with _lock:
        hit = _entries.get(key)
        if hit:
            return hit
        keys = list(_entries)
    close = difflib.get_close_matches(key, keys, n=2, cutoff=_FUZZY_CUTOFF)
    if not close:
        # "spotify" should find "Spotify - Music", but not every name that merely contains a short word
        contained = [k for k in keys if key in k.split() and len(key) >= 4]
        close = contained[:2] if len(contained) == 1 else []
    if len(close) == 2 and difflib.SequenceMatcher(None, key, close[0]).ratio() - difflib.SequenceMatcher(None, key, close[1]).ratio() < 0.05:
        return None            # two equally good candidates: do not guess
    with _lock:
        return _entries.get(close[0]) if close else None


def launch(entry: AppEntry) -> bool:
    try:
        os.startfile(entry.target)  # noqa: S606 -- target comes from the Start Menu / Get-StartApps, never from the user's text
        return True
    except OSError as exc:
        logger.warning("could not launch %s: %s", entry.name, exc)
        return False


def size() -> int:
    with _lock:
        return len(_entries)


def start_background_refresh() -> bool:
    """Build now (in a thread) and refresh every REFRESH_S. Idempotent."""
    global _started
    with _lock:
        if _started:
            return False
        _started = True

    def _loop() -> None:
        while True:
            try:
                n = build()
                logger.info("[AppIndex] %d apps indexed", n)
            except Exception as exc:  # noqa: BLE001
                logger.debug("app index build failed: %s", exc)
            time.sleep(REFRESH_S)

    threading.Thread(target=_loop, daemon=True, name="app-index").start()
    return True
