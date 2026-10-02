"""
sara.core.telemetry.store
SQLite sink for finished turn records: one bounded queue, one daemon writer
thread, batch inserts and a daily prune. Any DB error disables persistence
for the session (logged once); callers are never affected.
"""
from __future__ import annotations

import contextlib
import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from config import Config, app_data_subdir

logger = logging.getLogger(__name__)

DB_FILENAME = "telemetry.sqlite"
STOP = object()

COLUMNS = (
    "turn_id", "ts", "source", "lang", "route", "intent", "model", "outcome",
    "error_code", "tainted", "text_len", "text_hash", "text", "speech_end_ms",
    "stt_ms", "route_ms", "llm_ttft_ms", "llm_total_ms", "tool_ms",
    "tts_start_ms", "ttfa_ms", "total_ms", "extra_json",
)
_INSERT_SQL = (
    f"INSERT OR REPLACE INTO turn_trace ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(COLUMNS))})"
)
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS turn_trace ("
    "turn_id TEXT PRIMARY KEY, ts REAL, source TEXT, lang TEXT, route TEXT, "
    "intent TEXT, model TEXT, outcome TEXT, error_code TEXT, "
    "tainted INTEGER DEFAULT 0, text_len INTEGER, text_hash TEXT, text TEXT, "
    "speech_end_ms REAL, stt_ms REAL, route_ms REAL, llm_ttft_ms REAL, "
    "llm_total_ms REAL, tool_ms REAL, tts_start_ms REAL, ttfa_ms REAL, "
    "total_ms REAL, extra_json TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_turn_trace_ts ON turn_trace(ts)",
)
_BATCH_ROWS = 50
_FLUSH_S = 2.0
_MAX_DB_ROWS = 20000
_PRUNE_EVERY_S = 86400.0


def db_path() -> Path:
    """Location of telemetry.sqlite inside the app-data "data" folder."""
    return Path(app_data_subdir("data")) / DB_FILENAME


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the trace DB in WAL mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def _retention_days() -> int:
    try:
        return max(1, min(365, int(getattr(Config, "TRACE_RETENTION_DAYS", 30))))
    except (TypeError, ValueError):
        return 30


class TraceStore:
    """Bounded queue plus one writer thread; the connection lives on that thread."""

    def __init__(
        self,
        path_fn: Callable[[], Path] = db_path,
        queue_max: int = 1000,
        on_idle: Optional[Callable[[], None]] = None,
    ) -> None:
        self._path_fn = path_fn
        self._queue: queue.Queue = queue.Queue(maxsize=queue_max)
        self._on_idle = on_idle
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._last_prune = 0.0
        self.ok = True
        self.dropped = 0

    def ensure_thread(self) -> None:
        """Start the daemon writer if it is not running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="SaraTraceWriter", daemon=True
            )
            self._thread.start()

    def submit(self, records: list[dict[str, Any]]) -> None:
        """Queue records without blocking; a full queue drops and counts."""
        if not records or not self.ok:
            return
        self.ensure_thread()
        for rec in records:
            try:
                self._queue.put_nowait(rec)
            except queue.Full:
                with self._lock:
                    self.dropped += 1

    def flush(self, batch: list[dict[str, Any]]) -> None:
        """Insert one batch; on any error persistence is disabled for the session."""
        if not self.ok or not batch:
            return
        try:
            if self._conn is None:
                self._conn = open_db(self._path_fn())
            rows = [tuple(rec[c] for c in COLUMNS) for rec in batch]
            with self._conn:
                self._conn.executemany(_INSERT_SQL, rows)
            self._prune()
        except Exception as exc:
            self.ok = False
            logger.warning("[telemetry] persistence disabled for this session: %s", exc)
            if self._conn is not None:
                with contextlib.suppress(Exception):
                    self._conn.close()
                self._conn = None

    def _prune(self) -> None:
        now = time.time()
        if now - self._last_prune < _PRUNE_EVERY_S:
            return
        self._last_prune = now
        with self._conn:
            self._conn.execute(
                "DELETE FROM turn_trace WHERE ts < ?",
                (now - _retention_days() * 86400.0,),
            )
            self._conn.execute(
                "DELETE FROM turn_trace WHERE turn_id IN ("
                "SELECT turn_id FROM turn_trace ORDER BY ts DESC LIMIT -1 OFFSET ?)",
                (_MAX_DB_ROWS,),
            )

    def _idle(self) -> None:
        if self._on_idle is None:
            return
        try:
            self._on_idle()
        except Exception as exc:
            logger.warning("[telemetry] idle hook failed (ignored): %s", exc)

    def _run(self) -> None:
        stopping = False
        while not stopping:
            try:
                first = self._queue.get(timeout=_FLUSH_S)
            except queue.Empty:
                self._idle()
                continue
            if first is STOP:
                break
            batch = [first]
            deadline = time.monotonic() + _FLUSH_S
            while len(batch) < _BATCH_ROWS:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is STOP:
                    stopping = True
                    break
                batch.append(item)
            self.flush(batch)
            self._idle()