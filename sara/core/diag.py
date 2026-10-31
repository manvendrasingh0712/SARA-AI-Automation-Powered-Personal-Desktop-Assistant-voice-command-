"""
sara.core.diag
One place for "this error is deliberately not fatal, but it should not be invisible either".

    except OSError as exc:
        swallowed(logger, "stt.close_stream", exc)

Logs at DEBUG, at most once per `where` every 30 s (a hot loop cannot flood the log), and counts every occurrence
so `counts()` shows which best-effort paths are failing and how often.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from typing import Dict, Optional

_RATE_LIMIT_S = 30.0
_lock = threading.Lock()
_counts: Counter = Counter()
_last_logged: Dict[str, float] = {}
_fallback_logger = logging.getLogger("sara.swallowed")


def swallowed(logger: Optional[logging.Logger], where: str, exc: BaseException) -> None:
    """Record a deliberately swallowed exception. Never raises."""
    try:
        now = time.monotonic()
        with _lock:
            _counts[where] += 1
            n = _counts[where]
            due = now - _last_logged.get(where, -_RATE_LIMIT_S) >= _RATE_LIMIT_S
            if due:
                _last_logged[where] = now
        if due:
            (logger or _fallback_logger).debug(
                "[swallowed] %s: %s: %s (seen %d time%s)", where, type(exc).__name__, exc, n, "" if n == 1 else "s"
            )
        try:  # telemetry counter, if the recorder is running
            from sara.core.telemetry import annotate

            annotate(**{f"swallowed_{where.split(':')[0].replace('.', '_')}": n})
        except Exception:  # noqa: BLE001 -- telemetry is optional; this must not recurse into swallowed()
            pass
    except Exception:  # noqa: BLE001
        pass


def counts() -> Dict[str, int]:
    with _lock:
        return dict(_counts)


def reset() -> None:
    with _lock:
        _counts.clear()
        _last_logged.clear()
