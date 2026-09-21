"""
sara.tools.system.apps
Launch / restart / close applications by name.

SECURITY MODEL (read this before changing anything below)
---------------------------------------------------------
Everything in this file eventually receives text that came from a person's
voice or keyboard. That text is UNTRUSTED. The rules are:

1. Nothing is ever passed to a shell. There is no `shell=True` anywhere.
   Processes are only started in list form: subprocess.Popen([path], ...).
2. A name is resolved in this order:
       a) exact / normalised match in _APP_ALIASES  (trusted, hard-coded)
       b) a strictly validated "plain program name" (letters, digits,
          space, '.', '_', '+', '-'  — no '&', '|', ';', '>', '<', '^', '%',
          quotes, slashes, colons, '$', '`', parentheses, control chars)
   Anything else is rejected BEFORE any launch is attempted.
3. The last-resort fallback resolves the name with shutil.which() to a real
   .exe/.com file and launches THAT PATH in list form. If it cannot be
   resolved, we simply say "couldn't find/open" — nothing is run.
4. Destructive actions (close / restart) refuse critical Windows system
   processes and never touch SARA's own process tree.
"""
from ._shared import _ensure_windows

import difflib
import logging
import os
import re
import shutil
import time
import subprocess
import platform

from typing import Dict, List, NamedTuple, Optional, Set, Tuple

import psutil

from config import Config

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# PRODUCTION-AUDIT FIX: previously computed as os.path.join(os.getcwd(),
# "sara_notes.txt"), which meant launching the app from a different
# working directory could silently point at a different physical file
# than the one database.py/reminders.py use. Now resolved from a single,
# CWD-independent, project-root-based path defined once in config.py.
_NOTES_FILE = Config.NOTES_FILE_PATH

# FINAL PRODUCTION POLISH: single canonical definition, used by
# get_notes() below to parse each "[timestamp] text" line back out of
# sara_notes.txt. Previously this regex existed in two places — one
# unreachable/dead copy mis-indented inside clear_notes(), and one real
# copy declared AFTER get_notes() (which only worked because Python
# resolves names inside a function body at call time, not definition
# time — fragile and confusing to read). Consolidated here, declared
# before anything references it.
_NOTE_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s?(?P<text>.*)$")


# ============================================================
# APP NAME ALIASES
# ============================================================
_APP_ALIASES: Dict[str, str] = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "browser": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "notepad": "notepad",
    "word": "winword",
    "ms word": "winword",
    "microsoft word": "winword",
    "excel": "excel",
    "ms excel": "excel",
    "microsoft excel": "excel",
    "powerpoint": "powerpnt",
    "ms powerpoint": "powerpnt",
    "microsoft powerpoint": "powerpnt",
    "outlook": "outlook",
    "calculator": "calc",
    "calc": "calc",
    "paint": "mspaint",
    "spotify": "spotify",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "cmd": "cmd",
    "command prompt": "cmd",
    "terminal": "cmd",
    "powershell": "powershell",
    "task manager": "taskmgr",
    "control panel": "control",
    "settings": "ms-settings:",
    "file explorer": "explorer",
    "explorer": "explorer",
    "files": "explorer",
    "photos": "ms-photos:",
    "camera": "microsoft.windows.camera:",
    "snipping tool": "SnippingTool",
    "wordpad": "write",
    "vlc": "vlc",
    "discord": "discord",
    "telegram": "telegram",
    "whatsapp": "whatsapp:",
    "zoom": "zoom",
    "teams": "msteams",
    "microsoft teams": "msteams",
    "steam": "steam",
    "obs": "obs64",
}


# ============================================================
# INTERNAL CONSTANTS
# ============================================================

# Longest app name we are willing to even look at.
_MAX_APP_NAME_LEN = 64

# A "plain program name" (applied to the lower-cased, normalised name).
# Deliberately excludes every shell metacharacter, quotes, path separators,
# colons (URI schemes / drive letters) and control characters.
_SAFE_APP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9 ._+\-]*$")

# Spoken filler we strip so "open the chrome app" behaves like "open chrome".
_FILLER_PREFIXES = ("the ", "my ", "a ")
_FILLER_SUFFIXES = (" application", " app", " program", " software")

# Fuzzy matching (typo / speech-recognition tolerance).
_FUZZY_AUTO_CUTOFF = 0.85     # confident enough to open automatically
_FUZZY_HINT_CUTOFF = 0.60     # good enough to offer "Did you mean ...?"
_FUZZY_MIN_LEN = 4            # never auto-correct very short names

# Only real executables may be launched through the which() fallback.
# (.bat/.cmd are refused on purpose: on Windows they run through cmd.exe.)
_ALLOWED_EXE_EXTS = (".exe", ".com")

