"""
sara.orchestrator.db_writer
AsyncDBWriter -- fire-and-forget conversation-log writer so the hot
user/assistant exchange path never blocks on disk I/O.
"""

import time
import queue
import logging
import threading

from sara.core.memory import PreferencesDB

from sara.orchestrator._constants import (
    _DB_WRITER_IDLE_POLL_S,
    _THREAD_ERROR_BACKOFF_S,
)

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

# Grace period given to the writer thread to drain and exit on shutdown.
_SHUTDOWN_JOIN_TIMEOUT_S = 2.0


# ----------------------------------------------------------------------------
# Background DB writer
# ----------------------------------------------------------------------------


class AsyncDBWriter:
    __slots__ = ("_db", "_q", "_stop", "_thread")

    _BACKLOG_WARN_THRESHOLD = 500

    def __init__(self, db: PreferencesDB):
        self._db = db
        self._q: "queue.SimpleQueue" = queue.SimpleQueue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        last_warn_at = 0.0
        while not self._stop.is_set():
            try:
                try:
                    role, content = self._q.get(timeout=_DB_WRITER_IDLE_POLL_S)
                except queue.Empty:
                    continue
                try:
                    self._db.log_message(role, content)
                except Exception as e:
                    logger.exception(f"[AsyncDBWriter] log_message failed: {e}")
                    backlog = self._q.qsize()
                    if backlog > self._BACKLOG_WARN_THRESHOLD:
                        now = time.monotonic()
                        if now - last_warn_at > 30.0:
                            logger.warning(
                                f"[AsyncDBWriter] DB write backlog growing "
                                f"(~{backlog} pending) — is the DB writable?"
                            )
                            last_warn_at = now
            except Exception as e:
                logger.exception(f"[AsyncDBWriter] run loop error (continuing): {e}")
                time.sleep(_THREAD_ERROR_BACKOFF_S)

    def log_message(self, role: str, content: str) -> None:
        if not role or not str(role).strip():
            return
        if not content or not str(content).strip():
            return
        self._q.put((role, content))

    def _flush_pending(self) -> None:
        """Best-effort synchronous flush of whatever is still queued at
        shutdown time -- without this, anything log_message() enqueued
        just before shutdown() could be silently dropped if the writer
        thread hadn't dequeued it yet (queue.SimpleQueue has no
        persistence and the thread is a daemon)."""
        while True:
            try:
                role, content = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                self._db.log_message(role, content)
            except Exception as e:
                logger.exception(f"[AsyncDBWriter] flush-on-shutdown write failed: {e}")

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=_SHUTDOWN_JOIN_TIMEOUT_S)
        self._flush_pending()
