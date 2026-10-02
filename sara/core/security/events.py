"""
sara.core.security.events

Security audit log: one SQLite table of security-relevant events (injection
suspected, action allowed/confirmed/denied, argument rejected). Same shape
as sara.core.telemetry.store: one bounded queue, one daemon writer thread,
batch inserts, WAL mode, daily prune. Callers are never blocked and never
see an exception; any DB error disables persistence for the session (logged
once).

    log_event("injection", source="web_page", score=0.93, reasons=("override_en",))
    log_decision(decision)            # a policy.Decision
    recent_events(limit=20, kind="policy")

What is stored: short codes and numbers only. Free text (`detail`) is run
through redact(), flattened to one line and cut to 200 characters; content
read from web pages, files or the clipboard is never stored here.

Config: SECURITY_MODE (off -> nothing is logged) and
SECURITY_LOG_RETENTION_DAYS (default 90, clamped to 1..3650).
"""
from __future__ import annotations

import contextlib
import logging
import queue
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .detector import get_setting, security_mode
from .redact import redact

logger = logging.getLogger(__name__)

DB_FILENAME = "security_events.sqlite"
STOP = object()

COLUMNS = (
    "ts", "kind", "intent", "tier", "decision", "source", "score",
    "reasons", "tainted", "turn", "detail",
)
_INSERT_SQL = (
    f"INSERT INTO security_events ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(COLUMNS))})"
)
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS security_events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT, "
    "intent TEXT, tier INTEGER, decision TEXT, source TEXT, score REAL, "
    "reasons TEXT, tainted INTEGER DEFAULT 0, turn INTEGER, detail TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_security_events_ts ON security_events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_security_events_kind ON security_events(kind)",
)

_BATCH_ROWS = 50
_FLUSH_S = 0.5
_MAX_DB_ROWS = 50000
_PRUNE_EVERY_S = 86400.0

_DETAIL_MAX = 200
_REASONS_MAX = 120
_FIELD_MAX = 60
_SAFE_CODE_RE = re.compile(r"[^A-Za-z0-9_,:.\-]")


# ── paths and settings ──────────────────────────────────────────────────────

def db_path() -> Path:
    """Location of the audit DB inside the app-data "data" folder."""
    from config import app_data_subdir

    return Path(app_data_subdir("data")) / DB_FILENAME


def _retention_days(cfg: Any = None) -> int:
    try:
        days = int(get_setting("SECURITY_LOG_RETENTION_DAYS", 90, cfg))
    except (TypeError, ValueError):
        return 90
    return max(1, min(3650, days))


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


# ── record building ─────────────────────────────────────────────────────────

def _code(value: Any, limit: int = _FIELD_MAX) -> str:
    return _SAFE_CODE_RE.sub("", str(value or ""))[:limit]


def _detail(value: Any) -> str:
    text = redact(str(value or ""))
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:_DETAIL_MAX]


def _current_turn() -> int:
    try:
        from .taint import _current_gen

        return int(_current_gen())
    except Exception:  # noqa: BLE001
        return 0


def _current_tainted() -> bool:
    try:
        from . import taint

        return bool(taint.turn_tainted())
    except Exception:  # noqa: BLE001
        return False


def build_record(
    kind: str,
    *,
    intent: str = "",
    tier: Optional[int] = None,
    decision: str = "",
    source: str = "",
    score: Optional[float] = None,
    reasons: Iterable[str] = (),
    tainted: Optional[bool] = None,
    turn: Optional[int] = None,
    detail: str = "",
    ts: Optional[float] = None,
) -> Dict[str, Any]:
    if isinstance(reasons, str):
        reasons = (reasons,)
    reason_text = ",".join(_code(r, 40) for r in list(reasons or ())[:10] if r)
    return {
        "ts": float(ts) if ts is not None else time.time(),
        "kind": _code(kind) or "event",
        "intent": _code(intent),
        "tier": int(tier) if tier is not None else None,
        "decision": _code(decision),
        "source": _code(source),
        "score": round(float(score), 3) if score is not None else None,
        "reasons": reason_text[:_REASONS_MAX],
        "tainted": 1 if (tainted if tainted is not None else _current_tainted()) else 0,
        "turn": int(turn) if turn is not None else _current_turn(),
        "detail": _detail(detail),
    }


# ── store ───────────────────────────────────────────────────────────────────

