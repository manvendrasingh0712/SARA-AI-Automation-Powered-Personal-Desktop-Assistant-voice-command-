"""Memory benchmark runner: Memory 2.0 (gold extraction) versus the old vector-only RAG baseline."""

from __future__ import annotations

import importlib
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import numpy as np

from bench import report

from . import loader, metrics
from .baseline import VectorBaseline

logger = logging.getLogger(__name__)

DEFAULT_OUT = "bench/results/memory.json"
DEFAULT_REPORT = "bench/results/RESULTS.md"
EXIT_FAIL = 2
EXIT_MISSING = 4
_CACHE_MAX = 50000
Embed = Callable[[str], Optional[np.ndarray]]


class _QuietTouch(logging.Filter):
    """Hide the harmless late 'touch failed' line from retrieve()'s async update after a store closes."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith("[Memory2] touch failed")


def make_embedder(name: str) -> Optional[Embed]:
    """Cached embedder for 'hash' or 'gemini'; None when unavailable."""
    try:
        if name == "hash":
            from sara.core.memory2.hashembed import hash_embed as raw
        else:
            from sara.core.rag import embed_text as raw
    except Exception as exc:  # noqa: BLE001
        logger.warning("[membench] embedder import failed (%s)", type(exc).__name__)
        return None
    cache: Dict[str, np.ndarray] = {}
    lock = threading.Lock()

    def embed(text: str) -> Optional[np.ndarray]:
        with lock:
            if text in cache:
                return cache[text]
        try:
            value = raw(text)
            vec = None if value is None else np.asarray(value, dtype=np.float32).reshape(-1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[membench] embed failed (%s)", type(exc).__name__)
            return None
        if vec is not None:
            with lock:
                if len(cache) >= _CACHE_MAX:
                    cache.clear()
                cache[text] = vec
        return vec

    if name != "hash" and embed("probe") is None:
        return None
    return embed


def load_modules() -> Optional[SimpleNamespace]:
    """The Memory 2.0 retrieval / forget / store entry points, or None when a module is missing."""
    try:
        retrieve = importlib.import_module("sara.core.memory2.retrieve")
        forget = importlib.import_module("sara.core.memory2.forget")
        store = importlib.import_module("sara.core.memory2.store")
        return SimpleNamespace(
            retrieve=retrieve.retrieve, find_matches=forget.find_matches,
            retract_hits=forget.retract_hits, store_cls=store.Memory2Store)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[membench] memory2 modules unavailable (%s)", type(exc).__name__)
        return None


def _ops_at(scenario: Mapping[str, Any], slot: int) -> List[Mapping[str, Any]]:
    """Forget ops due after ``slot`` turns (after_turn is 1-based and clamped to the turn count)."""
    count = len(scenario["turns"])
    return [op for op in scenario.get("forget", [])
            if min(max(int(op["after_turn"]), 0), count) == slot]


def _forget_memory2(ops: Sequence[Mapping[str, Any]], store: Any, modules: Any, embed: Embed) -> None:
    for op in ops:
        try:
            hits = modules.find_matches(op["phrase"], store=store, embed=embed)
            modules.retract_hits(hits, store=store)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[membench] forget op failed (%s)", type(exc).__name__)


def _load_turn(store: Any, turn: Mapping[str, Any], turn_id: str) -> None:
    stamp = loader.to_epoch(turn["ts"])
    for fact in turn.get("facts", []):
        try:
            store.upsert_fact(
                fact["subject"], fact["predicate"], str(fact["object"]), valid_from=stamp,
                confidence=0.9, importance=0.6, source_turn_id=turn_id, now=stamp)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[membench] fact load failed (%s)", type(exc).__name__)
    for event in turn.get("events", []):
        try:
            store.add_event(
                event["summary"], loader.to_epoch(event["ts"]), entities=[], importance=0.5,
                source_turn_id=turn_id, now=stamp)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[membench] event load failed (%s)", type(exc).__name__)


def _freeze_usage(store: Any) -> None:
    """Make retrieve()'s asynchronous usage update a no-op so rankings cannot depend on thread timing."""
    try:
        store.touch = lambda ids, now=None: None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[membench] usage freeze failed (%s)", type(exc).__name__)


