"""
sara/tools/reminders.py
Persistent reminder/alarm system for Sara AI.

Reminders are stored in the SQLite database (sara_data.db) so they
survive app restarts. A background thread polls for due reminders at
a configurable interval and fires a callback (typically: play a beep
+ speak the reminder text) when one comes due.

Natural language time parsing is handled via `dateparser`, so users
can say things like "remind me in 10 minutes to check the oven" or
"set a reminder for tomorrow at 5pm to call mom".

CALENDAR SYNC (additive):
- Added a `done` column (auto-migrated via ALTER TABLE on existing DBs).
- Added add() / delete() / toggle() / get_all() — calendar-shaped API
  consumed by sara/gui/app.py's Api class (add_reminder/delete_reminder/
  toggle_reminder/get_reminders). These operate on the SAME `reminders`
  table as the voice-driven add_reminder()/list_reminders(), so calendar
  reminders now persist across restarts AND get spoken + beeped by the
  existing background poller when they come due — no separate system.

LATENCY FIX (this revision):
- dateparser.parse() was called with no `languages` restriction, so by
  default it tries to match the input against dozens of locales before
  settling — a well-documented dateparser performance trap (often
  several hundred ms+ per call). Restricted to languages=['en','hi'],
  the only two Sara actually needs (per Config.SARA_LANGUAGE), cutting
  reminder/timer voice-command parse latency substantially with no
  loss of functionality.
"""

import sqlite3
import threading
import time
import math
from datetime import datetime, timedelta
from typing import Optional, Callable, List, Dict, Any

import dateparser

from config import Config

_DEFAULT_DB_PATH = "sara_data.db"


