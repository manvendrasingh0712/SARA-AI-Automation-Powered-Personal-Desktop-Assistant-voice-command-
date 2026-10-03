"""
sara.core.security.events
Security audit log: bounded queue, one daemon writer thread, SQLite (WAL) with a
daily prune, plus a 200-row in-memory ring that keeps working if the DB fails.
Only redacted, truncated metadata is stored, never raw untrusted text.
"""
from __future__ import annotations

import contextlib
import logging
import queue
import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from config import Config, app_data_subdir

try:
    from sara.core.security.redact import redact as _redact
except ImportError:  # redact.py is delivered by another task
    _redact = None

logger = logging.getLogger(__name__)

DB_FILENAME = "security.sqlite"
KINDS = frozenset(
    {"injection_detected", "blocked", "confirm_asked", "confirmed",
     "denied_by_user", "arg_rejected", "redacted"}
)
BLOCKED_KINDS = ("blocked", "arg_rejected", "injection_detected")

_DETAIL_MAX = 300
_RING_MAX = 200
_QUEUE_MAX = 500
_BATCH_ROWS = 50
_POLL_S = 0.2
_PRUNE_EVERY_S = 86400.0
_MAX_DB_ROWS = 100000
_RING_ID_BASE = -(2 ** 31)
_COLUMNS = ("ts", "turn_id", "kind", "tool", "tier", "origin", "source", "score", "details")
_OUT_KEYS = ("id",) + _COLUMNS
_INSERT_SQL = (
    f"INSERT INTO security_event ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS security_event ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, turn_id TEXT, kind TEXT, "
    "tool TEXT, tier INTEGER, origin TEXT, source TEXT, score REAL, details TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_security_event_ts ON security_event(ts)",
    "CREATE INDEX IF NOT EXISTS idx_security_event_kind ON security_event(kind)",
)
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def default_path() -> Path:
    """Location of security.sqlite inside the app-data "data" folder."""
    return Path(app_data_subdir("data")) / DB_FILENAME


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the audit DB in WAL mode."""
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
        return max(1, min(3650, int(getattr(Config, "SECURITY_LOG_RETENTION_DAYS", 90))))
    except (TypeError, ValueError):
        return 90


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    try:
        return _CTRL_RE.sub(" ", str(value))[:limit]
    except Exception:
        return ""


def _num(value: Any, cast: Callable[[Any], Any]) -> Any:
    if value is None:
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _details(value: Any) -> str:
    fn = _redact
    if fn is None or not value:
        return ""
    try:
        return _text(fn(str(value)[:2000]), _DETAIL_MAX)
    except Exception:
        return ""


def make_row(
    kind: str,
    tool: str = "",
    tier: Optional[int] = None,
    origin: str = "",
    source: str = "",
    score: Optional[float] = None,
    details: str = "",
    turn_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build a sanitised event row; unknown kinds are stored as "other"."""
    return {
        "id": 0,
        "ts": time.time(),
        "turn_id": _text(turn_id, 64),
        "kind": kind if isinstance(kind, str) and kind in KINDS else "other",
        "tool": _text(tool, 80),
        "tier": _num(tier, int),
        "origin": _text(origin, 40),
        "source": _text(source, 120),
        "score": _num(score, float),
        "details": _details(details),
        "_db": False,
    }


def _key(row: dict[str, Any]) -> tuple:
    return tuple(row[k] for k in _COLUMNS)


