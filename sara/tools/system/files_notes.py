"""
sara.tools.system.files_notes
File search, recycle bin, and the plain-text quick-notes feature.
"""
from ._shared import _ensure_windows
from .apps import _try_launch, _LAUNCH_OK, _LAUNCH_ERROR, _needs_elevation

import ctypes
import difflib
import logging
import os
import re
import platform
import time
import uuid

from datetime import datetime


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

# ── To-Do list (NEW, separate feature) ──────────────────────────────
# Structured to-do items -- distinct from the plain-text notes feature
# above (different file, different format, different behavior). Each
# item is stored as its own line:
#   [<id>][<timestamp>][<status>] <text>
# where <id> is a short, stable, randomly-generated hex id (NOT a line
# number -- unlike get_notes()'s note ids above, a to-do's id must stay
# valid after other items are deleted, so it can never be derived from
# position in the file) and <status> is "x" (done) or " " (not done).
# Same resolved-from-config.py convention as _NOTES_FILE above -- see
# Config.TODO_FILE_PATH.
_TODO_FILE = Config.TODO_FILE_PATH
_TODO_LINE_RE = re.compile(
    r"^\[(?P<id>[^\]]+)\]\[(?P<ts>[^\]]+)\]\[(?P<status>[ x])\]\s?(?P<text>.*)$"
)

# Fuzzy-match confidence threshold complete_todo()/delete_todo() require
# before acting on a text match (as opposed to an exact id match) --
# same difflib.SequenceMatcher + substring-bonus convention, and the
# same "getattr with a sane default" defensiveness, as
# intent_handlers.py's _MEMORY_FORGET_MATCH_THRESHOLD. Below this,
# neither function guesses -- they ask the user to be more specific
# instead, so a vague reference can never delete/complete the wrong
# to-do.
_TODO_MATCH_THRESHOLD = getattr(Config, "TODO_MATCH_THRESHOLD", 0.45)




# ============================================================
# FILE OPERATIONS
# ============================================================


