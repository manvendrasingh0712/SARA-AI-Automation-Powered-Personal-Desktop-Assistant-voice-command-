"""
sara/tools/reminder_composer.py
Decides what Sara says when a reminder comes due.

    composer = ReminderComposer(Config, breaker_getter=...)
    wait = composer.start("sleep")   # returns at once; LLM runs in the background
    ...play the alarm beep meanwhile...
    result = wait()                  # ComposeResult, never raises

The LLM wording is optional polish. Whatever goes wrong (disabled, slow,
failed, invalid output) the result is the deterministic template, so a due
reminder is always spoken.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from sara.core.llm.provider_runtime import generate_short
from sara.tools.reminder_context import (
    build_prompt,
    classify,
    due_message,
    effective_policy,
    time_bucket,
    validate_text,
)

_WAIT_GRACE_S = 1.0  # extra time the waiter allows beyond the LLM budget


@dataclass(frozen=True)
class ComposeResult:
    text: str
    source: str       # "llm" | "template" | "default"
    provider: str     # "gemini" | "ollama" | "none"
    failure: str      # "success", a provider failure kind, or why the LLM was skipped
    latency_ms: int
    category: str
    level: str

    def describe(self) -> str:
        return (
            f"[Reminder] wording={self.source} provider={self.provider} "
            f"result={self.failure} {self.latency_ms}ms "
            f"category={self.category} level={self.level}"
        )


class ReminderComposer:
    def __init__(
        self,
        cfg: Any,
        *,
        breaker_getter: Optional[Callable[[], Any]] = None,
        generate: Callable[..., Any] = generate_short,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._cfg = cfg
        self._breaker_getter = breaker_getter
        self._generate = generate
        self._clock = clock

    def start(self, message: str) -> Callable[[], ComposeResult]:
        started = time.monotonic()
        default_text = f"Reminder: {message}"
        try:
            now = self._clock()
            intent = classify(message)
            bucket = time_bucket(now)
            policy = effective_policy(intent, bucket)
            template = due_message(message, now) or default_text
        except Exception:  # noqa: BLE001
            result = ComposeResult(default_text, "default", "none", "error", 0, "generic", "normal")
            return lambda: result

        def _done(text: str, source: str, provider: str, failure: str) -> ComposeResult:
            return ComposeResult(
                text=text,
                source=source,
                provider=provider,
                failure=failure,
                latency_ms=int((time.monotonic() - started) * 1000),
                category=intent.category,
                level=intent.level,
            )

        if not intent.subject:
            result = _done(default_text, "default", "none", "empty")
            return lambda: result
        if not getattr(self._cfg, "CONTEXTUAL_REMINDER_LLM", True):
            result = _done(template, "template", "none", "disabled")
            return lambda: result

        try:
            timeout_s = float(getattr(self._cfg, "CONTEXTUAL_REMINDER_LLM_TIMEOUT_S", 4.0))
        except (TypeError, ValueError):
            timeout_s = 4.0
        timeout_s = max(0.5, timeout_s)

        box: dict = {}
        finished = threading.Event()

        def _run() -> None:
            try:
                system, user = build_prompt(intent, bucket, now, policy)
                breaker = self._breaker_getter() if self._breaker_getter else None
                box["gen"] = self._generate(
                    user,
                    system,
                    self._cfg,
                    timeout_s=timeout_s,
                    max_tokens=80,
                    total_budget_s=timeout_s,
                    breaker=breaker,
                )
            except Exception as e:  # noqa: BLE001
                box["error"] = e
            finally:
                finished.set()

        threading.Thread(target=_run, daemon=True, name="sara-reminder-compose").start()

        def _wait() -> ComposeResult:
            try:
                finished.wait(timeout_s + _WAIT_GRACE_S)
                gen = box.get("gen")
                if gen is not None and getattr(gen, "text", None):
                    cleaned = validate_text(gen.text, policy.max_words)
                    if cleaned:
                        return _done(cleaned, "llm", gen.provider, "success")
                    return _done(template, "template", gen.provider, "invalid_output")
                if gen is not None:
                    return _done(template, "template", getattr(gen, "provider", "none"), gen.failure)
                if "error" in box:
                    return _done(template, "template", "none", "error")
                return _done(template, "template", "none", "timeout")
            except Exception:  # noqa: BLE001
                return _done(template, "template", "none", "error")

        return _wait