# Windows critical / system processes SARA must never terminate.
_PROTECTED_PROCESS_NAMES = frozenset({
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsm.exe",
    "svchost.exe", "dwm.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe",
    "spoolsv.exe", "audiodg.exe", "taskhostw.exe", "runtimebroker.exe",
    "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "securityhealthservice.exe", "msmpeng.exe",
})

# Windows process-creation flags (fall back to raw values if the running
# Python doesn't expose the names). Applications are started DETACHED so
# they keep running even if SARA exits.
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_LAUNCH_CREATION_FLAGS = (_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP) if _IS_WINDOWS else 0

# Windows error: "The requested operation requires elevation".
_ERROR_ELEVATION_REQUIRED = 740

# Launch outcomes.
_LAUNCH_OK = "ok"
_LAUNCH_NOT_FOUND = "not_found"
_LAUNCH_ERROR = "error"


class _Resolved(NamedTuple):
    target: Optional[str]   # what we may launch/close; None = rejected
    source: str             # "alias" | "raw" | "rejected"
    label: str              # safe-to-display version of what the user said


# ============================================================
# NAME HANDLING
# ============================================================

def _display_name(name: str) -> str:
    """Printable, single-line, length-capped version of user text.
    Used in every message and log line so odd input can never garble the
    GUI/logs (or be echoed back as control characters)."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 60:
        cleaned = cleaned[:57] + "..."
    return cleaned


def _normalize_app_name(name: str) -> str:
    """lower-case, collapse whitespace, drop spoken filler and trailing
    punctuation. 'The  Chrome App.' -> 'chrome'."""
    key = re.sub(r"\s+", " ", str(name).strip().lower())
    key = key.rstrip(".,!?").strip()
    for prefix in _FILLER_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    for suffix in _FILLER_SUFFIXES:
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    return key.strip()


def _resolve_app_name(raw_name: str) -> _Resolved:
    """Turn untrusted text into either a trusted alias target, a validated
    plain program name, or a rejection."""
    label = _display_name(raw_name)
    key = _normalize_app_name(raw_name)

    if not key or len(key) > _MAX_APP_NAME_LEN:
        return _Resolved(None, "rejected", label)

    # For anything we accept, speak the cleaned-up name
    # ("The Chrome App." -> "chrome"), not the raw text.
    if _SAFE_APP_NAME_RE.match(key) or key in _APP_ALIASES:
        label = _display_name(key)

    # a) trusted, hard-coded aliases (exact dictionary lookup only).
    alias_target = _APP_ALIASES.get(key)
    if alias_target is not None:
        return _Resolved(alias_target, "alias", label)

    # b) anything else must look like a plain program name.
    if not _SAFE_APP_NAME_RE.match(key):
        return _Resolved(None, "rejected", label)

    return _Resolved(key, "raw", label)


def _fuzzy_alias(key: str, cutoff: float) -> Optional[str]:
    """Closest known alias NAME (dict key) to `key`, or None."""
    if len(key) < _FUZZY_MIN_LEN:
        return None
    matches = difflib.get_close_matches(key, list(_APP_ALIASES), n=1, cutoff=cutoff)
    return matches[0] if matches else None


def _to_process_exe(target: str) -> str:
    return target if target.lower().endswith(".exe") else target + ".exe"


# ============================================================
# SAFE LAUNCH HELPERS (no shell, ever)
# ============================================================

def _dir_is_on_path(directory: str) -> bool:
    wanted = os.path.normcase(os.path.abspath(directory))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry and os.path.normcase(os.path.abspath(entry)) == wanted:
            return True
    return False


def _resolve_executable(target: str) -> Optional[str]:
    """Resolve a plain program name to the absolute path of a real
    .exe/.com file, or None. Never returns anything that could carry
    shell syntax, and never returns a program that only exists in the
    current working directory (CWD-hijack protection)."""
    if not target or ":" in target or "/" in target or "\\" in target:
        return None
    if not _SAFE_APP_NAME_RE.match(target.lower()):
        return None

    has_ext = os.path.splitext(target)[1].lower() in _ALLOWED_EXE_EXTS
    candidates = [target] if has_ext else [target + ".exe", target]

    for candidate in candidates:
        try:
            found = shutil.which(candidate)
        except Exception:
            found = None
        if not found:
            continue

        path = os.path.abspath(found)
        if os.path.splitext(path)[1].lower() not in _ALLOWED_EXE_EXTS:
            continue
        if not os.path.isfile(path):
            continue

        folder = os.path.dirname(path)
        in_cwd = os.path.normcase(folder) == os.path.normcase(os.path.abspath(os.getcwd()))
        if in_cwd and not _dir_is_on_path(folder):
            continue

        return path
    return None


def _spawn_detached(exe_path: str) -> "subprocess.Popen":
    """Start a program from an absolute path: list form, shell disabled,
    detached from SARA, no inherited console handles."""
    folder = os.path.dirname(exe_path)
    return subprocess.Popen(
        [exe_path],
        shell=False,
        cwd=folder if os.path.isdir(folder) else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_LAUNCH_CREATION_FLAGS,
    )


def _try_launch(target: str) -> Tuple[str, Optional[BaseException]]:
    """Attempt to open `target` without a shell.
    Step 1: os.startfile (Windows resolves aliases / App Paths / URIs).
    Step 2: if Windows says 'not found', resolve to a real .exe with
            shutil.which() and start it in list form.
    Returns (status, exception_or_None)."""
    try:
        os.startfile(target)
        return _LAUNCH_OK, None
    except FileNotFoundError:
        pass
    except Exception as e:
        return _LAUNCH_ERROR, e

    exe_path = _resolve_executable(target)
    if exe_path is None:
        return _LAUNCH_NOT_FOUND, None

    try:
        _spawn_detached(exe_path)
        return _LAUNCH_OK, None
    except Exception as e:
        return _LAUNCH_ERROR, e


def _needs_elevation(err: Optional[BaseException]) -> bool:
    return getattr(err, "winerror", None) == _ERROR_ELEVATION_REQUIRED


# ============================================================
# SAFE TERMINATE HELPERS
# ============================================================

def _protected_pids() -> Set[int]:
    """PIDs SARA must never kill: itself, its children (e.g. the GUI
    webview) and its launcher chain (e.g. the terminal it was started
    from) — except explorer.exe, which is a legitimate thing to close."""
    pids: Set[int] = {os.getpid()}
    try:
        me = psutil.Process(os.getpid())
        for child in me.children(recursive=True):
            pids.add(child.pid)
        for parent in me.parents():
            try:
                if parent.name().lower() != "explorer.exe":
                    pids.add(parent.pid)
            except Exception:
                continue
    except Exception:
        pass
    return pids


def _is_protected_process(process_exe: str) -> bool:
    return process_exe.lower() in _PROTECTED_PROCESS_NAMES


class _TerminateResult(NamedTuple):
    procs: List["psutil.Process"]
    exe_path: Optional[str]
    denied: int


def _terminate_matching(process_exe: str) -> _TerminateResult:
    """Terminate every running process whose image name equals
    `process_exe`. Remembers the executable path of the first match so a
    restart can relaunch the exact same install."""
    protected = _protected_pids()
    wanted = process_exe.lower()

    procs: List["psutil.Process"] = []
    exe_path: Optional[str] = None
    denied = 0

    for proc in psutil.process_iter(["name", "exe"]):
        try:
            name = proc.info.get("name")
            if not name or name.lower() != wanted:
                continue
            if proc.pid in protected:
                continue
            if exe_path is None:
                exe_path = proc.info.get("exe") or None
            proc.terminate()
            procs.append(proc)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            denied += 1
            continue

    return _TerminateResult(procs, exe_path, denied)


def _wait_and_kill(procs: List["psutil.Process"], grace: float) -> None:
    """Give terminated processes `grace` seconds to disappear, then
    force-kill any stragglers."""
    if not procs:
        return
    try:
        _gone, alive = psutil.wait_procs(procs, timeout=grace)
        for p in alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if alive:
            psutil.wait_procs(alive, timeout=1.0)
    except Exception as e:
        logger.warning(f"_wait_and_kill: {e}")


# ============================================================
# SYSTEM ACTIONS — apps
# ============================================================


def open_application(app_name: str) -> str:
    _ensure_windows()

    if not app_name or not app_name.strip():
        return "No application name was provided."

    raw_name = app_name.strip()
    resolved = _resolve_app_name(raw_name)
    label = resolved.label

    # Unsafe / malformed name (e.g. "test & calc", "a | b", "x; y"):
    # refuse before anything is launched.
    if resolved.target is None:
        logger.warning(f"open_application rejected invalid app name: {label!r}")
        return f"Sorry, I couldn't find or open '{label}'."

    target = resolved.target
    status, err = _try_launch(target)

    # Not found as typed -> try a confident typo / mishearing correction
    # against the known app list ("crome" -> chrome).
    if status == _LAUNCH_NOT_FOUND and resolved.source == "raw":
        corrected = _fuzzy_alias(target, _FUZZY_AUTO_CUTOFF)
        if corrected:
            fixed_status, fixed_err = _try_launch(_APP_ALIASES[corrected])
            if fixed_status == _LAUNCH_OK:
                if Config.DEBUG_MODE:
                    print(f"[Debug] Auto-corrected '{label}' -> '{corrected}'")
                return f"Opened {corrected}."
            if fixed_status == _LAUNCH_ERROR:
                status, err, label = fixed_status, fixed_err, corrected

    if status == _LAUNCH_OK:
        if Config.DEBUG_MODE:
            print(f"[Debug] Launched application: {target} (spoken: '{label}')")
        return f"Opened {label}."

    if status == _LAUNCH_ERROR:
        logger.error(f"open_application failed for {label!r}: {err}")
        if _needs_elevation(err):
            return f"'{label}' needs administrator permission to run."
        return f"Sorry, I couldn't open '{label}' right now."

    # Not found anywhere. Nothing was executed.
    message = f"Sorry, I couldn't find or open '{label}'."
    if resolved.source == "raw":
        hint = _fuzzy_alias(target, _FUZZY_HINT_CUTOFF)
        if hint:
            message += f" Did you mean '{hint}'?"
    logger.info(f"open_application: no match for {label!r}")
    return message


def restart_application(app_name: str) -> str:
    """
    Terminates all running instances of app_name, then relaunches it from
    the SAME executable path (captured before termination via psutil's
    proc.exe()) rather than guessing via os.startfile(), which only
    resolves names Windows already knows about (PATH / App Paths registry)
    and can silently launch a different install than the one that was
    actually running.
    """
    _ensure_windows()

    if not app_name or not app_name.strip():
        return "No application name was provided."

    raw_name = app_name.strip()
    resolved = _resolve_app_name(raw_name)
    label = resolved.label

    if resolved.target is None:
        logger.warning(f"restart_application rejected invalid app name: {label!r}")
        return f"Sorry, I couldn't find or restart '{label}'."

    target = resolved.target

    if ":" in target:
        return (
            f"'{label}' isn't a running application I can restart "
            f"(it opens a system page, not a process)."
        )

    process_exe = _to_process_exe(target)

    if _is_protected_process(process_exe):
        return f"For safety, I can't restart '{label}' — it's a critical system process."

    try:
        result = _terminate_matching(process_exe)
    except Exception as e:
        logger.error(f"restart_application enumeration failed for '{label}': {e}")
        return f"Sorry, I couldn't restart '{label}' right now."

    if not result.procs:
        if result.denied:
            return (
                f"Sorry, I don't have permission to restart '{label}'. "
                f"It may need administrator rights."
            )
        # Wasn't running -> a restart is just a normal open.
        return open_application(raw_name)

    # Wait for a real exit (instead of a blind sleep), force-kill stragglers.
    _wait_and_kill(result.procs, grace=3.0)
    time.sleep(0.3)  # let file locks / ports release before relaunch

    try:
        if result.exe_path and os.path.isfile(result.exe_path):
            _spawn_detached(result.exe_path)
        else:
            os.startfile(target)
        return f"Restarted {label}."
    except Exception as e:
        logger.error(f"restart_application relaunch failed for '{label}': {e}")
        return f"Closed {label} but couldn't relaunch it automatically."


def close_application(process_name: str) -> str:
    _ensure_windows()

    if not process_name or not process_name.strip():
        return "No process name was provided."

    raw_name = process_name.strip()
    resolved = _resolve_app_name(raw_name)
    label = resolved.label

    if resolved.target is None:
        logger.warning(f"close_application rejected invalid app name: {label!r}")
        return f"Sorry, '{label}' isn't a valid application name."

    target = resolved.target

    # FINAL PRODUCTION POLISH: some _APP_ALIASES values are URI-scheme
    # handlers intended for open_application()'s os.startfile() (e.g.
    # "ms-settings:", "whatsapp:", "microsoft.windows.camera:"), not
    # real running-process names. Blindly appending ".exe" below would
    # have produced an unmatchable target like "ms-settings:.exe" — now
    # caught early with a clear message instead of silently closing
    # nothing.
    if ":" in target:
        return f"'{label}' isn't a running application I can close (it opens a system page, not a process)."

    target_exe = _to_process_exe(target)

    if _is_protected_process(target_exe):
        return f"For safety, I can't close '{label}' — it's a critical system process."

    try:
        result = _terminate_matching(target_exe)
    except Exception as e:
        logger.error(f"close_application failed for '{label}': {e}")
        return f"Sorry, I couldn't close '{label}' right now."

    if result.procs:
        _wait_and_kill(result.procs, grace=2.0)
        return f"Closed {len(result.procs)} instance(s) of {target_exe}."

    if result.denied:
        return (
            f"Sorry, I don't have permission to close '{label}'. "
            f"It may need administrator rights."
        )

    return f"No running process found matching '{target_exe}'."