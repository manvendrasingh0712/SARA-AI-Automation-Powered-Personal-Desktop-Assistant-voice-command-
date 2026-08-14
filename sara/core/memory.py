"""
sara/core/memory.py
SQLite-backed persistent storage for Sara AI.

NOTE (structure fix): this used to exist as two near-duplicate copies —
an older draft here and the actual one in use at sara/tools/database.py.
This file is now the single canonical PreferencesDB (content taken from
the newer, in-use version); sara/tools/database.py has been removed.
"""

from __future__ import annotations

import difflib
import json
import logging
import queue
import sqlite3
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from datetime import date, datetime
from typing import Callable, Optional

from config import Config

logger = logging.getLogger(__name__)

# PRODUCTION-AUDIT FIX (DB split-brain): this used to be computed as
# os.path.join(os.path.dirname(__file__), "sara_data.db"), i.e. relative
# to THIS file's folder (sara/tools/). Meanwhile config.py independently
# computes its own canonical Config.DB_PATH (project root), and
# reminders.py used to default to a bare "sara_data.db" (relative to
# CWD). All three defaults disagreed, so — depending on where the app
# was launched from — preferences/conversation history, reminders, and
# the "canonical" path in config.py could all silently point at THREE
# DIFFERENT physical .db files. Now this module's default is Config.DB_PATH
# itself, so there is exactly one canonical, CWD-independent database
# file for the whole app, matching reminders.py's fix.
_DEFAULT_DB_PATH = Config.DB_PATH

_VALID_ROLES = frozenset({"user", "assistant", "system"})


