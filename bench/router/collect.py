"""
bench.router.collect
Runs every benchmark task on every enabled router tier once and caches each reply.

Usage: python -m bench.router.collect [--tiers a,b] [--limit N] [--rpm 10] [--dry-run]

Resumable: a cell whose cache file already holds ok=true is skipped. Failed cells are retried.
Prompts and replies are never printed; only tier names, counts and task ids appear in output.
Exit codes: 0 done, 2 bad arguments, 3 fewer than two enabled tiers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
TASKS_PATH = ROOT / "bench" / "router" / "tasks.jsonl"
CACHE_ROOT = ROOT / "bench" / ".cache" / "router"
MAX_RETRIES = 4
BACKOFF_BASE_S = 4.0
BACKOFF_MAX_S = 60.0
MAX_OUTPUT_TOKENS = 300
GEMINI_TIMEOUT_S = 30.0
PROGRESS_EVERY = 10
EXIT_OK = 0
EXIT_ONE_TIER = 3
TIER_FAIL_MSG = (
    "Only one model tier is configured. "
    "Set GEMINI_FAST_MODEL (and optionally GEMINI_PRO_MODEL) in .env first."
)
_RATE_LIMIT_NAMES = frozenset({"ResourceExhausted", "RateLimitError"})

Caller = Callable[[Any, str], str]


def load_tasks(path: Path = TASKS_PATH) -> list[dict]:
    """Parse the JSONL tasks file, skipping blank lines."""
    tasks: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def cell_path(cache_root: Path, tier_name: str, task_id: str) -> Path:
    """Cache file for one (tier, task) cell."""
    return cache_root / tier_name / f"{task_id}.json"


def read_cell(path: Path) -> Optional[dict]:
    """Cached cell as a dict, or None when missing or corrupt."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_cell(path: Path, cell: dict) -> None:
    """Atomically write one cell (temp file, then os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cell, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class RateLimiter:
    """Spaces calls at least 60 / rpm seconds apart. Single-threaded use."""

    def __init__(self, rpm: float, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self._interval = 60.0 / rpm
        self._clock = clock
        self._sleep = sleeper
        self._last: Optional[float] = None

    def wait(self) -> None:
        """Block until the next call is allowed."""
        now = self._clock()
        if self._last is not None:
            gap = self._interval - (now - self._last)
            if gap > 0:
                self._sleep(gap)
                now = self._clock()
        self._last = now


def is_rate_limited(exc: BaseException) -> bool:
    """True for HTTP 429 or a known quota / rate-limit exception class."""
    for attr in ("code", "status_code"):
        if getattr(exc, attr, None) == 429:
            return True
    return type(exc).__name__ in _RATE_LIMIT_NAMES


def call_with_backoff(caller: Caller, tier: Any, prompt: str, limiter: RateLimiter,
                      sleeper: Callable[[float], None]) -> tuple[bool, str, float, Optional[str]]:
    """Run one cell with rate limiting and 429 backoff.

    Returns (ok, text, latency_ms, error_code). Raw exception text is never returned.
    """
    attempt = 0
    while True:
        limiter.wait()
        started = time.perf_counter()
        try:
            text = caller(tier, prompt)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            limited = is_rate_limited(exc)
            if limited and attempt < MAX_RETRIES:
                delay = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** attempt))
                print(f"[bench.router] tier={tier.name} rate limited; retry "
                      f"{attempt + 1}/{MAX_RETRIES} in {delay:.0f}s")
                sleeper(delay)
                attempt += 1
                continue
            return False, "", latency_ms, "rate_limited" if limited else "call_failed"
        latency_ms = (time.perf_counter() - started) * 1000.0
        return True, str(text or "").strip(), latency_ms, None


def _gemini_caller(cfg: Any) -> Caller:
    from sara.core.llm.clients import _build_gemini_generate_config, _get_gemini_client

    def call(tier: Any, prompt: str) -> str:
        client = _get_gemini_client(cfg)
        if client is None:
            raise RuntimeError("gemini client unavailable")
        from google.genai import types

        config = _build_gemini_generate_config(
            types, GEMINI_TIMEOUT_S, temperature=0, max_output_tokens=MAX_OUTPUT_TOKENS
        )
        resp = client.models.generate_content(model=tier.model, contents=prompt, config=config)
        return getattr(resp, "text", "") or ""

    return call


def _ollama_caller(cfg: Any) -> Caller:
    from sara.core.llm.clients import _get_ollama_client

    def call(tier: Any, prompt: str) -> str:
        client = _get_ollama_client(cfg)
        if client is None:
            raise RuntimeError("ollama client unavailable")
        resp = client.chat(
            model=tier.model,
            messages=[{"role": "user", "content": prompt}],
            think=False,
            options={
                "temperature": 0,
                "num_predict": MAX_OUTPUT_TOKENS,
                "num_ctx": int(getattr(cfg, "OLLAMA_NUM_CTX", 4096)),
            },
            keep_alive=getattr(cfg, "OLLAMA_KEEP_ALIVE", "5m"),
        )
        return getattr(getattr(resp, "message", None), "content", "") or ""

    return call


def make_real_caller(cfg: Any) -> Caller:
    """Caller that dispatches each tier to its backend's client."""
    gemini = _gemini_caller(cfg)
    ollama = _ollama_caller(cfg)

    def call(tier: Any, prompt: str) -> str:
        if tier.backend == "gemini":
            return gemini(tier, prompt)
        if tier.backend == "ollama":
            return ollama(tier, prompt)
        raise ValueError("unknown backend")

    return call