class EventStore:
    """Ring buffer + bounded queue + one writer thread owning the DB connection."""

    def __init__(
        self, path_fn: Callable[[], Path] = default_path, queue_max: int = _QUEUE_MAX
    ) -> None:
        self._path_fn = path_fn
        self._queue: queue.Queue = queue.Queue(maxsize=queue_max)
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._ring: deque = deque(maxlen=_RING_MAX)
        self._pending = 0
        self._seq = 0
        self._last_prune = 0.0
        self.ok = True
        self.dropped = 0

    def ensure_thread(self) -> None:
        """Start the daemon writer if it is not running."""
        with self._lock:
            if self._stop.is_set() or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = threading.Thread(
                target=self._run, name="SaraSecurityWriter", daemon=True
            )
            self._thread.start()

    def submit(self, row: dict[str, Any]) -> None:
        """Keep the row in the ring and queue it for the DB without blocking."""
        with self._lock:
            self._seq += 1
            row["id"] = _RING_ID_BASE + self._seq
            self._ring.append(row)
            persist = self.ok and not self._stop.is_set()
        if not persist:
            return
        self.ensure_thread()
        with self._cond:
            self._pending += 1
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            with self._cond:
                self._pending -= 1
            with self._lock:
                self.dropped += 1

    def flush(self, timeout: float) -> None:
        """Wait until queued rows are written (or the timeout expires)."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._pending > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._cond.wait(remaining)

    def stop(self) -> None:
        """Stop the writer (used by configure())."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def ring_rows(self) -> list[dict[str, Any]]:
        """Snapshot of the in-memory ring (oldest first)."""
        with self._lock:
            return [dict(r) for r in self._ring]

    def query(self, sql: str, args: tuple) -> Optional[list[tuple]]:
        """Run a read query on a short-lived connection; None when the DB is unusable."""
        try:
            path = Path(self._path_fn())
            if not path.exists():
                return None
            with contextlib.closing(sqlite3.connect(str(path), timeout=2.0)) as conn:
                return conn.execute(sql, args).fetchall()
        except Exception:
            return None

    def _close(self) -> None:
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
                "DELETE FROM security_event WHERE ts < ?",
                (now - _retention_days() * 86400.0,),
            )
            self._conn.execute(
                "DELETE FROM security_event WHERE id IN ("
                "SELECT id FROM security_event ORDER BY id DESC LIMIT -1 OFFSET ?)",
                (_MAX_DB_ROWS,),
            )

    def _write(self, batch: list[dict[str, Any]]) -> None:
        try:
            if self.ok:
                if self._conn is None:
                    self._conn = open_db(Path(self._path_fn()))
                with self._conn:
                    self._conn.executemany(
                        _INSERT_SQL, [tuple(r[c] for c in _COLUMNS) for r in batch]
                    )
                for row in batch:
                    row["_db"] = True
                self._prune()
        except Exception as exc:
            self.ok = False
            logger.warning(
                "[security] audit log persistence disabled for this session: %s",
                type(exc).__name__,
            )
            self._close()
        finally:
            with self._cond:
                self._pending -= len(batch)
                self._cond.notify_all()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    batch = [self._queue.get(timeout=_POLL_S)]
                except queue.Empty:
                    continue
                while len(batch) < _BATCH_ROWS:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                self._write(batch)
        finally:
            self._close()


_STORE = EventStore()
_STORE_LOCK = threading.Lock()


def log_event(
    kind: str,
    *,
    tool: str = "",
    tier: Optional[int] = None,
    origin: str = "",
    source: str = "",
    score: Optional[float] = None,
    details: str = "",
    turn_id: Optional[str] = None,
) -> None:
    """Record a security event; never raises, never blocks."""
    try:
        _STORE.submit(make_row(kind, tool, tier, origin, source, score, details, turn_id))
    except Exception:
        return


def get_events(limit: int = 50, kind: Optional[str] = None) -> list[dict[str, Any]]:
    """Newest-first events from the DB merged with the in-memory ring."""
    try:
        store = _STORE
        limit = max(1, min(500, int(limit)))
        want = None if kind is None else (kind if kind in KINDS else "other")
        sql = "SELECT " + ", ".join(_OUT_KEYS) + " FROM security_event"
        args: tuple = ()
        if want is not None:
            sql += " WHERE kind = ?"
            args = (want,)
        ring = [r for r in store.ring_rows() if want is None or r["kind"] == want]
        found = store.query(sql + " ORDER BY ts DESC, id DESC LIMIT ?", args + (limit,)) or []
        merged = [dict(zip(_OUT_KEYS, row)) for row in found]
        seen = {_key(r) for r in merged}
        merged += [{k: r[k] for k in _OUT_KEYS} for r in ring if _key(r) not in seen]
        merged.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
        return merged[:limit]
    except Exception:
        return []


def get_counts(days: int = 7) -> dict[str, Any]:
    """Event counts for the last `days` days; blocked = blocked + arg_rejected + injection."""
    try:
        store = _STORE
        since = time.time() - max(1, min(3650, int(days))) * 86400.0
        found = store.query(
            "SELECT kind, COUNT(*) FROM security_event WHERE ts >= ? GROUP BY kind", (since,)
        )
        by_kind = {k: int(n) for k, n in (found or [])}
        for row in store.ring_rows():
            if not row["_db"] and row["ts"] >= since:
                by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        blocked = sum(by_kind.get(k, 0) for k in BLOCKED_KINDS)
        return {"total": sum(by_kind.values()), "blocked": blocked, "by_kind": by_kind}
    except Exception:
        return {"total": 0, "blocked": 0, "by_kind": {}}


def flush(timeout: float = 2.0) -> None:
    """Wait (bounded) until queued events are persisted."""
    try:
        _STORE.flush(timeout)
    except Exception:
        return


def configure(db_path: "Path | None" = None) -> None:
    """Reset queue, ring and writer; point to db_path (tests) or the default location."""
    global _STORE
    path_fn: Callable[[], Path] = default_path if db_path is None else (lambda: Path(db_path))
    with _STORE_LOCK:
        old, _STORE = _STORE, EventStore(path_fn=path_fn)
    old.stop()