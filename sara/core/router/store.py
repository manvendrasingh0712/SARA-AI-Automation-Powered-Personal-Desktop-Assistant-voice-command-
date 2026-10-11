"""
sara.core.router.store
SQLite sink for router decisions: one lock, WAL, a bounded write queue with a
daemon writer. Any DB error disables persistence for the session (logged
once); callers are never affected. Rows hold feature vectors only, never text.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .worker import BackgroundWorker

logger = logging.getLogger("sara.router.store")

DB_FILENAME = "router.sqlite"
_DAY_S = 86400.0
COLUMNS = (
    "ts", "turn_id", "tier", "static_tier", "propensity", "x_json", "quality",
    "latency_ms", "cost", "reward", "flags", "decision_id", "labelled",
)
_INSERT_SQL = (
    f"INSERT INTO router_samples ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(COLUMNS))})"
)
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS router_samples ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, turn_id TEXT, tier TEXT, "
    "static_tier TEXT, propensity REAL, x_json TEXT, quality REAL, latency_ms REAL, "
    "cost REAL, reward REAL, flags TEXT, decision_id TEXT, labelled INTEGER DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS idx_router_samples_ts ON router_samples(ts)",
    "CREATE INDEX IF NOT EXISTS idx_router_samples_decision ON router_samples(decision_id)",
    "CREATE INDEX IF NOT EXISTS idx_router_samples_turn ON router_samples(turn_id)",
)


def _retention_days() -> int:
    try:
        from config import Config

        return max(1, min(3650, int(getattr(Config, "ROUTER_RETENTION_DAYS", 60))))
    except Exception:
        return 60


class RouterStore:
    """router_samples table plus an asynchronous, bounded write path."""

    def __init__(
        self,
        path: Optional[str] = None,
        queue_max: int = 500,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path: Optional[Path] = Path(path) if path else None
        self._clock = clock
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._last_prune = 0.0
        self._worker = BackgroundWorker(
            self._apply, self._maintain, queue_max=queue_max, name="SaraRouterStore"
        )
        self.ok = True

    @staticmethod
    def default_path() -> Path:
        """router.sqlite inside the app-data "data" folder."""
        from config import app_data_subdir

        return Path(app_data_subdir("data")) / DB_FILENAME

    @property
    def path(self) -> Path:
        """Resolved database location (computed on first use)."""
        with self._lock:
            if self._path is None:
                self._path = self.default_path()
            return self._path

    @property
    def dropped(self) -> int:
        """Rows dropped because the write queue was full."""
        return self._worker.dropped

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.commit()
            except Exception:
                conn.close()
                raise
            self._conn = conn
        return self._conn

    def _drop_conn(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _run(self, fn: Callable[[sqlite3.Connection], Any], default: Any) -> Any:
        if not self.ok:
            return default
        try:
            with self._lock:
                return fn(self._db())
        except Exception as exc:
            self.ok = False
            logger.warning("[router] store disabled for this session (%s)", type(exc).__name__)
            with self._lock:
                self._drop_conn()
            return default

    def _rows(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        def _do(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
            cur = conn.execute(sql, params)
            names = [c[0] for c in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

        return self._run(_do, [])

    def _scalar(self, sql: str, params: Tuple[Any, ...], default: Any) -> Any:
        def _do(conn: sqlite3.Connection) -> Any:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else default

        return self._run(_do, default)

    # ── synchronous API (used by the writer thread, tests and readers) ──

    def add_sample(self, **row: Any) -> int:
        """Insert one row now; returns its id, or -1 when persistence is off."""
        values = dict(row)
        values.setdefault("ts", self._clock())
        values.setdefault("labelled", 0)
        params = tuple(values.get(c) for c in COLUMNS)

        def _do(conn: sqlite3.Connection) -> int:
            with conn:
                cur = conn.execute(_INSERT_SQL, params)
            return int(cur.lastrowid)

        return self._run(_do, -1)

    def update_sample(self, decision_id: str, **fields: Any) -> bool:
        """Update columns of the row with this decision id; True when a row changed."""
        cols = [k for k in fields if k in COLUMNS and k != "decision_id"]
        if not cols:
            return False
        sql = f"UPDATE router_samples SET {', '.join(c + '=?' for c in cols)} WHERE decision_id=?"
        params = tuple(fields[c] for c in cols) + (decision_id,)

        def _do(conn: sqlite3.Connection) -> bool:
            with conn:
                cur = conn.execute(sql, params)
            return cur.rowcount > 0

        return bool(self._run(_do, False))

    def get_by_turn(self, turn_id: str) -> Optional[Dict[str, Any]]:
        """Newest row bound to this telemetry turn id."""
        rows = self._rows(
            "SELECT * FROM router_samples WHERE turn_id=? ORDER BY id DESC LIMIT 1", (turn_id,)
        )
        return rows[0] if rows else None

    def recent(self, n: int = 50) -> List[Dict[str, Any]]:
        """Newest rows first."""
        limit = max(1, min(1000, int(n)))
        return self._rows("SELECT * FROM router_samples ORDER BY id DESC LIMIT ?", (limit,))

    def count(self, labelled_only: bool = False) -> int:
        """Number of rows (optionally only those with a learned outcome)."""
        sql = "SELECT COUNT(*) FROM router_samples"
        if labelled_only:
            sql += " WHERE labelled=1"
        return int(self._scalar(sql, (), 0) or 0)

    def usage(self, days: int = 7) -> Dict[str, int]:
        """Rows per executed tier over the last `days`."""
        since = self._clock() - days * _DAY_S
        rows = self._rows(
            "SELECT tier, COUNT(*) AS n FROM router_samples WHERE ts>=? GROUP BY tier", (since,)
        )
        return {str(r["tier"]): int(r["n"]) for r in rows}

    def avg_latency(self, days: int = 7) -> Optional[float]:
        """Mean LLM latency in ms over the last `days`, None when unknown."""
        since = self._clock() - days * _DAY_S
        value = self._scalar(
            "SELECT AVG(latency_ms) FROM router_samples WHERE ts>=? AND latency_ms IS NOT NULL",
            (since,),
            None,
        )
        return None if value is None else float(value)

    def avg_quality(self, applied: bool, n: int = 50) -> Tuple[Optional[float], int]:
        """Mean quality and count of the last `n` labelled rows (learner-applied or not)."""
        cond = (
            "flags LIKE '%applied%'"
            if applied
            else "(flags IS NULL OR flags NOT LIKE '%applied%')"
        )
        rows = self._rows(
            "SELECT quality FROM router_samples WHERE labelled=1 AND quality IS NOT NULL "
            f"AND {cond} ORDER BY id DESC LIMIT ?",
            (max(1, int(n)),),
        )
        values = [float(r["quality"]) for r in rows]
        return (sum(values) / len(values) if values else None), len(values)

    def prune(self, retention_days: int) -> int:
        """Delete rows older than the retention window; returns how many."""
        cutoff = self._clock() - max(1, int(retention_days)) * _DAY_S

        def _do(conn: sqlite3.Connection) -> int:
            with conn:
                cur = conn.execute("DELETE FROM router_samples WHERE ts < ?", (cutoff,))
            return int(cur.rowcount)

        return int(self._run(_do, 0) or 0)

    def reset(self) -> None:
        """Forget every sample (including writes still queued)."""
        self._worker.clear()

        def _do(conn: sqlite3.Connection) -> None:
            with conn:
                conn.execute("DELETE FROM router_samples")

        self._run(_do, None)

    def close(self) -> None:
        """Stop the writer and close the connection (both reopen lazily)."""
        self._worker.stop()
        with self._lock:
            self._drop_conn()

    # ── asynchronous write path (never blocks the caller) ──

    def submit_add(self, row: Dict[str, Any]) -> bool:
        """Queue an insert; False when the bounded queue is full (row dropped, counted)."""
        return self._worker.submit(("add", row))

    def submit_update(self, decision_id: str, **fields: Any) -> bool:
        """Queue an update, applied after any earlier queued insert."""
        return self._worker.submit(("update", (decision_id, fields)))

    def flush(self, timeout: float = 2.0) -> bool:
        """Wait until everything queued so far is written."""
        if not self.ok:
            return True
        return self._worker.drain(timeout)

    def _apply(self, item: Tuple[str, Any]) -> None:
        kind, payload = item
        if kind == "add":
            self.add_sample(**payload)
        elif kind == "update":
            self.update_sample(payload[0], **payload[1])

    def _maintain(self) -> None:
        now = self._clock()
        if now - self._last_prune >= _DAY_S:
            self._last_prune = now
            self.prune(_retention_days())