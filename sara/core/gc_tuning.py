"""
sara.core.gc_tuning
Garbage-collector hygiene around the audio loop (part 3, P17) plus a pause histogram so the effect can be measured.

After start-up warm-up everything long-lived (models, tables, compiled regexes) is moved out of the collector's sight with
gc.freeze() and the generation-0 threshold is raised, so a collection pass does not walk the whole heap while audio frames are
being captured. A full collection is run by hand between turns (idle_collect). `stats()` reports the pauses the collector
actually caused (gc.callbacks), so a change that shows no measurable improvement can be switched off (GC_TUNING_ENABLED=False).
"""
from __future__ import annotations

import gc
import threading
import time
from typing import Dict, List

_pauses_ms: List[float] = []
_lock = threading.Lock()
_t0 = {"start": 0.0}
_installed = {"cb": False, "tuned": False}
_MAX_KEPT = 5000


def _callback(phase: str, info: dict) -> None:
    if phase == "start":
        _t0["start"] = time.perf_counter()
    elif phase == "stop" and _t0["start"]:
        ms = (time.perf_counter() - _t0["start"]) * 1000.0
        with _lock:
            _pauses_ms.append(ms)
            if len(_pauses_ms) > _MAX_KEPT:
                del _pauses_ms[: _MAX_KEPT // 2]


def install_histogram() -> None:
    if not _installed["cb"]:
        gc.callbacks.append(_callback)
        _installed["cb"] = True


def tune_after_warmup(threshold0: int = 50_000, threshold1: int = 20, threshold2: int = 20) -> None:
    """Call once, after the models are loaded."""
    install_histogram()
    gc.collect()
    gc.freeze()
    gc.set_threshold(threshold0, threshold1, threshold2)
    _installed["tuned"] = True


def idle_collect() -> None:
    """A cheap young-generation collection for the gap between turns."""
    gc.collect(1)


def stats() -> Dict[str, float]:
    with _lock:
        data = sorted(_pauses_ms)
    if not data:
        return {"n": 0, "p50_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    return {"n": len(data), "p50_ms": data[len(data) // 2], "p99_ms": data[min(len(data) - 1, int(len(data) * 0.99))], "max_ms": data[-1]}


def reset() -> None:
    with _lock:
        _pauses_ms.clear()


def start_reporter(interval_s: float = 60.0) -> None:
    """Log the pause histogram once a minute (INFO, one line)."""
    import logging

    log = logging.getLogger("sara.gc")

    def _loop() -> None:
        while True:
            time.sleep(interval_s)
            s = stats()
            log.info("[GC] pauses n=%d p50=%.2f ms p99=%.2f ms max=%.2f ms tuned=%s thresholds=%s",
                     s["n"], s["p50_ms"], s["p99_ms"], s["max_ms"], _installed["tuned"], gc.get_threshold())

    threading.Thread(target=_loop, daemon=True, name="gc-report").start()