class SecurityEventStore:
    """Bounded queue plus one writer thread; the connection lives on that thread."""

    def __init__(
        self,
        path_fn: Callable[[], Path] = db_path,
        queue_max: int = 1000,
    ) -> None:
        self._path_fn = path_fn
        self._queue: queue.Queue = queue.Queue(maxsize=queue_max)
        self._lock = threading.Lock()
        self._cv = threading.Condition()
        self._pending = 0
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._last_prune = 0.0
        self.ok = True
        self.dropped = 0

    # -- writing

    def ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="SaraSecurityLog", daemon=True
            )
            self._thread.start()

    def submit(self, record: Dict[str, Any]) -> bool:
        """Queue one record without blocking. False if dropped or disabled."""
        if not self.ok:
            return False
        self.ensure_thread()
        with self._cv:
            self._pending += 1
        try:
            self._queue.put_nowait(record)
            return True
        except queue.Full:
            with self._cv:
                self._pending -= 1
                self._cv.notify_all()
            with self._lock:
                self.dropped += 1
            return False

    def flush_sync(self, timeout: float = 2.0) -> bool:
        """Wait until everything queued so far is written (or dropped).
        Mainly for tests and clean shutdown. True if the queue drained."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._pending > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(remaining)
        return True

    def close(self, timeout: float = 2.0) -> None:
        """Flush, stop the writer and close the connection."""
        self.flush_sync(timeout)
        thread = self._thread
        if thread is not None and thread.is_alive():
            with contextlib.suppress(queue.Full):
                self._queue.put(STOP, timeout=0.5)
            thread.join(timeout)

    def _flush(self, batch: List[Dict[str, Any]]) -> None:
        if not self.ok or not batch:
            return
        try:
            if self._conn is None:
                self._conn = open_db(self._path_fn())
            rows = [tuple(rec[c] for c in COLUMNS) for rec in batch]
            with self._conn:
                self._conn.executemany(_INSERT_SQL, rows)
            self._prune()
        except Exception as exc:  # noqa: BLE001
            self.ok = False
            logger.warning("[security] audit log disabled for this session: %s", exc)
            self._close_conn()

    def _close_conn(self) -> None:
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
                "DELETE FROM security_events WHERE ts < ?",
                (now - _retention_days() * 86400.0,),
            )
            self._conn.execute(
                "DELETE FROM security_events WHERE id IN ("
                "SELECT id FROM security_events ORDER BY id DESC LIMIT -1 OFFSET ?)",
                (_MAX_DB_ROWS,),
            )

    def _done(self, count: int) -> None:
        with self._cv:
            self._pending = max(0, self._pending - count)
            self._cv.notify_all()

    def _run(self) -> None:
        stopping = False
        while not stopping:
            try:
                first = self._queue.get(timeout=_FLUSH_S)
            except queue.Empty:
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
            try:
                self._flush(batch)
            finally:
                self._done(len(batch))
        self._close_conn()

    # -- reading

    def recent(
        self,
        limit: int = 50,
        kind: Optional[str] = None,
        since: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Newest-first events. Returns [] if the DB is missing or unreadable."""
        try:
            path = self._path_fn()
            if not Path(path).exists():
                return []
            limit = max(1, min(500, int(limit)))
            clauses, params = [], []
            if kind:
                clauses.append("kind = ?")
                params.append(str(kind))
            if since is not None:
                clauses.append("ts >= ?")
                params.append(float(since))
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = (
                f"SELECT id, {', '.join(COLUMNS)} FROM security_events"
                f"{where} ORDER BY id DESC LIMIT ?"
            )
            conn = sqlite3.connect(str(path), timeout=2.0)
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, (*params, limit)).fetchall()
            finally:
                conn.close()
            out = []
            for row in rows:
                item = dict(row)
                item["tainted"] = bool(item.get("tainted"))
                out.append(item)
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("[security] could not read audit log: %s", exc)
            return []


# ── module-level convenience API ────────────────────────────────────────────

_STORE: Optional[SecurityEventStore] = None
_STORE_LOCK = threading.Lock()


def get_store() -> SecurityEventStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = SecurityEventStore()
        return _STORE


def log_event(kind: str, *, cfg: Any = None, **fields: Any) -> bool:
    """Record one event. Never raises, never blocks. Returns True if queued.
    Does nothing when SECURITY_MODE=off. Keyword fields: see build_record()."""
    try:
        if security_mode(cfg) == "off":
            return False
        return get_store().submit(build_record(kind, **fields))
    except Exception:  # noqa: BLE001
        logger.debug("[security] log_event failed", exc_info=True)
        return False


def log_decision(decision: Any, *, source: str = "dispatcher", detail: str = "", cfg: Any = None) -> bool:
    """Record a policy.Decision (duck-typed: intent, tier, action, reason, context)."""
    try:
        return log_event(
            "policy",
            cfg=cfg,
            intent=getattr(decision, "intent", ""),
            tier=getattr(decision, "tier", None),
            decision=getattr(decision, "action", ""),
            source=source,
            reasons=(getattr(decision, "reason", ""),),
            tainted=getattr(decision, "context", "clean") != "clean",
            detail=detail,
        )
    except Exception:  # noqa: BLE001
        logger.debug("[security] log_decision failed", exc_info=True)
        return False


def recent_events(limit: int = 50, kind: Optional[str] = None, since: Optional[float] = None) -> List[Dict[str, Any]]:
    return get_store().recent(limit=limit, kind=kind, since=since)


def flush(timeout: float = 2.0) -> bool:
    return get_store().flush_sync(timeout)


def shutdown(timeout: float = 2.0) -> None:
    """Flush and stop the writer (call on application exit)."""
    global _STORE
    with _STORE_LOCK:
        store, _STORE = _STORE, None
    if store is not None:
        store.close(timeout)
