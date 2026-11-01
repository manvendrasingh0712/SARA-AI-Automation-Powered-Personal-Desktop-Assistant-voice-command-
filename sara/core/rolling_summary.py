"""
sara.core.rolling_summary
A short "conversation so far" line for turns that already fell out of the local model's history window (part 3, P8).

The Ollama history is capped (MAX_MEMORY_EXCHANGES) because every extra token costs prefill time. Older turns used to simply
vanish. Here the turns that drop out are collected; every `every` dropped turns ONE background call to the local model folds
them into the existing summary (kept under `max_chars`). The line is appended to the system prompt, and it changes only when
a summary job finishes, so the prompt prefix stays identical between updates (the KV prefix cache keeps hitting) and the
prompt does not grow turn after turn.

`summarize_fn(previous_summary, [(user, assistant), ...]) -> str` is injected (the engine passes a small Ollama call).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, List, Tuple

logger = logging.getLogger("sara.summary")

Pair = Tuple[str, str]


class RollingSummary:
    def __init__(self, summarize_fn: Callable[[str, List[Pair]], str], every: int = 4, max_chars: int = 420,
                 run_async: bool = True):
        self._fn, self.every, self.max_chars, self._async = summarize_fn, max(1, int(every)), int(max_chars), run_async
        self._lock = threading.Lock()
        self._line = ""
        self._pending: List[Pair] = []
        self._busy = False
        self.updates = 0

    @property
    def line(self) -> str:
        return self._line

    def note_dropped(self, user: str, assistant: str) -> None:
        with self._lock:
            self._pending.append((user, assistant))
            if len(self._pending) < self.every or self._busy:
                return
            batch, prev, self._pending, self._busy = self._pending, self._line, [], True
        if self._async:
            threading.Thread(target=self._run, args=(prev, batch), name="rolling-summary", daemon=True).start()
        else:
            self._run(prev, batch)

    def _run(self, prev: str, batch: List[Pair]) -> None:
        try:
            new = " ".join((self._fn(prev, batch) or "").split())[: self.max_chars].strip()
            with self._lock:
                if new:
                    self._line, self.updates = new, self.updates + 1
                else:
                    self._pending = batch + self._pending       # nothing came back: try again with the next batch
        except Exception as exc:  # noqa: BLE001 -- the summary is an optimisation; keep the old line
            logger.debug("summary update failed: %s", exc)
            with self._lock:
                self._pending = batch + self._pending
        finally:
            with self._lock:
                self._busy = False

    def reset(self) -> None:
        with self._lock:
            self._line, self._pending = "", []