class ReminderManager:
    """Manages persistent reminders/alarms with background polling."""

    # See sara/core/llm/engine.py (SaraLLM._serializable) -- self.reminders
    # is exposed directly off the Api object, so this stops pywebview's
    # js_api bridge from recursing into the live sqlite3 connection/thread.
    _serializable = False

    def __init__(self, db_path: str = _DEFAULT_DB_PATH, on_trigger: Optional[Callable[[str], None]] = None):
        """
        Args:
            db_path: Path to the shared SQLite database file.
            on_trigger: Callback invoked with the reminder's message
                        text when a reminder comes due. Typically set
                        by main.py to play a beep + speak the message.
        """
        self.db_path = db_path
        self.on_trigger = on_trigger
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._create_table()
            self._ensure_done_column()
            if Config.DEBUG_MODE:
                print(f"[Debug] ReminderManager initialized at '{self.db_path}'.")
        except sqlite3.Error as e:
            print(f"[Error] Failed to initialize reminders database: {e}")
            self._conn = None

    def _create_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message     TEXT NOT NULL,
                due_at      TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                triggered   INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.commit()

    def _ensure_done_column(self) -> None:
        """
        Adds a 'done' column to pre-existing databases that were created
        before the Calendar UI sync feature existed. Safe no-op if the
        column is already present.
        """
        if not self._conn:
            return
        try:
            cur = self._conn.execute("PRAGMA table_info(reminders)")
            cols = [row[1] for row in cur.fetchall()]
            if "done" not in cols:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN done INTEGER NOT NULL DEFAULT 0"
                )
                self._conn.commit()
                if Config.DEBUG_MODE:
                    print("[Debug] Migrated reminders table: added 'done' column.")
        except sqlite3.Error as e:
            print(f"[Warning] Could not ensure 'done' column on reminders table: {e}")

    # ------------------------------------------------------------
    # Public API (voice-driven, natural language)
    # ------------------------------------------------------------

    def add_reminder(self, message: str, when_text: str) -> str:
        """
        Parses a natural-language time expression and schedules a
        reminder.

        Args:
            message: What the reminder is about (e.g. "check the oven").
            when_text: Natural language time, e.g. "in 10 minutes",
                       "tomorrow at 5pm", "tonight at 9".

        Returns:
            A human-readable confirmation or error message.
        """
        if not self._conn:
            return "Reminder system is unavailable right now."

        if not message or not message.strip():
            return "Please tell me what to remind you about."

        if not when_text or not when_text.strip():
            return "Please tell me when to remind you."

        due_dt = dateparser.parse(
            when_text,
            languages=["en", "hi"],
            settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
        )

        if not due_dt:
            return f"I couldn't understand the time '{when_text}'. Could you rephrase it?"

        if due_dt <= datetime.now():
            return "That time has already passed. Please give me a future time."

        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO reminders (message, due_at, created_at, triggered, done) "
                    "VALUES (?, ?, ?, 0, 0)",
                    (message.strip(), due_dt.isoformat(), datetime.now().isoformat()),
                )
                self._conn.commit()

            friendly_time = due_dt.strftime("%I:%M %p on %B %d")
            return f"Got it. I'll remind you to {message.strip()} at {friendly_time}."
        except sqlite3.Error as e:
            return f"Failed to save the reminder. Error: {e}"

    def list_reminders(self) -> str:
        """Returns a human-readable list of all upcoming (untriggered) reminders."""
        if not self._conn:
            return "Reminder system is unavailable right now."

        try:
            cursor = self._conn.execute(
                "SELECT message, due_at FROM reminders WHERE triggered = 0 ORDER BY due_at ASC"
            )
            rows = cursor.fetchall()
            if not rows:
                return "You have no upcoming reminders."

            lines = []
            for msg, due_at in rows:
                due_dt = datetime.fromisoformat(due_at)
                friendly_time = due_dt.strftime("%I:%M %p on %B %d")
                lines.append(f"{msg} at {friendly_time}")

            return "Here are your upcoming reminders: " + "; ".join(lines)
        except sqlite3.Error as e:
            return f"Failed to fetch reminders. Error: {e}"

    def cancel_all_reminders(self) -> str:
        """Cancels (deletes) all pending reminders."""
        if not self._conn:
            return "Reminder system is unavailable right now."

        try:
            with self._lock:
                cursor = self._conn.execute("DELETE FROM reminders WHERE triggered = 0")
                self._conn.commit()
                count = cursor.rowcount

            if count == 0:
                return "You have no pending reminders to cancel."
            return f"Cancelled {count} pending reminder(s)."
        except sqlite3.Error as e:
            return f"Failed to cancel reminders. Error: {e}"

    # ------------------------------------------------------------
    # Calendar-style API (used by sara/gui/app.py Api class)
    # ------------------------------------------------------------

    def add(self, date_str: str, time_str: str, text: str) -> int:
        """
        Calendar-shaped reminder add — used by the GUI Calendar page.

        Args:
            date_str: 'YYYY-MM-DD'
            time_str: 'HH:MM' (optional/blank → defaults to 00:00)
            text: Reminder text.

        Returns:
            The new reminder's database id, or -1 on failure.
        """
        if not self._conn or not text or not text.strip():
            return -1
        try:
            time_part = (time_str or "").strip() or "00:00"
            if len(time_part) == 5:
                time_part += ":00"
            due_at = f"{date_str.strip()}T{time_part}"
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO reminders (message, due_at, created_at, triggered, done) "
                    "VALUES (?, ?, ?, 0, 0)",
                    (text.strip(), due_at, datetime.now().isoformat()),
                )
                self._conn.commit()
                return cur.lastrowid
        except sqlite3.Error as e:
            print(f"[Error] ReminderManager.add failed: {e}")
            return -1

    def delete(self, reminder_id: Any) -> bool:
        """Deletes a reminder by its database id."""
        if not self._conn:
            return False
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM reminders WHERE id = ?", (int(reminder_id),)
                )
                self._conn.commit()
            return True
        except (sqlite3.Error, ValueError, TypeError) as e:
            print(f"[Error] ReminderManager.delete failed: {e}")
            return False

    def toggle(self, reminder_id: Any) -> bool:
        """Flips the 'done' flag on a reminder by its database id."""
        if not self._conn:
            return False
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT done FROM reminders WHERE id = ?", (int(reminder_id),)
                )
                row = cur.fetchone()
                if not row:
                    return False
                new_done = 0 if row[0] else 1
                self._conn.execute(
                    "UPDATE reminders SET done = ? WHERE id = ?",
                    (new_done, int(reminder_id)),
                )
                self._conn.commit()
            return True
        except (sqlite3.Error, ValueError, TypeError) as e:
            print(f"[Error] ReminderManager.toggle failed: {e}")
            return False

    def get_all(self) -> List[Dict[str, Any]]:
        """
        Returns every reminder, calendar-shaped:
        [{ "id": int, "text": str, "date": "YYYY-MM-DD", "time": "HH:MM", "done": bool }, ...]
        """
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(
                "SELECT id, message, due_at, done FROM reminders ORDER BY due_at ASC"
            )
            rows = cur.fetchall()
            result: List[Dict[str, Any]] = []
            for rid, message, due_at, done in rows:
                try:
                    dt = datetime.fromisoformat(due_at)
                    date_str = dt.strftime("%Y-%m-%d")
                    time_str = dt.strftime("%H:%M")
                except ValueError:
                    date_str, time_str = due_at, ""
                result.append({
                    "id": rid,
                    "text": message,
                    "date": date_str,
                    "time": time_str,
                    "done": bool(done),
                })
            return result
        except sqlite3.Error as e:
            print(f"[Error] ReminderManager.get_all failed: {e}")
            return []

    def get_upcoming(self, within_minutes: int) -> List[Dict[str, Any]]:
        """
        Returns not-yet-done reminders whose due_at falls between now and
        now + within_minutes, calendar-shaped like get_all():
        [{ "id": int, "text": str, "due_at": str (ISO) }, ...]

        Used by sara/orchestrator/proactive.py to give a spoken heads-up
        BEFORE a reminder is actually due — separate from and additive to
        the existing on-time alarm in _check_due_reminders(), which still
        fires exactly as before when the reminder's real due_at arrives.
        """
        if not self._conn:
            return []
        try:
            now = datetime.now()
            horizon = (now + timedelta(minutes=max(0, within_minutes))).isoformat()
            now_iso = now.isoformat()
            with self._lock:
                cur = self._conn.execute(
                    "SELECT id, message, due_at FROM reminders "
                    "WHERE done = 0 AND due_at > ? AND due_at <= ? "
                    "ORDER BY due_at ASC",
                    (now_iso, horizon),
                )
                rows = cur.fetchall()
            return [
                {"id": rid, "text": message, "due_at": due_at}
                for rid, message, due_at in rows
            ]
        except sqlite3.Error as e:
            print(f"[Error] ReminderManager.get_upcoming failed: {e}")
            return []

    # ------------------------------------------------------------
    # Background polling
    # ------------------------------------------------------------

    def start(self) -> None:
        """Starts the background thread that polls for due reminders."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        if Config.DEBUG_MODE:
            print("[Debug] Reminder background polling started.")

    def stop(self) -> None:
        """Stops the background polling thread."""
        self._stop_event.set()

    def close(self) -> None:
        """Stops reminder polling and closes the database connection."""
        self.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._conn:
            try:
                self._conn.close()
            except Exception as e:
                if Config.DEBUG_MODE:
                    print(f"[Warning] ReminderManager close failed: {e}")
            finally:
                self._conn = None
        if Config.DEBUG_MODE:
            print("[Debug] ReminderManager closed.")

    def shutdown(self) -> None:
        """Alias for close() to support consumers using a shutdown lifecycle."""
        self.close()

    def _poll_loop(self) -> None:
        """Continuously checks for due reminders at the configured interval."""
        interval = max(1, Config.REMINDER_CHECK_INTERVAL)
        while not self._stop_event.wait(timeout=interval):
            self._check_due_reminders()

    def _check_due_reminders(self) -> None:
        """Checks the database for due, untriggered reminders and fires them."""
        if not self._conn:
            return

        try:
            now_iso = datetime.now().isoformat()
            with self._lock:
                cursor = self._conn.execute(
                    "SELECT id, message FROM reminders WHERE triggered = 0 AND due_at <= ?",
                    (now_iso,),
                )
                due_rows = cursor.fetchall()

                for reminder_id, message in due_rows:
                    self._conn.execute(
                        "UPDATE reminders SET triggered = 1 WHERE id = ?", (reminder_id,)
                    )
                self._conn.commit()

            for _, message in due_rows:
                if self.on_trigger:
                    try:
                        self.on_trigger(message)
                    except Exception as e:
                        print(f"[Warning] Reminder trigger callback failed: {e}")
        except sqlite3.Error as e:
            print(f"[Error] Failed to check due reminders: {e}")


def play_alarm_beep(repetitions: int = 3) -> None:
    """
    Plays a simple, generated alarm beep sound — no audio file needed.
    Uses Python's standard `winsound` module on Windows for a
    guaranteed, dependency-free beep.

    Args:
        repetitions: How many times to beep.
    """
    try:
        import winsound
        for _ in range(repetitions):
            winsound.Beep(1000, 300)  # 1000 Hz for 300 ms
            time.sleep(0.15)
    except ImportError:
        # Non-Windows fallback: terminal bell character.
        for _ in range(repetitions):
            print("\a", end="", flush=True)
            time.sleep(0.3)
    except Exception as e:
        if Config.DEBUG_MODE:
            print(f"[Warning] Failed to play alarm beep: {e}")