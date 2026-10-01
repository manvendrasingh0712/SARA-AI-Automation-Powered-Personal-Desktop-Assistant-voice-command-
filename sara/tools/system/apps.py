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
5. OPEN-ONLY Windows Search fallback: a name that passed rule 2b but is
   NOT on the trusted list (_APP_ALIASES / Config.APP_LAUNCH_ALLOWLIST) is
   not launched by us at all. It is pasted as plain text into the Windows
   Search box (Win+S) and the first result is opened, exactly as a person
   would do by hand. The name is never turned into a process, path or shell
   command, and close / restart never use this path.
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
import threading

from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

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

# ------------------------------------------------------------
# Windows Search fallback for UNLISTED applications (open only)
# ------------------------------------------------------------
# WHY THIS EXISTS: _APP_ALIASES / Config.APP_LAUNCH_ALLOWLIST are the apps
# SARA launches through the trusted direct path. Anything else (Blender,
# Figma, Android Studio, ...) used to be refused outright. open_application()
# now hands such names to Windows Search instead: Win+S, paste the name,
# open the FIRST result. The name only ever travels as pasted plain text,
# after passing _SAFE_APP_NAME_RE. close/restart never use this.

# Processes that host the Windows Search UI. Keys are only sent while one of
# these owns the foreground window, so nothing is ever typed into SARA's GUI
# or some other app if Search fails to open.
_SEARCH_HOST_PROCESSES = frozenset({
    "searchhost.exe", "searchapp.exe", "searchui.exe",
    "startmenuexperiencehost.exe",
})

# Timing (seconds). The first two can be overridden in Config and are clamped.
_SEARCH_FOCUS_TIMEOUT_DEFAULT_S = 2.0   # max wait for Search to take focus
_SEARCH_FOCUS_TIMEOUT_LIMITS_S = (0.5, 5.0)
_SEARCH_SETTLE_DEFAULT_S = 0.8          # wait for results after pasting
_SEARCH_SETTLE_LIMITS_S = (0.2, 3.0)
_SEARCH_POLL_INTERVAL_S = 0.05
_SEARCH_INPUT_READY_S = 0.15            # Search box accepts input
_SEARCH_CLOSE_WAIT_S = 1.5              # Search should close after Enter
_SEARCH_LOCK_WAIT_S = 5.0

# The keyboard is one shared resource: two overlapping searches would
# interleave keystrokes, so they are serialised.
_SEARCH_LOCK = threading.Lock()
_MODIFIER_KEYS = ("ctrl", "shift", "alt", "windows")


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
    except (OSError, ValueError) as e:
        return _LAUNCH_ERROR, e
    except Exception as e:
        logger.exception(
            "os.startfile(%r) raised an unexpected error type "
            "(this may be a bug): %s", target, e
        )
        return _LAUNCH_ERROR, e

    exe_path = _resolve_executable(target)
    if exe_path is None:
        return _LAUNCH_NOT_FOUND, None

    try:
        _spawn_detached(exe_path)
        return _LAUNCH_OK, None
    except (OSError, ValueError) as e:
        return _LAUNCH_ERROR, e
    except Exception as e:
        logger.exception(
            "_spawn_detached(%r) raised an unexpected error type "
            "(this may be a bug): %s", exe_path, e
        )
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
            except psutil.Error:
                continue
            except Exception:
                logger.warning(
                    "_protected_pids: unexpected error type reading a parent "
                    "process name (this may be a bug)", exc_info=True
                )
                continue
    except psutil.Error:
        pass
    except Exception:
        logger.exception(
            "_protected_pids: unexpected error type enumerating SARA's "
            "process tree (this may be a bug)"
        )
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


def _config_seconds(name: str, default: float, limits: Tuple[float, float]) -> float:
    """Timing option from Config, defaulted and clamped to `limits`."""
    try:
        value = float(getattr(Config, name, default))
    except (TypeError, ValueError):
        value = default
    if value != value:  # NaN
        value = default
    low, high = limits
    return max(low, min(high, value))


def _search_fallback_applies(key: str) -> bool:
    """True when `key` (a validated plain name that is not in
    _APP_ALIASES) should go to Windows Search instead of the direct path.
    Names on Config.APP_LAUNCH_ALLOWLIST stay on the trusted direct path;
    so does everything if the installation switched the allowlist off."""
    if not getattr(Config, "APP_UNLISTED_SEARCH_FALLBACK_ENABLED", True):
        return False
    if not getattr(Config, "APP_LAUNCH_ALLOWLIST_ENABLED", True):
        return False
    configured = getattr(Config, "APP_LAUNCH_ALLOWLIST", None) or ()
    trusted = {str(item).strip().lower() for item in configured}
    return key not in trusted