class PreferencesDB:
    """Manages persistent user preferences and conversation logs via SQLite."""

    # See sara/core/llm/engine.py (SaraLLM._serializable) -- self.db is
    # exposed directly off the Api object, so this stops pywebview's js_api
    # bridge from recursing into the live sqlite3 connections/locks.
    _serializable = False

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._closed = False
        self._close_lock = threading.Lock()

        self._local = threading.local()
        self._read_conns_lock = threading.Lock()
        self._read_conns: list[sqlite3.Connection] = []

        self._write_conn: Optional[sqlite3.Connection] = None
        self._queue: queue.Queue = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None

        try:
            self._write_conn = self._open_connection()
            self._create_tables(self._write_conn)
            logger.debug("PreferencesDB initialized at '%s'.", self.db_path)
            if Config.DEBUG_MODE:
                print(f"[Debug] PreferencesDB initialized at '{self.db_path}'.")
        except sqlite3.Error as e:
            logger.error("Failed to initialize preferences database: %s", e)
            print(f"[Error] Failed to initialize preferences database: {e}")
            self._write_conn = None
            self._closed = True
            return

        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="PreferencesDB-Writer",
            daemon=True,
        )
        self._writer_thread.start()

    def __repr__(self) -> str:
        status = "closed" if self._closed else "open"
        return f"PreferencesDB(db_path={self.db_path!r}, status={status!r})"

    # ── Context manager ───────────────────────────────────────────────────

    def __enter__(self) -> "PreferencesDB":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ── Connection setup ──────────────────────────────────────────────────

    def _open_connection(self) -> sqlite3.Connection:
        """Opens a connection with pragmas tuned for low-latency local use."""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=5.0,
        )
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA cache_size=-8000;")
        conn.execute("PRAGMA mmap_size=268435456;")
        conn.execute("PRAGMA wal_autocheckpoint=1000;")
        return conn

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        # executescript issues an implicit COMMIT; no explicit conn.commit() needed.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS preferences (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                role      TEXT    NOT NULL,
                message   TEXT    NOT NULL,
                timestamp TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS proactive_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger   TEXT    NOT NULL,
                message   TEXT    NOT NULL,
                reason    TEXT    NOT NULL,
                timestamp TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routines (
                name       TEXT    PRIMARY KEY,
                definition TEXT    NOT NULL,
                enabled    INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS decision_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT    NOT NULL,
                old_value   TEXT,
                new_value   TEXT    NOT NULL,
                reason      TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL
            );
            """)

    def _get_read_conn(self) -> sqlite3.Connection:
        """Lazily creates and caches one connection per calling thread."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_connection()
            self._local.conn = conn
            with self._read_conns_lock:
                self._read_conns.append(conn)
        return conn

    # ── Writer thread ─────────────────────────────────────────────────────

    def _writer_loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                break
            fn, future = job
            write_conn = self._write_conn
            if write_conn is None:
                if future is not None:
                    future.set_exception(RuntimeError("Write connection is closed."))
                continue
            try:
                result = fn(write_conn)
                if future is not None:
                    future.set_result(result)
            except Exception as e:
                if future is not None:
                    future.set_exception(e)
                else:
                    logger.error("Background DB write failed: %s", e)
                    print(f"[Error] Background DB write failed: {e}")

    def _submit_write(
        self,
        fn: Callable[[sqlite3.Connection], bool],
        wait: bool = True,
        timeout: float = 5.0,
    ) -> bool:
        """
        Queues fn(write_conn) to run on the writer thread.
        If wait=True, blocks until it finishes and returns its result.
        If wait=False, returns True immediately (fire-and-forget).
        """
        with self._close_lock:
            if self._closed or self._write_conn is None:
                return False
            future: Optional[Future] = Future() if wait else None
            self._queue.put((fn, future))

        if wait and future is not None:
            try:
                return bool(future.result(timeout=timeout))
            except FutureTimeoutError:
                logger.error("DB write timed out after %.1fs.", timeout)
                print(f"[Error] DB write did not complete in time ({timeout}s).")
                return False
            except Exception as e:
                logger.error("DB write raised an exception: %s", e)
                print(f"[Error] DB write raised an exception: {e}")
                return False
        return True

    # ── Input validation helpers ──────────────────────────────────────────

    @staticmethod
    def _validate_key(key: str) -> None:
        """Raises ValueError if key is not a non-empty, non-whitespace string."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Preference key must be a non-empty string; got {key!r}.")

    @staticmethod
    def _validate_nonempty(value: str, name: str) -> str:
        """Strips value and raises ValueError if the result is empty."""
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"{name} must not be empty or whitespace-only.")
        return stripped

    # ── Generic key-value ─────────────────────────────────────────────────

    def set_preference(self, key: str, value: str, wait: bool = True) -> bool:
        """Inserts or updates a preference by key."""
        self._validate_key(key)
        if not isinstance(value, str):
            raise TypeError(
                f"Preference value must be a str; got {type(value).__name__!r}."
            )

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                conn.execute(
                    """
                    INSERT INTO preferences (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("set_preference('%s'): %s", key, e)
                print(f"[Error] set_preference('{key}'): {e}")
                return False

        return self._submit_write(_do, wait=wait)

    def get_preference(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Returns the stored value for key, or default if not found."""
        self._validate_key(key)
        if self._closed:
            return default
        try:
            conn = self._get_read_conn()
            cursor = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
        except sqlite3.Error as e:
            logger.error("get_preference('%s'): %s", key, e)
            print(f"[Error] get_preference('{key}'): {e}")
            return default

    def delete_preference(self, key: str, wait: bool = True) -> bool:
        """Deletes a preference by key. Returns True if a row was deleted."""
        self._validate_key(key)

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                cursor = conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
                conn.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("delete_preference('%s'): %s", key, e)
                print(f"[Error] delete_preference('{key}'): {e}")
                return False

        return self._submit_write(_do, wait=wait)

    def get_all_preferences(self) -> dict[str, str]:
        """Returns all stored preferences as a key-value dict."""
        if self._closed:
            return {}
        try:
            conn = self._get_read_conn()
            cursor = conn.execute("SELECT key, value FROM preferences")
            return dict(cursor.fetchall())
        except sqlite3.Error as e:
            logger.error("get_all_preferences: %s", e)
            print(f"[Error] get_all_preferences: {e}")
            return {}

    # ── Typed helpers ─────────────────────────────────────────────────────

    def get_wake_word(self) -> str:
        """Returns the stored wake word, falling back to Config.WAKE_WORD."""
        return self.get_preference("wake_word", default=Config.WAKE_WORD)

    def set_wake_word(self, wake_word: str) -> bool:
        """Persists a new wake word (lowercased and stripped)."""
        cleaned = self._validate_nonempty(wake_word, "wake_word")
        return self.set_preference("wake_word", cleaned.lower())

    def get_user_name(self) -> Optional[str]:
        """Returns the stored user name, or None if not set."""
        return self.get_preference("user_name", default=None)

    def set_user_name(self, name: str) -> bool:
        """Persists the user name (stripped)."""
        cleaned = self._validate_nonempty(name, "user name")
        ok = self.set_preference("user_name", cleaned)
        # BUGFIX: force this specific write out of the WAL and into the
        # main db file immediately. user_name is written rarely (once per
        # onboarding) but must survive even an abrupt process kill —
        # unlike high-frequency prefs (slider drags) where this would be
        # wasteful, a single explicit checkpoint here is cheap and safe.
        if ok and self._write_conn is not None:
            try:
                self._write_conn.execute("PRAGMA wal_checkpoint(FULL);")
            except sqlite3.Error as e:
                logger.warning("wal_checkpoint after set_user_name failed: %s", e)
        return ok

    # ── Conversation log ──────────────────────────────────────────────────

    def log_message(self, role: str, message: str, wait: bool = False) -> bool:
        """
        Appends a message to the persistent conversation log.

        Defaults to fire-and-forget (wait=False). Pass wait=True for a hard
        commit guarantee before continuing (e.g. before clearing in-memory history).
        """
        if role not in _VALID_ROLES:
            raise ValueError(
                f"role must be one of {sorted(_VALID_ROLES)}; got {role!r}."
            )
        if not message or not message.strip():
            raise ValueError("message must not be empty or whitespace-only.")

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                conn.execute(
                    "INSERT INTO conversation_log (role, message, timestamp) VALUES (?, ?, ?)",
                    (role, message, datetime.now().isoformat()),
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("log_message: %s", e)
                print(f"[Error] log_message: {e}")
                return False

        return self._submit_write(_do, wait=wait)

    def get_recent_messages(self, limit: int = 20) -> list[dict[str, str]]:
        """Returns the most recent `limit` messages in chronological order."""
        if not isinstance(limit, int) or limit <= 0:
            return []
        if self._closed:
            return []
        try:
            conn = self._get_read_conn()
            cursor = conn.execute(
                """
                SELECT role, message, timestamp
                FROM (
                    SELECT role, message, timestamp, id
                    FROM conversation_log
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (limit,),
            )
            return [
                {"role": r[0], "message": r[1], "timestamp": r[2]}
                for r in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            logger.error("get_recent_messages: %s", e)
            print(f"[Error] get_recent_messages: {e}")
            return []

    def clear_conversation_log(self, wait: bool = True) -> bool:
        """Wipes all rows from the conversation log (for 'forget history' feature)."""

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                conn.execute("DELETE FROM conversation_log")
                conn.commit()
                return True
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("clear_conversation_log: %s", e)
                print(f"[Error] clear_conversation_log: {e}")
                return False

        return self._submit_write(_do, wait=wait)

    # ── Proactive engine log ────────────────────────────────────────────
    # Backs two features built on top of sara/orchestrator/proactive.py:
    #   1. "Why did you say that?" transparency — get_last_proactive_event()
    #      lets the why_proactive intent (sara/orchestrator/intent_handlers.py)
    #      explain the reasoning behind Sara's most recent unprompted remark.
    #   2. The Settings page's Proactive Insights card — get_proactive_stats()
    #      feeds get_proactive_stats (a new Api method) with per-trigger
    #      counts and a recent-activity list.

    def log_proactive_event(
        self, trigger: str, message: str, reason: str, wait: bool = False
    ) -> bool:
        """
        Records one Proactive Engine nudge. Defaults to fire-and-forget
        (wait=False) since this is called from the proactive background
        thread right after speaking — it must never make that thread wait
        on a disk write.
        """
        if not trigger or not message:
            return False

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                conn.execute(
                    "INSERT INTO proactive_log (trigger, message, reason, timestamp) "
                    "VALUES (?, ?, ?, ?)",
                    (trigger, message, reason or "", datetime.now().isoformat()),
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("log_proactive_event: %s", e)
                print(f"[Error] log_proactive_event: {e}")
                return False

        return self._submit_write(_do, wait=wait)

    def get_recent_proactive_events(self, limit: int = 20) -> list[dict[str, str]]:
        """Returns the most recent `limit` proactive nudges, newest last."""
        if not isinstance(limit, int) or limit <= 0:
            return []
        if self._closed:
            return []
        try:
            conn = self._get_read_conn()
            cursor = conn.execute(
                """
                SELECT trigger, message, reason, timestamp
                FROM (
                    SELECT trigger, message, reason, timestamp, id
                    FROM proactive_log
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (limit,),
            )
            return [
                {"trigger": r[0], "message": r[1], "reason": r[2], "timestamp": r[3]}
                for r in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            logger.error("get_recent_proactive_events: %s", e)
            print(f"[Error] get_recent_proactive_events: {e}")
            return []

    def get_last_proactive_event(self) -> Optional[dict[str, str]]:
        """Returns the single most recent proactive nudge, or None if there isn't one."""
        recent = self.get_recent_proactive_events(limit=1)
        return recent[-1] if recent else None

    def get_proactive_stats(self) -> dict:
        """
        Returns {"total": int, "by_trigger": {trigger: count}, "recent": [...]}
        for the Settings page's Proactive Insights card. "recent" is capped
        at 10 entries, newest last, same shape as get_recent_proactive_events.
        """
        if self._closed:
            return {"total": 0, "by_trigger": {}, "recent": []}
        try:
            conn = self._get_read_conn()
            cursor = conn.execute(
                "SELECT trigger, COUNT(*) FROM proactive_log GROUP BY trigger"
            )
            by_trigger = {row[0]: row[1] for row in cursor.fetchall()}
            return {
                "total": sum(by_trigger.values()),
                "by_trigger": by_trigger,
                "recent": self.get_recent_proactive_events(limit=10),
            }
        except sqlite3.Error as e:
            logger.error("get_proactive_stats: %s", e)
            print(f"[Error] get_proactive_stats: {e}")
            return {"total": 0, "by_trigger": {}, "recent": []}

    # ── Decision memory (NEW) ────────────────────────────────────────────
    # Backs the "why did I change X" voice intent
    # (sara/orchestrator/intent_handlers.py's _h_why_decision). Hooked
    # from sara/gui/app/helpers.py's _PrefWriter._run() -- the single
    # chokepoint every settings/config change (voice OR GUI) already
    # flows through, since every one of sara/gui/app/settings.py's
    # setter methods (set_mute, set_focus_mode, update_setting,
    # set_assistant_active, set_mic_sensitivity, set_speech_speed,
    # set_language, set_skill_enabled) calls
    # self._pref_writer.enqueue(key, value) rather than
    # db.set_preference() directly. NOT hooked into update_setting()
    # alone -- that would miss 7 of the 8 setter methods.

    def log_decision(
        self,
        setting_key: str,
        old_value: Optional[str],
        new_value: str,
        reason: str,
        wait: bool = False,
    ) -> bool:
        """
        Records one settings/config change with a plain-English reason.
        Defaults to fire-and-forget (wait=False) since this is called
        right after a preference write completes and must never add
        latency to that path (same contract as log_proactive_event()
        above).
        """
        if not setting_key or new_value is None:
            return False

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                conn.execute(
                    "INSERT INTO decision_log (setting_key, old_value, new_value, reason, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (setting_key, old_value, new_value, reason or "", datetime.now().isoformat()),
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("log_decision('%s'): %s", setting_key, e)
                print(f"[Error] log_decision('{setting_key}'): {e}")
                return False

        return self._submit_write(_do, wait=wait)

    def get_recent_decisions(self, limit: int = 50) -> list[dict[str, str]]:
        """Returns the most recent `limit` decision-log entries, newest last."""
        if not isinstance(limit, int) or limit <= 0:
            return []
        if self._closed:
            return []
        try:
            conn = self._get_read_conn()
            cursor = conn.execute(
                """
                SELECT setting_key, old_value, new_value, reason, timestamp
                FROM (
                    SELECT setting_key, old_value, new_value, reason, timestamp, id
                    FROM decision_log
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (limit,),
            )
            return [
                {
                    "setting_key": r[0],
                    "old_value": r[1],
                    "new_value": r[2],
                    "reason": r[3],
                    "timestamp": r[4],
                }
                for r in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            logger.error("get_recent_decisions: %s", e)
            print(f"[Error] get_recent_decisions: {e}")
            return []

    def find_decision_by_query(
        self, query_text: str, limit_scan: int = 200
    ) -> Optional[dict[str, str]]:
        """
        Fuzzy-matches `query_text` (e.g. "mic sensitivity" captured from
        the "why did I change X" voice intent) against the setting_key
        of recent decision_log entries, using stdlib difflib (no new
        dependency -- it's part of the Python standard library). Returns
        the single most recent entry whose setting_key clears a
        similarity threshold, or None if nothing matches confidently --
        the caller (see _h_why_decision in intent_handlers.py) must then
        say so instead of guessing, per this feature's spec.
        """
        if not query_text or not query_text.strip():
            return None
        recent = self.get_recent_decisions(limit=limit_scan)
        if not recent:
            return None

        normalized_query = query_text.strip().lower()
        best_entry: Optional[dict[str, str]] = None
        best_score = 0.0
        # get_recent_decisions() returns oldest-first; iterate reversed
        # (newest-first) so that on a tied score, the MOST RECENT
        # matching change wins -- "why did I change X" should answer
        # about the latest change, not an old one.
        for entry in reversed(recent):
            key_display = entry["setting_key"].replace("_", " ").replace(":", " ").lower()
            ratio = difflib.SequenceMatcher(None, normalized_query, key_display).ratio()
            # Bonus for a direct substring match either direction --
            # handles "mic" matching "mic sensitivity" even though the
            # whole-string ratio alone would be fairly low.
            if normalized_query in key_display or key_display in normalized_query:
                ratio = max(ratio, 0.75)
            if ratio > best_score:
                best_score = ratio
                best_entry = entry

        _MATCH_THRESHOLD = 0.45
        if best_entry is not None and best_score >= _MATCH_THRESHOLD:
            return best_entry
        return None

    # ── Daily talk streak (personality feature) ─────────────────────────
    # Backs "how many days in a row have we talked" and
    # sara/orchestrator/proactive.py's streak-milestone nudge.
    _STREAK_MILESTONES = (3, 7, 14, 30, 50, 100, 200, 365)

    def record_interaction_day(self) -> int:
        """
        Idempotent per calendar day — safe to call on every single wake
        event (that's how sara/orchestrator/core_wiring.py uses it); only
        the FIRST call on a given date actually does anything. Returns the
        current streak count either way. If today's update just crossed
        one of _STREAK_MILESTONES, also stores it in the
        "streak_pending_milestone" preference for the Proactive Engine to
        pick up, announce once, and clear.
        """
        today = date.today().isoformat()
        last = self.get_preference("streak_last_date")
        if last == today:
            return int(self.get_preference("streak_count") or "1")

        gap = 999
        if last:
            try:
                gap = (date.today() - date.fromisoformat(last)).days
            except ValueError:
                gap = 999

        current = int(self.get_preference("streak_count") or "0")
        new_streak = current + 1 if gap == 1 else 1

        self.set_preference("streak_last_date", today)
        self.set_preference("streak_count", str(new_streak))
        if new_streak in self._STREAK_MILESTONES:
            self.set_preference("streak_pending_milestone", str(new_streak))
        return new_streak

    def get_streak_count(self) -> int:
        try:
            return int(self.get_preference("streak_count") or "0")
        except (TypeError, ValueError):
            return 0

    def get_conversation_stats(self) -> dict:
        """
        {"total_messages": int, "first_message_date": str|None (ISO)} —
        backs the Shareable Moments card (sara/gui/index.html's Share
        button). Never raises; returns zeros on any failure.
        """
        if self._closed:
            return {"total_messages": 0, "first_message_date": None}
        try:
            conn = self._get_read_conn()
            cursor = conn.execute("SELECT COUNT(*), MIN(timestamp) FROM conversation_log")
            row = cursor.fetchone()
            return {
                "total_messages": row[0] or 0,
                "first_message_date": row[1],
            }
        except sqlite3.Error as e:
            logger.error("get_conversation_stats: %s", e)
            print(f"[Error] get_conversation_stats: {e}")
            return {"total_messages": 0, "first_message_date": None}

    # ── Routines (Automation) ─────────────────────────────────────────────
    # Backs sara/core/routines.py's run_routine(), the "run <name> routine"
    # voice intent (sara/orchestrator/intent_handlers.py's _h_run_routine),
    # the scheduled auto-trigger (sara/orchestrator/proactive.py), and the
    # Settings > Routines page (sara/gui/app/routines_api.py). `definition`
    # stores the FULL routine dict (name/label/steps/trigger_time) as JSON
    # so its shape can grow later without a schema migration.

    def save_routine(self, name: str, definition: dict, wait: bool = True) -> bool:
        """Inserts or updates a routine by name. `definition` is the full routine dict."""
        self._validate_key(name)
        if not isinstance(definition, dict):
            raise TypeError(
                f"Routine definition must be a dict; got {type(definition).__name__!r}."
            )

        try:
            payload = json.dumps(definition)
        except (TypeError, ValueError) as e:
            logger.error("save_routine('%s'): definition not JSON-serializable: %s", name, e)
            print(f"[Error] save_routine('{name}'): definition not JSON-serializable: {e}")
            return False

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                conn.execute(
                    """
                    INSERT INTO routines (name, definition, enabled) VALUES (?, ?, 1)
                    ON CONFLICT(name) DO UPDATE SET definition = excluded.definition
                    """,
                    (name, payload),
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("save_routine('%s'): %s", name, e)
                print(f"[Error] save_routine('{name}'): {e}")
                return False

        return self._submit_write(_do, wait=wait)

    def get_routine(self, name: str) -> Optional[dict]:
        """Returns the routine's definition dict (with 'name'/'enabled' merged in), or None."""
        self._validate_key(name)
        if self._closed:
            return None
        try:
            conn = self._get_read_conn()
            cursor = conn.execute(
                "SELECT definition, enabled FROM routines WHERE name = ?", (name,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            try:
                definition = json.loads(row[0])
            except (TypeError, ValueError) as e:
                logger.error("get_routine('%s'): stored definition is not valid JSON: %s", name, e)
                return None
            definition["name"] = name
            definition["enabled"] = bool(row[1])
            return definition
        except sqlite3.Error as e:
            logger.error("get_routine('%s'): %s", name, e)
            print(f"[Error] get_routine('{name}'): {e}")
            return None

    def delete_routine(self, name: str, wait: bool = True) -> bool:
        """Deletes a routine by name. Returns True if a row was deleted."""
        self._validate_key(name)

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                cursor = conn.execute("DELETE FROM routines WHERE name = ?", (name,))
                conn.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("delete_routine('%s'): %s", name, e)
                print(f"[Error] delete_routine('{name}'): {e}")
                return False

        return self._submit_write(_do, wait=wait)

    def list_routines(self) -> list[dict]:
        """Returns every saved routine's definition dict (see get_routine)."""
        if self._closed:
            return []
        try:
            conn = self._get_read_conn()
            cursor = conn.execute("SELECT name, definition, enabled FROM routines")
            out: list[dict] = []
            for r_name, r_def, r_enabled in cursor.fetchall():
                try:
                    definition = json.loads(r_def)
                except (TypeError, ValueError) as e:
                    logger.error("list_routines: bad JSON for '%s': %s", r_name, e)
                    continue
                definition["name"] = r_name
                definition["enabled"] = bool(r_enabled)
                out.append(definition)
            return out
        except sqlite3.Error as e:
            logger.error("list_routines: %s", e)
            print(f"[Error] list_routines: {e}")
            return []

    def set_routine_enabled(self, name: str, enabled: bool, wait: bool = True) -> bool:
        """Toggles a routine's enabled flag (Settings page switch / auto-trigger gate)."""
        self._validate_key(name)

        def _do(conn: sqlite3.Connection) -> bool:
            try:
                cursor = conn.execute(
                    "UPDATE routines SET enabled = ? WHERE name = ?",
                    (1 if enabled else 0, name),
                )
                conn.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("set_routine_enabled('%s'): %s", name, e)
                print(f"[Error] set_routine_enabled('{name}'): {e}")
                return False

        return self._submit_write(_do, wait=wait)

    def get_routine_last_run_date(self, name: str) -> Optional[str]:
        """
        Per-routine 'already ran today' marker for the scheduled-trigger
        auto-run (sara/orchestrator/proactive.py) -- same idempotent-per-day
        shape as record_interaction_day()'s streak tracking above, just
        keyed per routine instead of globally. Stored as a plain preference
        (no new table needed) under "routine_last_run:<name>".
        """
        return self.get_preference(f"routine_last_run:{name}")

    def set_routine_last_run_date(self, name: str, iso_date: str, wait: bool = False) -> bool:
        """Fire-and-forget by default -- called right after a scheduled routine runs."""
        return self.set_preference(f"routine_last_run:{name}", iso_date, wait=wait)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Closes all connections and stops the writer thread. Safe to call multiple times."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        if self._writer_thread is not None:
            self._queue.put(None)
            self._writer_thread.join(timeout=5.0)
            self._writer_thread = None

        if self._write_conn is not None:
            try:
                self._write_conn.close()
            except sqlite3.Error as e:
                logger.warning("Error closing write connection: %s", e)
            self._write_conn = None

        with self._read_conns_lock:
            for conn in self._read_conns:
                try:
                    conn.close()
                except sqlite3.Error as e:
                    logger.warning("Error closing read connection: %s", e)
            self._read_conns.clear()

        logger.debug("PreferencesDB closed.")
        if Config.DEBUG_MODE:
            print("[Debug] PreferencesDB connection closed.")

    def __del__(self) -> None:
        # Only catch Exception, not BaseException — SystemExit/KeyboardInterrupt must propagate.
        try:
            self.close()
        except Exception:
            pass