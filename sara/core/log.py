"""
sara.core.log
Non-blocking, redacted logging (part 3, P2).

  * log records go to a queue (QueueHandler) and are written by one listener thread: the caller never waits for the disk or
    the console (measured: a logger call costs a few microseconds on the caller's thread);
  * every record passes RedactFilter first, so an API key / token / OAuth value that ends up in a message never reaches a file;
  * RateLimitFilter drops a message that the SAME call site repeats within `window_s` and later says how many were dropped;
  * a rotating text log (same format as before) and a rotating JSON-lines log (`sara.jsonl`: ts, level, component, turn_id, msg);
  * AsyncStdout wraps sys.stdout so the 500+ remaining `print()` calls stop blocking on a slow console: the text is queued
    and written (redacted) by a background thread.
"""
from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Dict, Optional, Tuple

from sara.core.security.redact import redact


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
            clean = redact(text)
            if clean != text:
                record.msg, record.args = clean, None
        except Exception:  # noqa: BLE001 -- logging must never raise
            pass
        return True


class RateLimitFilter(logging.Filter):
    """Same call site + same message template inside `window_s` -> dropped; the next one that gets through carries a count."""

    def __init__(self, window_s: float = 5.0, min_level: int = logging.WARNING):
        super().__init__()
        self.window_s, self.min_level = window_s, min_level
        self._last: Dict[Tuple[str, int, str], Tuple[float, int]] = {}
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < self.min_level:
            return True
        key = (record.pathname, record.lineno, str(record.msg))
        now = time.monotonic()
        with self._lock:
            last, dropped = self._last.get(key, (-1e9, 0))
            if now - last < self.window_s:
                self._last[key] = (last, dropped + 1)
                return False
            self._last[key] = (now, 0)
            if len(self._last) > 2000:
                self._last.clear()
        if dropped:
            record.msg = f"{record.getMessage()} (+{dropped} similar suppressed)"
            record.args = None
        return True


def _turn_id() -> str:
    try:
        from sara.core.telemetry import current_turn_id

        return str(current_turn_id() or "")
    except Exception:  # noqa: BLE001
        return ""


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {"ts": round(record.created, 3), "level": record.levelname, "component": record.name,
             "turn_id": _turn_id(), "msg": record.getMessage()},
            ensure_ascii=False,
        )


class AsyncStdout:
    """File-like wrapper: write() only enqueues; a thread does the real (redacted) write."""

    def __init__(self, real):
        self._real = real
        self._q: "queue.SimpleQueue[Optional[str]]" = queue.SimpleQueue()
        self._t = threading.Thread(target=self._run, name="stdout-writer", daemon=True)
        self._t.start()
        atexit.register(self.flush_and_stop)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                self._real.write(redact(item))
                self._real.flush()
            except Exception:  # noqa: BLE001 -- closed console, odd encoding: drop the line
                pass

    def write(self, s):
        if s:
            self._q.put(s)
        return len(s or "")

    def flush(self) -> None:
        return None

    def flush_and_stop(self) -> None:
        self._q.put(None)
        self._t.join(timeout=3.0)

    def isatty(self) -> bool:
        return bool(getattr(self._real, "isatty", lambda: False)())

    def __getattr__(self, name):
        return getattr(self._real, name)


_listener: Optional[logging.handlers.QueueListener] = None


def setup(level: int, log_dir: str, text_format: str, date_format: str, *, async_stdout: bool = True) -> None:
    """Install queue logging on the root logger. Called once by logging_config.setup_logging()."""
    global _listener
    root = logging.getLogger()
    root.setLevel(level)
    text_fmt = logging.Formatter(text_format, datefmt=date_format)
    handlers = []
    try:
        os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(os.path.join(log_dir, "sara.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(text_fmt)
        handlers.append(fh)
        jh = RotatingFileHandler(os.path.join(log_dir, "sara.jsonl"), maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
        jh.setFormatter(JsonLinesFormatter())
        handlers.append(jh)
    except Exception as exc:  # noqa: BLE001
        sys.__stderr__.write(f"[log] file logging unavailable: {exc}\n")
    if async_stdout and sys.stdout is not None and not isinstance(sys.stdout, AsyncStdout):
        sys.stdout = AsyncStdout(sys.stdout)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(text_fmt)
    handlers.append(console)

    q: "queue.SimpleQueue" = queue.SimpleQueue()
    qh = logging.handlers.QueueHandler(q)
    qh.addFilter(RedactFilter())
    qh.addFilter(RateLimitFilter())
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(qh)
    _listener = logging.handlers.QueueListener(q, *handlers, respect_handler_level=False)
    _listener.start()
    atexit.register(shutdown)


def shutdown() -> None:
    global _listener
    if _listener is not None:
        try:
            _listener.stop()
        except Exception:  # noqa: BLE001
            pass
        _listener = None