def evaluate_memory2(scenarios: Sequence[Mapping[str, Any]], embed: Embed, modules: Any,
                     workdir: Path, timer: Callable[[], float] = time.perf_counter
                     ) -> List[Dict[str, Any]]:
    """Replay every scenario into a fresh temp store and answer its questions with retrieve()."""
    records: List[Dict[str, Any]] = []
    for number, scenario in enumerate(scenarios):
        now = [0.0]
        store = modules.store_cls(db_path=workdir / f"s{number}.db", embed=embed,
                                  clock=lambda: now[0])
        _freeze_usage(store)
        try:
            _forget_memory2(_ops_at(scenario, 0), store, modules, embed)
            for index, turn in enumerate(scenario["turns"], start=1):
                now[0] = loader.to_epoch(turn["ts"])
                _load_turn(store, turn, f"t{index}")
                _forget_memory2(_ops_at(scenario, index), store, modules, embed)
            for question in scenario["questions"]:
                at = loader.to_epoch(question["at"])
                now[0] = at
                started = timer()
                try:
                    hits = modules.retrieve(question["q"], store=store, now=at, embed=embed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[membench] retrieve failed (%s)", type(exc).__name__)
                    hits = []
                elapsed = (timer() - started) * 1000.0
                texts = [str(getattr(hit, "text", "")) for hit in hits or []]
                records.append(metrics.make_record(scenario, question, texts, elapsed))
        finally:
            store.close()
    return records


def evaluate_baseline(scenarios: Sequence[Mapping[str, Any]], embed: Embed,
                      timer: Callable[[], float] = time.perf_counter) -> List[Dict[str, Any]]:
    """Answer every question with the vector-only baseline over the raw utterances."""
    records: List[Dict[str, Any]] = []
    for scenario in scenarios:
        base = VectorBaseline(embed)
        for op in _ops_at(scenario, 0):
            base.forget(op["phrase"])
        for index, turn in enumerate(scenario["turns"], start=1):
            base.add(turn["text"])
            for op in _ops_at(scenario, index):
                base.forget(op["phrase"])
        for question in scenario["questions"]:
            started = timer()
            texts = base.query(question["q"])
            elapsed = (timer() - started) * 1000.0
            records.append(metrics.make_record(scenario, question, texts, elapsed))
    return records


def _fmt(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _summary(meta: Mapping[str, Any], base: Mapping[str, Any], mem: Mapping[str, Any]) -> str:
    return (f"SARA-Bench memory: scenarios={meta['scenarios']} questions={meta['questions']}"
            f" recall@3 baseline={_fmt(base['recall_at_3'])} memory2={_fmt(mem['recall_at_3'])}"
            f" leak memory2={_fmt(mem['forgetting_leak_rate'])}")


def run(out: Path, embedder: str = "hash", extract: str = "gold", gate: bool = False,
        report_md: Optional[Path] = None, scenarios_path: Optional[Path] = None) -> int:
    """Run the memory benchmark; 0 ok, 2 error or failed gate, 4 missing modules / embedder."""
    if extract != "gold":
        print("ERROR: --extract llm is not implemented", file=sys.stderr)
        return EXIT_FAIL
    try:
        scenarios = loader.load_scenarios(scenarios_path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_FAIL
    modules = load_modules()
    if modules is None:
        print("ERROR: Memory 2.0 modules are missing (need retrieve, forget and store)", file=sys.stderr)
        return EXIT_MISSING
    embed = make_embedder(embedder)
    if embed is None:
        print(f"ERROR: embedder '{embedder}' is not available (no vector returned)", file=sys.stderr)
        return EXIT_MISSING
    quiet = _QuietTouch()
    handlers = list(logging.getLogger().handlers)
    for handler in handlers:
        handler.addFilter(quiet)
    try:
        with tempfile.TemporaryDirectory(prefix="sara_membench_", ignore_cleanup_errors=True) as tmp:
            mem_records = evaluate_memory2(scenarios, embed, modules, Path(tmp))
        base_records = evaluate_baseline(scenarios, embed)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: benchmark failed ({type(exc).__name__})", file=sys.stderr)
        return EXIT_FAIL
    finally:
        for handler in handlers:
            handler.removeFilter(quiet)
    base_metrics = metrics.compute_metrics(base_records)
    mem_metrics = metrics.compute_metrics(mem_records)
    meta = {"mode": "memory", "embedder": embedder, "extract": extract,
            "scenarios": len(scenarios), "questions": len(base_records)}
    payload = {"meta": meta, "metrics": {"baseline": base_metrics, "memory2": mem_metrics},
               "results": {"baseline": base_records, "memory2": mem_records}}
    target = report_md or Path(DEFAULT_REPORT)
    try:
        report.write_json(out, payload)
        report.update_memory_section(target, report.render_memory(meta, base_metrics, mem_metrics))
    except OSError as exc:
        print(f"ERROR: cannot write results ({type(exc).__name__})", file=sys.stderr)
        return EXIT_FAIL
    print(_summary(meta, base_metrics["overall"], mem_metrics["overall"]))
    if not gate:
        return 0
    failures = metrics.check_gate(mem_metrics["overall"], base_metrics["overall"])
    if failures:
        print("GATE FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return EXIT_FAIL
    print("GATE PASSED")
    return 0