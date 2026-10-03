"""
sara.core.llm.provider_runtime
Stateless, short, best-effort text generation with provider fallback.

    generate_short(prompt, system, cfg, ...) -> GenResult

Order: primary backend (Config.LLM_BACKEND), then local Ollama if the primary
is Gemini and LLM_FALLBACK_ENABLED is on. It never raises, never streams, and
never touches conversation history. When nothing works, GenResult.text is None
and the caller uses its own deterministic template.

An optional `breaker` (anything with active() and record(ok)) lets the caller
share SaraLLM's primary-backend circuit breaker, so a Gemini outage seen by
chat also skips Gemini here (and the other way round).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from sara.core.llm.clients import (
    _build_gemini_generate_config,
    _get_gemini_client,
    _get_ollama_client,
)

# Failure kinds (strings, so they log and compare easily).
SUCCESS = "success"
EMPTY = "empty"
TIMEOUT = "timeout"
QUOTA = "quota"
RATE_LIMIT = "rate_limit"
AUTH = "auth"
NETWORK = "network"
SERVER = "server"
INVALID = "invalid"
UNAVAILABLE = "unavailable"

# Failures that say "this provider is unhealthy" -> count against the breaker.
# TIMEOUT is left out on purpose: our short deadline says nothing about whether
# chat (which waits much longer) can still use the primary backend.
_BREAKER_KINDS = frozenset({QUOTA, RATE_LIMIT, AUTH, NETWORK, SERVER, UNAVAILABLE})


@dataclass(frozen=True)
class GenResult:
    text: Optional[str]
    provider: str                      # provider that produced text, else "none"
    failure: str                       # SUCCESS, or the kind of the LAST failure
    attempts: Tuple[Tuple[str, str], ...]  # ((provider, kind), ...) in order tried
    latency_ms: int


# ----------------------------------------------------------------------
# Failure classification: status code first, then exception type, then text
# ----------------------------------------------------------------------


def _status_of(exc: BaseException) -> Optional[int]:
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def classify_failure(exc: BaseException) -> str:
    message = str(exc).lower()
    status = _status_of(exc)

    if status is not None:
        if status == 429:
            if "quota" in message or "exceeded your current" in message or "billing" in message:
                return QUOTA
            return RATE_LIMIT
        if status in (401, 403):
            return AUTH
        if status == 408:
            return TIMEOUT
        if status in (400, 404, 422):
            return AUTH if "api key" in message else INVALID
        if 500 <= status <= 599:
            return SERVER

    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name or "timedout" in name:
        return TIMEOUT
    if isinstance(exc, (ConnectionError, OSError)) or "connect" in name or "network" in name:
        return NETWORK

    if "timed out" in message or "timeout" in message or "deadline" in message:
        return TIMEOUT
    if "quota" in message or "resource_exhausted" in message:
        return QUOTA
    if "rate limit" in message or "too many requests" in message:
        return RATE_LIMIT
    if "api key" in message or "unauthorized" in message or "permission" in message:
        return AUTH
    if any(w in message for w in ("connection", "unreachable", "refused", "name or service", "getaddrinfo")):
        return NETWORK
    return UNAVAILABLE


# ----------------------------------------------------------------------
# One call to one provider, with a hard deadline
# ----------------------------------------------------------------------


def _run_with_deadline(fn: Callable[[], Any], timeout_s: float) -> Any:
    """Runs fn in a daemon thread; raises TimeoutError if it takes too long."""
    box: dict = {}

    def _target() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller
            box["error"] = e

    worker = threading.Thread(target=_target, daemon=True, name="sara-short-gen")
    worker.start()
    worker.join(max(0.1, timeout_s))
    if worker.is_alive():
        raise TimeoutError(f"generation exceeded {timeout_s:.1f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _gemini_call(cfg: Any, prompt: str, system: str, timeout_s: float, max_tokens: int) -> str:
    client = _get_gemini_client(cfg)
    if client is None:
        raise ConnectionError("Gemini client unavailable")
    from google.genai import types

    model = getattr(cfg, "GEMINI_FAST_MODEL", "") or getattr(cfg, "GEMINI_MODEL", "gemini-2.5-flash")
    gen_config = _build_gemini_generate_config(
        types,
        timeout_s,
        system_instruction=system,
        temperature=0.6,
        max_output_tokens=max_tokens,
    )
    resp = client.models.generate_content(
        model=model,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=gen_config,
    )
    return (resp.text or "").strip()


def _ollama_call(cfg: Any, prompt: str, system: str, timeout_s: float, max_tokens: int) -> str:
    client = _get_ollama_client(cfg)
    if client is None:
        raise ConnectionError("Ollama client unavailable")
    model = getattr(cfg, "OLLAMA_FAST_MODEL", "") or getattr(cfg, "OLLAMA_MODEL", "llama3")
    resp = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        think=False,
        options={"temperature": 0.6, "num_predict": max_tokens},
        keep_alive=getattr(cfg, "OLLAMA_KEEP_ALIVE", "5m"),
    )
    return (resp.message.content or "").strip()


_CALLS = {"gemini": _gemini_call, "ollama": _ollama_call}


def _provider_chain(cfg: Any) -> Tuple[str, ...]:
    primary = str(getattr(cfg, "LLM_BACKEND", "gemini")).lower()
    if primary == "ollama":
        return ("ollama",)  # offline mode: never send reminder text to Gemini
    if getattr(cfg, "LLM_FALLBACK_ENABLED", True):
        return ("gemini", "ollama")
    return ("gemini",)


def generate_short(
    prompt: str,
    system: str,
    cfg: Any,
    *,
    timeout_s: float = 4.0,
    max_tokens: int = 80,
    total_budget_s: Optional[float] = None,
    breaker: Any = None,
) -> GenResult:
    """Never raises. See the module docstring."""
    started = time.monotonic()
    attempts: list = []
    last_failure = UNAVAILABLE
    chain = _provider_chain(cfg)
    primary = chain[0]

    for provider in chain:
        # Skip the primary while the shared breaker says it is down.
        if provider == primary and breaker is not None:
            try:
                if breaker.active() and len(chain) > 1:
                    attempts.append((provider, UNAVAILABLE))
                    last_failure = UNAVAILABLE
                    continue
            except Exception:  # noqa: BLE001
                pass

        remaining = timeout_s
        if total_budget_s is not None:
            remaining = min(timeout_s, total_budget_s - (time.monotonic() - started))
            if remaining < 0.5:
                attempts.append((provider, TIMEOUT))
                last_failure = TIMEOUT
                break

        try:
            text = _run_with_deadline(
                lambda p=provider, r=remaining: _CALLS[p](cfg, prompt, system, r, max_tokens),
                remaining + 0.5,
            )
        except Exception as e:  # noqa: BLE001
            kind = classify_failure(e)
            attempts.append((provider, kind))
            last_failure = kind
            if provider == primary and breaker is not None and kind in _BREAKER_KINDS:
                try:
                    breaker.record(False)
                except Exception:  # noqa: BLE001
                    pass
            continue

        if not text:
            attempts.append((provider, EMPTY))
            last_failure = EMPTY
            continue

        if provider == primary and breaker is not None:
            try:
                breaker.record(True)
            except Exception:  # noqa: BLE001
                pass
        attempts.append((provider, SUCCESS))
        return GenResult(
            text=text,
            provider=provider,
            failure=SUCCESS,
            attempts=tuple(attempts),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    return GenResult(
        text=None,
        provider="none",
        failure=last_failure,
        attempts=tuple(attempts),
        latency_ms=int((time.monotonic() - started) * 1000),
    )