def fake_caller(tier: Any, prompt: str) -> str:
    """Deterministic offline stand-in for --dry-run and tests. Never touches the network."""
    digest = hashlib.sha256(f"{tier.name}\n{prompt}".encode("utf-8")).hexdigest()
    return str(int(digest[:6], 16) % 1000)


def _select_tiers(all_tiers: Sequence[Any], names: Optional[str]) -> list[Any]:
    if not names:
        return list(all_tiers)
    wanted = {n.strip() for n in names.split(",") if n.strip()}
    return [t for t in all_tiers if t.name in wanted]


def _parse(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bench.router.collect",
        description="Collect one cached reply per (enabled tier, task) cell.",
    )
    parser.add_argument("--tiers", default=None, help="comma-separated tier names (default: all enabled)")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N tasks")
    parser.add_argument("--rpm", type=float, default=10.0, help="maximum requests per minute")
    parser.add_argument("--dry-run", action="store_true", help="deterministic fake model, no network")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.rpm <= 0:
        parser.error("--rpm must be positive")
    return args


def run(argv: Optional[Sequence[str]] = None, *, tiers: Optional[Sequence[Any]] = None,
        tasks: Optional[Sequence[dict]] = None, caller: Optional[Caller] = None,
        cache_root: Path = CACHE_ROOT, sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic) -> int:
    """Collect every missing or failed cell. Returns the process exit code."""
    args = _parse(argv)

    if tiers is None or (caller is None and not args.dry_run):
        from config import Config

    if tiers is None:
        from sara.core.router.tiers import load_tiers

        all_tiers = load_tiers()
    else:
        all_tiers = list(tiers)

    chosen = _select_tiers(all_tiers, args.tiers)
    if len(chosen) < 2:
        print(TIER_FAIL_MSG)
        return EXIT_ONE_TIER

    if caller is None:
        caller = fake_caller if args.dry_run else make_real_caller(Config)

    selected = list(tasks) if tasks is not None else load_tasks()
    if args.limit is not None:
        selected = selected[: args.limit]

    limiter = RateLimiter(args.rpm, clock=clock, sleeper=sleeper)
    total = len(chosen) * len(selected)
    done = skipped = failed = 0

    for tier in chosen:
        for task in selected:
            task_id = str(task["id"])
            path = cell_path(cache_root, tier.name, task_id)
            existing = read_cell(path)
            if existing is not None and existing.get("ok") is True:
                skipped += 1
            else:
                ok, text, latency_ms, error = call_with_backoff(
                    caller, tier, str(task["prompt"]), limiter, sleeper
                )
                write_cell(path, {
                    "text": text,
                    "latency_ms": round(latency_ms, 1),
                    "ok": ok,
                    "error": error,
                })
                if not ok:
                    failed += 1
            done += 1
            if done % PROGRESS_EVERY == 0 or done == total:
                print(f"[bench.router] {done}/{total} cells "
                      f"(cached {skipped}, failed {failed})")

    print(f"[bench.router] finished: {done} cells, {skipped} cached, {failed} failed "
          f"-> {cache_root}")
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())