"""
sara.orchestrator.network_utils
Bounded-timeout wrapper for network-bound tool calls (search/weather/news/
URL fetch), so a slow network can never hang the conversation loop.
"""

import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from sara.orchestrator._constants import _NETWORK_TOOL_TIMEOUT_S
from sara.orchestrator.state import TURN_STATE

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

# ----------------------------------------------------------------------------
# Network-bound tool call wrapper
# ----------------------------------------------------------------------------

_NETWORK_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sara-net")

# CIRCUIT BREAKER: if a given tool times out / errors this many times in a
# row, we stop even attempting it for a cooldown window and return an
# immediate friendly message instead. Protects the voice loop from
# repeatedly stalling for the full timeout on a tool that's clearly down
# (dead network, blocked API, etc.) -- one bad call is a fluke, three in a
# row is a real outage.
#
# KNOWN LIMITATION: future.cancel() below only succeeds for a job that
# hasn't started running yet. Once a submitted call is actually executing
# in one of _NETWORK_EXECUTOR's threads, a timeout does NOT stop that
# thread -- it keeps running (or hangs) in the background and stays
# occupied until the underlying call itself returns or raises. The
# breaker stops new dispatches to a repeatedly-failing tool, but it does
# not reclaim threads already stuck on a prior call; with only 4 workers,
# a handful of truly-hung calls (e.g. a socket read with no internal
# timeout) can starve every other network-bound tool app-wide. If that
# turns out to matter in practice, the real fix is a timeout inside each
# wrapped `fn` itself (e.g. `requests` timeouts), not a bigger pool.
_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_COOLDOWN_S = 30.0

_breaker_failure_counts: dict[str, int] = {}
_breaker_open_until: dict[str, float] = {}
_breaker_lock = threading.Lock()


class _TurnCancelled(Exception):
    """Raised internally when the current turn was cancelled via Stop."""


def _wait_cancellable(future, timeout: float, cancel_event):
    """
    Same semantics as future.result(timeout=timeout), but polls in short
    slices so a Stop press aborts the wait within ~0.1s. The overall
    timeout still applies exactly as before.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FutureTimeoutError()
        try:
            return future.result(timeout=min(0.1, remaining))
        except FutureTimeoutError:
            if future.done():
                raise  # the wrapped fn itself raised a timeout; not a poll miss
            if cancel_event.is_set():
                raise _TurnCancelled()


def _call_with_timeout(
    fn, *args, timeout: float = _NETWORK_TOOL_TIMEOUT_S, tool_name: str = None, **kwargs
):
    name = tool_name or getattr(fn, "__name__", "unknown_tool")

    with _breaker_lock:
        open_until = _breaker_open_until.get(name, 0.0)
        if time.time() < open_until:
            remaining = int(open_until - time.time())
            return (
                f"Sorry, that's not responding right now -- give it about "
                f"{max(remaining, 1)} seconds and try again."
            )

    cancel_event = TURN_STATE.current_event()
    if cancel_event.is_set():
        return "Okay, stopped."

    future = _NETWORK_EXECUTOR.submit(fn, *args, **kwargs)
    try:
        result = _wait_cancellable(future, timeout, cancel_event)
    except _TurnCancelled:
        future.cancel()  # no-op if already running; see KNOWN LIMITATION
        return "Okay, stopped."  # a Stop is not a tool failure: no breaker hit
    except FutureTimeoutError:
        future.cancel()  # no-op if fn is already running; see note above
        _record_breaker_failure(name)
        return (
            "Sorry, that's taking longer than expected. Please try again in a moment."
        )
    except Exception as e:
        _record_breaker_failure(name)
        return f"Sorry, I ran into a problem: {e}"
    else:
        _record_breaker_success(name)
        return result


def _record_breaker_failure(name: str) -> None:
    with _breaker_lock:
        count = _breaker_failure_counts.get(name, 0) + 1
        _breaker_failure_counts[name] = count
        if count >= _BREAKER_FAILURE_THRESHOLD:
            _breaker_open_until[name] = time.time() + _BREAKER_COOLDOWN_S
            _breaker_failure_counts[name] = 0
            logger.warning(
                f"[CircuitBreaker] '{name}' tripped after {_BREAKER_FAILURE_THRESHOLD} "
                f"consecutive failures, cooling down for {_BREAKER_COOLDOWN_S}s"
            )


def _record_breaker_success(name: str) -> None:
    with _breaker_lock:
        _breaker_failure_counts[name] = 0
        _breaker_open_until.pop(name, None)


def _shutdown_network_executor() -> None:
    try:
        _NETWORK_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        _NETWORK_EXECUTOR.shutdown(wait=False)
    except Exception as e:
        logger.error(f"[Shutdown] Failed to shut down network executor: {e}")