def _foreground_process_name() -> Optional[str]:
    """Lower-cased image name of the process owning the foreground window,
    or None if it cannot be determined."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    try:
        return psutil.Process(pid.value).name().lower()
    except psutil.Error:
        return None


def _wait_for_search_state(visible: bool, timeout_s: float) -> bool:
    """Poll (short, bounded) until the Search UI is / is not foreground."""
    deadline = time.monotonic() + timeout_s
    while True:
        if (_foreground_process_name() in _SEARCH_HOST_PROCESSES) == visible:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_SEARCH_POLL_INTERVAL_S)


def _release_modifiers(keyboard: Any) -> None:
    """Make sure no modifier key is left pressed (stuck-key protection)."""
    for name in _MODIFIER_KEYS:
        try:
            if keyboard.is_pressed(name):
                keyboard.release(name)
        except (ValueError, OSError):
            continue


def _run_search_keys(keyboard: Any) -> bool:
    """Win+S -> (Search focused?) -> Ctrl+A -> Ctrl+V -> Enter.
    The query is already on the clipboard. Ctrl+A makes the paste REPLACE
    any old text; changing the query makes Search pre-select its top result,
    so Enter opens the FIRST result. Returns True only if Search closed
    after Enter (i.e. something was actually opened)."""
    focus_timeout = _config_seconds(
        "WINDOWS_SEARCH_TIMEOUT_S",
        _SEARCH_FOCUS_TIMEOUT_DEFAULT_S,
        _SEARCH_FOCUS_TIMEOUT_LIMITS_S,
    )
    settle = _config_seconds(
        "WINDOWS_SEARCH_SETTLE_S", _SEARCH_SETTLE_DEFAULT_S, _SEARCH_SETTLE_LIMITS_S
    )

    keyboard.send("windows+s")
    if not _wait_for_search_state(True, focus_timeout):
        logger.warning(
            "Windows Search did not take focus (foreground process: %r); "
            "nothing was typed.", _foreground_process_name()
        )
        return False

    time.sleep(_SEARCH_INPUT_READY_S)
    keyboard.send("ctrl+a")
    keyboard.send("ctrl+v")
    time.sleep(settle)

    if _foreground_process_name() not in _SEARCH_HOST_PROCESSES:
        logger.warning("Windows Search lost focus before Enter; aborting.")
        return False

    keyboard.send("enter")
    if _wait_for_search_state(False, _SEARCH_CLOSE_WAIT_S):
        return True
    keyboard.send("esc")  # still open -> nothing matched; close it again
    return False


def _drive_windows_search(query: str) -> bool:
    """Put `query` (already validated plain text) on the clipboard, run the
    Search key sequence, and restore the clipboard. Always releases
    modifier keys and the keyboard lock."""
    try:
        import keyboard
        import pyperclip
    except ImportError:
        logger.error(
            "Windows Search fallback unavailable: the 'keyboard' and "
            "'pyperclip' packages are required."
        )
        return False

    if not _SEARCH_LOCK.acquire(timeout=_SEARCH_LOCK_WAIT_S):
        logger.warning("Windows Search fallback busy; skipping this request.")
        return False

    saved_clip: Optional[str] = None
    try:
        try:
            saved_clip = pyperclip.paste()
        except pyperclip.PyperclipException:
            saved_clip = None
        pyperclip.copy(query)
        _release_modifiers(keyboard)
        return _run_search_keys(keyboard)
    finally:
        _release_modifiers(keyboard)
        if isinstance(saved_clip, str) and saved_clip:
            try:
                pyperclip.copy(saved_clip)
            except pyperclip.PyperclipException:
                pass
        _SEARCH_LOCK.release()


def _open_via_windows_search(query: str, label: str) -> str:
    """User-facing wrapper: never raises, never exposes a traceback."""
    failure = f"Sorry, I couldn't open '{label}'."
    if not _IS_WINDOWS:
        logger.warning("Windows Search fallback requested on a non-Windows platform.")
        return failure
    try:
        opened = _drive_windows_search(query)
    except (OSError, ValueError, RuntimeError) as e:
        logger.error(f"Windows Search fallback failed for {label!r}: {e}")
        return failure
    except Exception as e:
        logger.exception(
            "Windows Search fallback for %r raised an unexpected error type "
            "(this may be a bug): %s", label, e
        )
        return failure

    if opened:
        logger.debug("open_application: opened %r via Windows Search", label)
        return f"Opened {label}."
    logger.info(f"open_application: Windows Search fallback did not open {label!r}")
    return failure


def open_application(app_name: str, *, allow_search_fallback: bool = True) -> str:
    """Open an application by (untrusted) name. Trusted names launch
    directly; other valid plain names use the Windows Search fallback
    unless `allow_search_fallback` is False (restart passes False)."""
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

    # Unlisted-but-safe name -> Windows Search (open only, see top of file).
    if (
        allow_search_fallback
        and resolved.source == "raw"
        and _search_fallback_applies(target)
    ):
        return _open_via_windows_search(target, label)

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
    except (psutil.Error, OSError) as e:
        logger.error(f"restart_application enumeration failed for '{label}': {e}")
        return f"Sorry, I couldn't restart '{label}' right now."
    except Exception as e:
        logger.exception(
            "restart_application enumeration for %r raised an unexpected "
            "error type (this may be a bug): %s", label, e
        )
        return f"Sorry, I couldn't restart '{label}' right now."

    if not result.procs:
        if result.denied:
            return (
                f"Sorry, I don't have permission to restart '{label}'. "
                f"It may need administrator rights."
            )
        # Wasn't running -> a restart is just a normal open.
        return open_application(raw_name, allow_search_fallback=False)

    # Wait for a real exit (instead of a blind sleep), force-kill stragglers.
    _wait_and_kill(result.procs, grace=3.0)
    time.sleep(0.3)  # let file locks / ports release before relaunch

    try:
        if result.exe_path and os.path.isfile(result.exe_path):
            _spawn_detached(result.exe_path)
        else:
            os.startfile(target)
        return f"Restarted {label}."
    except (OSError, ValueError) as e:
        logger.error(f"restart_application relaunch failed for '{label}': {e}")
        return f"Closed {label} but couldn't relaunch it automatically."
    except Exception as e:
        logger.exception(
            "restart_application relaunch for %r raised an unexpected "
            "error type (this may be a bug): %s", label, e
        )
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
    except (psutil.Error, OSError) as e:
        logger.error(f"close_application failed for '{label}': {e}")
        return f"Sorry, I couldn't close '{label}' right now."
    except Exception as e:
        logger.exception(
            "close_application for %r raised an unexpected error type "
            "(this may be a bug): %s", label, e
        )
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


# Background/system processes that are never useful as a "which app did
# you mean" candidate -- excluded from list_open_app_names() regardless
# of _PROTECTED_PROCESS_NAMES (which governs what's safe to KILL, a
# stricter/different concern than what's worth SHOWING the user here).
_BORING_PROCESS_NAMES = frozenset({
    "svchost.exe", "conhost.exe", "dllhost.exe", "runtimebroker.exe",
    "backgroundtaskhost.exe", "wmiprvse.exe", "taskhostw.exe",
    "searchindexer.exe", "searchhost.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "applicationframehost.exe",
    "textinputhost.exe", "ctfmon.exe", "sihost.exe", "fontdrvhost.exe",
    "audiodg.exe", "spoolsv.exe", "dwm.exe", "registry",
    "system idle process", "system", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsm.exe",
})

# Reverse lookup (process/exe target -> the human-friendly alias key that
# maps to it), built once at import time from _APP_ALIASES so
# list_open_app_names() doesn't rescan the dict on every call.
_APP_ALIASES_REVERSE: Dict[str, str] = {}
for _alias_key, _alias_target in _APP_ALIASES.items():
    _APP_ALIASES_REVERSE.setdefault(_alias_target.lower(), _alias_key)


def list_open_app_names(limit: int = 8) -> List[str]:
    """
    Friendly display names of currently-running, user-facing
    applications, most-instances-first -- used to offer real candidates
    ("Chrome ya Spotify?") instead of a generic "which app?" when a
    pronoun/reference can't be resolved. Never raises; returns [] on
    total failure, same defensive contract as _protected_pids().
    """
    try:
        protected = _protected_pids()
        counts: Dict[str, int] = {}
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = proc.info.get("pid")
                name = proc.info.get("name")
                if not name or pid in protected:
                    continue
                lname = name.lower()
                if lname in _BORING_PROCESS_NAMES or _is_protected_process(lname):
                    continue
                alias_key = _APP_ALIASES_REVERSE.get(lname)
                display = alias_key if alias_key is not None else _display_name(
                    name[:-4] if lname.endswith(".exe") else name
                )
                counts[display] = counts.get(display, 0) + 1
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                continue
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        return [name for name, _count in ranked[:limit]]
    except psutil.Error:
        return []
    except Exception:
        logger.exception(
            "list_open_app_names: unexpected error type enumerating running "
            "processes (this may be a bug)"
        )
        return []