def _locate_files(name: str):
    """
    Shared directory-walk search used by BOTH find_file() below and the
    new find_and_open_file() -- the common-folders walk, 5-match cap,
    and 3-second timeout guard live here ONCE so the two public
    functions can never drift out of sync with each other. `name` is
    expected to already be stripped/lower-cased by the caller.

    Searches Downloads, Documents, Desktop, Pictures, Music, Videos,
    and the user's home folder, recursively, via os.walk().

    BUG FIX (dedup): `home` is walked LAST but its recursive os.walk()
    also covers Downloads/Documents/Desktop/Pictures/Music/Videos,
    since they're all subfolders of home -- so a single physical file
    used to get counted (and returned) twice: once from its specific
    folder's scan, once again from the home scan. That silently doubled
    the "Found N file(s)" count and could make a genuinely unambiguous
    file look ambiguous. Now deduped by normalised absolute path as
    results are collected, so each real file is only ever counted once
    -- this matters more now than it used to, since
    find_and_open_file() relies on "exactly one match" to decide it's
    safe to open.

    Returns:
        (found, timed_out) -- `found` is a list of up to 5 absolute
        paths (deduplicated, first-seen order), `timed_out` is True if
        the 3-second budget was hit before the walk finished.
    """
    home = os.path.expanduser("~")
    search_dirs = [
        os.path.join(home, "Downloads"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Pictures"),
        os.path.join(home, "Music"),
        os.path.join(home, "Videos"),
        home,
    ]

    found = []
    seen = set()  # normalised absolute paths already counted (dedup fix)
    start_time = time.monotonic()
    timed_out = False
    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            # Don't recurse into hidden/system dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if name in f.lower():
                    full_path = os.path.join(root, f)
                    key = os.path.normcase(os.path.abspath(full_path))
                    if key not in seen:
                        seen.add(key)
                        found.append(full_path)
                if time.monotonic() - start_time > 3.0:
                    timed_out = True
                    break
            if len(found) >= 5 or timed_out:
                break
            if time.monotonic() - start_time > 3.0:
                timed_out = True
                break
        if len(found) >= 5 or timed_out:
            break

    return found, timed_out


def find_file(name: str) -> str:
    """
    Searches common user directories for a file with the given name.
    Searches Downloads, Documents, Desktop, Pictures, Music, Videos,
    and the user's home folder. All directories are scanned recursively
    via os.walk(), guarded by a 3-second timeout — if the search takes
    longer than 3 seconds, it stops early and returns whatever matches
    have been found so far (or a "no results within the time limit"
    message if none have been found yet).

    Args:
        name: Filename or partial filename to search for.

    Returns:
        Path to the file if found, or a helpful not-found message.
    """
    if not name or not name.strip():
        return "Please tell me the name of the file to find."

    name = name.strip().lower()
    try:
        found, timed_out = _locate_files(name)

        if not found:
            if timed_out:
                return "No results found within the time limit."
            return f"Could not find any file matching '{name}' in your common folders."

        if len(found) == 1:
            return f"Found it: {found[0]}"

        results = "; ".join(found[:5])
        return f"Found {len(found)} file(s) matching '{name}': {results}"

    except OSError as e:
        logger.error(f"find_file failed: {e}")
        return "Sorry, I couldn't complete the file search right now."
    except Exception as e:
        logger.exception(
            "find_file hit an unexpected error type (this may be a bug): %s", e
        )
        return "Sorry, I couldn't complete the file search right now."


def find_and_open_file(name: str) -> str:
    """
    Same search as find_file() above (identical common-folders walk via
    the shared _locate_files() helper, including its dedup and
    3-second timeout behavior), but when the search narrows to exactly
    ONE unambiguous match, actually opens it -- via apps.py's
    _try_launch(), the SAME no-shell, os.startfile()-then-
    shutil.which() launch mechanism open_application() already uses,
    so this introduces no second way of opening things in this
    codebase -- instead of just reading the path back.

    Zero matches or 2+ matches: behaves EXACTLY like find_file() above,
    on purpose -- an ambiguous result is never auto-opened. The user
    has to be more specific and try again.

    Args:
        name: Filename or partial filename to find and open.

    Returns:
        A confirmation once the single match is opened, the same
        "found N matches"/"no matches" messages find_file() already
        produces when the result isn't a single unambiguous match, or
        a launch-failure message if the one match was found but
        couldn't be opened.
    """
    if not name or not name.strip():
        return "Please tell me the name of the file to open."

    query = name.strip().lower()
    try:
        found, timed_out = _locate_files(query)
    except OSError as e:
        logger.error(f"find_and_open_file failed: {e}")
        return "Sorry, I couldn't complete the file search right now."
    except Exception as e:
        logger.exception(
            "find_and_open_file hit an unexpected error type "
            "(this may be a bug): %s", e
        )
        return "Sorry, I couldn't complete the file search right now."

    if not found:
        if timed_out:
            return "No results found within the time limit."
        return f"Could not find any file matching '{query}' in your common folders."

    if len(found) > 1:
        # AMBIGUOUS -- never guess which one the user meant. Same
        # "found N matches" listing find_file() produces today, so the
        # user can be more specific instead of a random one opening.
        results = "; ".join(found[:5])
        return f"Found {len(found)} file(s) matching '{query}': {results}"

    # Exactly one match -- safe to open. Reuses apps.py's _try_launch()
    # verbatim rather than introducing a second launch path: step 1 is
    # os.startfile() (works directly on a full file path, launching
    # whatever app is associated with it), step 2 is the
    # shutil.which() fallback apps.py already has for plain program
    # names (a harmless no-op here, since a full path never matches
    # that fallback's "plain program name" shape -- it just degrades
    # to "not found").
    path = found[0]
    status, err = _try_launch(path)

    if status == _LAUNCH_OK:
        return f"Opened {os.path.basename(path)}."

    if status == _LAUNCH_ERROR:
        logger.error(f"find_and_open_file failed to launch {path!r}: {err}")
        if _needs_elevation(err):
            return f"'{os.path.basename(path)}' needs administrator permission to open."
        return f"Found it, but couldn't open '{os.path.basename(path)}': {path}"

    # _LAUNCH_NOT_FOUND: shouldn't happen for a path _locate_files()
    # just verified exists on disk, but degrade to the same
    # "Found it: <path>" find_file() would have said rather than
    # silently claiming success.
    return f"Found it: {path}"


def empty_recycle_bin() -> str:
    """Empties the Windows Recycle Bin silently (no confirmation dialog)."""
    _ensure_windows()
    try:
        # SHERB_NOCONFIRMATION=0x1, SHERB_NOPROGRESSUI=0x2, SHERB_NOSOUND=0x4
        flags = 0x1 | 0x2 | 0x4
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        if result == 0 or result == -2147418113:  # S_OK or already empty
            return "Recycle Bin has been emptied."
        return f"Recycle Bin emptied (code {result})."
    except (OSError, AttributeError, ctypes.ArgumentError) as e:
        logger.error(f"empty_recycle_bin failed: {e}")
        return "Sorry, I couldn't empty the Recycle Bin right now."
    except Exception as e:
        logger.exception(
            "empty_recycle_bin hit an unexpected error type "
            "(this may be a bug): %s", e
        )
        return "Sorry, I couldn't empty the Recycle Bin right now."


# ============================================================
# NOTES
# ============================================================


def take_note(text: str, return_id: bool = False):
    """
    Appends a timestamped note to sara_notes.txt.

    Args:
        text: note content.
        return_id: when False (default — unchanged from before), returns
            the plain confirmation string exactly like before, so every
            EXISTING caller of take_note() (voice commands, gui_main.py,
            etc.) keeps working with zero changes. When True, returns a
            dict {"message": str, "id": int|None} instead, where `id` is
            a stable 0-indexed line number that the desktop UI's Quick
            Notes backend-sync uses to avoid re-importing the same note
            twice (see gui/app.py: Api.save_note).
    """
    if not text or not text.strip():
        msg = "Nothing to note down."
        return {"message": msg, "id": None} if return_id else msg
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(_NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {text.strip()}\n")
        message = f"Got it. I've noted: {text.strip()}"
        if not return_id:
            return message
        # id = 0-indexed line number of the note we just appended. Safe
        # to compute by re-reading the file (notes are only ever
        # appended, never edited in place, so this is cheap and rare —
        # note-taking isn't a high-frequency action).
        try:
            with open(_NOTES_FILE, "r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f if _.strip())
            note_id = line_count - 1
        except (OSError, UnicodeDecodeError):
            note_id = None
        except Exception:
            logger.warning(
                "take_note: unexpected error type while counting note lines "
                "(this may be a bug); returning id=None",
                exc_info=True,
            )
            note_id = None
        return {"message": message, "id": note_id}
    except (OSError, UnicodeEncodeError) as e:
        logger.error(f"take_note failed: {e}")
        err = "Sorry, I couldn't save that note right now."
        return {"message": err, "id": None} if return_id else err
    except Exception as e:
        logger.exception(
            "take_note hit an unexpected error type (this may be a bug): %s", e
        )
        err = "Sorry, I couldn't save that note right now."
        return {"message": err, "id": None} if return_id else err


def read_notes() -> str:
    """Reads all saved notes from sara_notes.txt."""
    try:
        if not os.path.exists(_NOTES_FILE):
            return "You have no saved notes."
        with open(_NOTES_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return "Your notes are empty."
        lines = content.split("\n")
        if len(lines) == 1:
            return f"You have one note: {lines[0]}"
        return f"You have {len(lines)} notes: " + ". ".join(lines)
    except (OSError, UnicodeDecodeError) as e:
        logger.error(f"read_notes failed: {e}")
        return "Sorry, I couldn't read your notes right now."
    except Exception as e:
        logger.exception(
            "read_notes hit an unexpected error type (this may be a bug): %s", e
        )
        return "Sorry, I couldn't read your notes right now."


def clear_notes() -> str:
    """Deletes all saved notes."""
    try:
        if not os.path.exists(_NOTES_FILE):
            return "You have no notes to clear."
        os.remove(_NOTES_FILE)
        return "All your notes have been cleared."
    except OSError as e:
        logger.error(f"clear_notes failed: {e}")
        return "Sorry, I couldn't clear your notes right now."
    except Exception as e:
        logger.exception(
            "clear_notes hit an unexpected error type (this may be a bug): %s", e
        )
        return "Sorry, I couldn't clear your notes right now."


def get_notes() -> list:
    """
    Returns all saved notes as a list of {"id","text","timestamp"} dicts
    (oldest first, same order as the file), for the desktop UI's Quick
    Notes backend sync (see gui/app.py: Api.get_notes).

    `id` is the 0-indexed line number within sara_notes.txt. This is
    stable as long as notes are only ever appended and never
    individually edited/reordered/deleted — which matches the current
    behavior exactly (the only bulk operation is clear_notes(), which
    wipes the whole file, and the frontend already re-syncs cleanly
    from an empty list in that case).
    """
    try:
        if not os.path.exists(_NOTES_FILE):
            return []
        with open(_NOTES_FILE, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]
        result = []
        for idx, line in enumerate(lines):
            m = _NOTE_LINE_RE.match(line)
            if m:
                result.append(
                    {
                        "id": idx,
                        "text": m.group("text"),
                        "timestamp": m.group("ts"),
                    }
                )
            else:
                # Malformed/legacy line with no "[timestamp]" prefix —
                # still surface it rather than silently dropping a saved
                # note the user actually wrote.
                result.append({"id": idx, "text": line, "timestamp": ""})
        return result
    except (OSError, UnicodeDecodeError) as e:
        logger.error(f"get_notes failed: {e}")
        return []
    except Exception as e:
        logger.exception(
            "get_notes hit an unexpected error type (this may be a bug): %s", e
        )
        return []


# ============================================================
# TO-DO LIST (NEW, separate feature -- see _TODO_LINE_RE above)
# ============================================================


def _read_todos() -> list:
    """
    Reads and parses every to-do line from Config.TODO_FILE_PATH into
    {"id","timestamp","done","text"} dicts, via _TODO_LINE_RE. Mirrors
    get_notes()'s "file may not exist yet" / "skip anything that
    doesn't parse" handling above, but a malformed/legacy line is
    silently DROPPED here rather than surfaced with a blank id (unlike
    get_notes()) -- a to-do with no parseable id could never be
    targeted by complete_todo()/delete_todo() anyway, so keeping it
    around would just be dead weight in list_todos() output.
    """
    if not os.path.exists(_TODO_FILE):
        return []
    try:
        with open(_TODO_FILE, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]
    except (OSError, UnicodeDecodeError) as e:
        logger.error(f"_read_todos failed to read file: {e}")
        return []
    except Exception as e:
        logger.exception(
            "_read_todos hit an unexpected error type (this may be a bug): %s", e
        )
        return []

    todos = []
    for line in lines:
        m = _TODO_LINE_RE.match(line)
        if m:
            todos.append(
                {
                    "id": m.group("id"),
                    "timestamp": m.group("ts"),
                    "done": m.group("status") == "x",
                    "text": m.group("text"),
                }
            )
    return todos


def _write_todos(todos: list) -> bool:
    """
    Rewrites Config.TODO_FILE_PATH from scratch with `todos`, one line
    per item in _TODO_LINE_RE's shape. Unlike take_note()'s pure-append
    model, a to-do's done-flag must be flip-able and an item must be
    removable, so complete_todo()/delete_todo() both read the full
    list, mutate/filter it in memory, and call this to persist the
    result -- there's no way to update a single line in place without
    rewriting the file.

    KNOWN LIMITATION: like every other file-backed store in this
    module (notes included), there's no file locking -- two concurrent
    writes (e.g. two commands racing) could clobber each other. Not
    addressed here since it matches this module's existing convention
    (take_note()/clear_notes() have the same gap) and a single-user
    voice assistant makes it very unlikely in practice.
    """
    try:
        with open(_TODO_FILE, "w", encoding="utf-8") as f:
            for todo in todos:
                status = "x" if todo["done"] else " "
                f.write(f"[{todo['id']}][{todo['timestamp']}][{status}] {todo['text']}\n")
        return True
    except (OSError, UnicodeEncodeError) as e:
        logger.error(f"_write_todos failed: {e}")
        return False
    except Exception as e:
        logger.exception(
            "_write_todos hit an unexpected error type (this may be a bug): %s", e
        )
        return False


def _resolve_todo_target(identifier: str, todos: list) -> "dict | None":
    """
    Resolves `identifier` (as spoken/typed) to a single todo dict from
    `todos` -- an exact (case-insensitive) id match first, then a
    fuzzy/substring match against each item's text, same
    difflib.SequenceMatcher-plus-substring-bonus convention as
    intent_handlers.py's _best_fuzzy_memory_match() (see that
    function's docstring for the full rationale). Returns None on no
    confident match so complete_todo()/delete_todo() can ask the user
    to be more specific instead of ever guessing and acting on the
    wrong item.
    """
    if not identifier or not todos:
        return None
    normalized = identifier.strip().lower()

    for todo in todos:
        if todo["id"].lower() == normalized:
            return todo

    best = None
    best_score = 0.0
    for todo in todos:
        text = todo["text"].lower()
        ratio = difflib.SequenceMatcher(None, normalized, text).ratio()
        if normalized in text:
            ratio = max(ratio, 0.8)
        if ratio > best_score:
            best_score = ratio
            best = todo
    if best is None or best_score < _TODO_MATCH_THRESHOLD:
        return None
    return best


def add_todo(text: str) -> str:
    """
    Appends a new, not-done item to the to-do list.

    Args:
        text: the to-do's text.

    Returns:
        A spoken confirmation, or a clarifying question if `text` is
        empty.
    """
    if not text or not text.strip():
        return "What would you like me to add to your to-do list?"
    clean_text = text.strip()
    try:
        # Defensive uniqueness check: a 6-hex-char id (~16M possible
        # values) makes a collision astronomically unlikely for a
        # personal to-do list, but checking is cheap and the
        # consequence of a real collision -- complete_todo()/
        # delete_todo() acting on the wrong item -- is bad enough to
        # guard against anyway.
        existing_ids = {t["id"] for t in _read_todos()}
        todo_id = uuid.uuid4().hex[:6]
        while todo_id in existing_ids:
            todo_id = uuid.uuid4().hex[:6]

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(_TODO_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{todo_id}][{timestamp}][ ] {clean_text}\n")
        return f"Added to your to-do list: {clean_text}"
    except (OSError, UnicodeEncodeError) as e:
        logger.error(f"add_todo failed: {e}")
        return "Sorry, I couldn't save that to-do right now."
    except Exception as e:
        logger.exception(
            "add_todo hit an unexpected error type (this may be a bug): %s", e
        )
        return "Sorry, I couldn't save that to-do right now."


def list_todos(pending_only: bool = True) -> str:
    """
    Speaks back the to-do list.

    Args:
        pending_only: True (default) reads back only not-done items --
            the common "what's still on my plate" case. False includes
            completed items too, each marked "(done)".

    Returns:
        A spoken-friendly summary.
    """
    todos = _read_todos()
    if pending_only:
        todos = [t for t in todos if not t["done"]]

    if not todos:
        return "You have no pending to-dos." if pending_only else "Your to-do list is empty."

    if len(todos) == 1:
        t = todos[0]
        marker = " (done)" if t["done"] else ""
        return f"You have one to-do: {t['text']}{marker}."

    described = [f"{t['text']}{' (done)' if t['done'] else ''}" for t in todos]
    return f"You have {len(todos)} to-dos: " + "; ".join(described) + "."


def complete_todo(identifier: str) -> str:
    """
    Marks a to-do item as done, matched by exact id or fuzzy/substring
    text match -- see _resolve_todo_target() above. Never guesses on a
    low-confidence match.

    Args:
        identifier: the to-do's id, or a phrase matching its text.
    """
    if not identifier or not identifier.strip():
        return "Which to-do would you like me to mark as done?"
    todos = _read_todos()
    if not todos:
        return "Your to-do list is empty."

    target = _resolve_todo_target(identifier, todos)
    if target is None:
        return (
            f"I couldn't find a to-do matching '{identifier}'. "
            f"Could you say it a bit differently?"
        )
    if target["done"]:
        return f"'{target['text']}' is already marked done."

    target["done"] = True
    if not _write_todos(todos):
        return "Sorry, I ran into a problem updating that to-do."
    return f"Marked '{target['text']}' as done."


def delete_todo(identifier: str) -> str:
    """
    Deletes a to-do item, matched the same way complete_todo() does --
    see _resolve_todo_target() above. Never guesses on a low-confidence
    match.

    Args:
        identifier: the to-do's id, or a phrase matching its text.
    """
    if not identifier or not identifier.strip():
        return "Which to-do would you like me to delete?"
    todos = _read_todos()
    if not todos:
        return "Your to-do list is empty."

    target = _resolve_todo_target(identifier, todos)
    if target is None:
        return (
            f"I couldn't find a to-do matching '{identifier}'. "
            f"Could you say it a bit differently?"
        )

    remaining = [t for t in todos if t["id"] != target["id"]]
    if not _write_todos(remaining):
        return "Sorry, I ran into a problem deleting that to-do."
    return f"Deleted '{target['text']}' from your to-do list."