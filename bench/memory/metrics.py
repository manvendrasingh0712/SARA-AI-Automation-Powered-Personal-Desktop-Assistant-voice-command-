"""Pass rules, per-question records, metrics and the regression gate for the memory benchmark."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

RECALL_TYPES = ("single", "contradiction", "contradiction_history", "temporal", "multihop")
PASS_K = 3


def _contains(text: str, needles: Sequence[str]) -> bool:
    folded = text.casefold()
    return any(needle.casefold() in folded for needle in needles)


def first_rank(texts: Sequence[str], needles: Sequence[str]) -> Optional[int]:
    """1-based rank of the first text containing any needle (case-insensitive), else None."""
    for position, text in enumerate(texts, start=1):
        if _contains(text, needles):
            return position
    return None


def judge(question: Mapping[str, Any], texts: Sequence[str]) -> Dict[str, Any]:
    """Apply the pass rule of the question's type to the ranked hit texts."""
    qtype, expect = question["type"], question["expect"]
    rank: Optional[int] = None
    leak = False
    if qtype == "abstain":
        passed = not texts
    elif qtype == "forget":
        banned = list(expect.get("none_of", [])) + list(question.get("forbid", []))
        leak = any(_contains(text, banned) for text in texts)
        passed = not leak
    else:
        rank = first_rank(texts, expect.get("any", []))
        passed = rank is not None and rank <= PASS_K
        if qtype == "contradiction" and texts and _contains(texts[0], question.get("forbid", [])):
            passed = False
    return {"passed": passed, "rank": rank, "leak": leak}


def make_record(scenario: Mapping[str, Any], question: Mapping[str, Any],
                texts: Sequence[str], latency_ms: float) -> Dict[str, Any]:
    """One result row; it holds ids, ranks and counts only, never memory text."""
    verdict = judge(question, texts)
    return {
        "id": question["id"], "scenario": scenario["id"], "lang": scenario["lang"],
        "type": question["type"], "passed": verdict["passed"], "rank": verdict["rank"],
        "hits": len(texts), "leak": verdict["leak"], "latency_ms": round(latency_ms, 4),
    }


def _rate(num: float, den: int) -> Optional[float]:
    return None if den == 0 else round(num / den, 4)


def percentile(values: Sequence[float], pct: int) -> Optional[float]:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(pct * len(ordered) / 100) - 1)], 4)


def _group(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    asked = [r for r in records if r["type"] in RECALL_TYPES]

    def recall(k: int) -> Optional[float]:
        return _rate(sum(1 for r in asked if r["rank"] is not None and r["rank"] <= k), len(asked))

    abstain = [r for r in records if r["type"] == "abstain"]
    forget = [r for r in records if r["type"] == "forget"]
    return {
        "questions": len(records),
        "accuracy": _rate(sum(1 for r in records if r["passed"]), len(records)),
        "recall_at_1": recall(1), "recall_at_3": recall(3), "recall_at_5": recall(5),
        "mrr": _rate(sum(1.0 / r["rank"] for r in asked if r["rank"]), len(asked)),
        "false_memory_rate": _rate(sum(1 for r in abstain if r["hits"] > 0), len(abstain)),
        "forgetting_leak_rate": _rate(sum(1 for r in forget if r["leak"]), len(forget)),
    }


def _type_accuracy(records: Sequence[Mapping[str, Any]], qtype: str) -> Optional[float]:
    subset = [r for r in records if r["type"] == qtype]
    return _rate(sum(1 for r in subset if r["passed"]), len(subset))


def compute_metrics(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Overall and per-type metrics plus retrieval latency percentiles."""
    overall = _group(records)
    overall["contradiction_accuracy"] = _type_accuracy(records, "contradiction")
    overall["temporal_accuracy"] = _type_accuracy(records, "temporal")
    overall["abstention_accuracy"] = _type_accuracy(records, "abstain")
    latencies = [float(r["latency_ms"]) for r in records]
    return {
        "overall": overall,
        "by_type": {t: _group([r for r in records if r["type"] == t])
                    for t in sorted({r["type"] for r in records})},
        "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)},
    }


def check_gate(mem: Mapping[str, Any], base: Mapping[str, Any]) -> List[str]:
    """Failure messages (empty = pass); arguments are the 'overall' dicts of both systems."""
    fails: List[str] = []
    leak = mem.get("forgetting_leak_rate")
    if leak is not None and leak > 0:
        fails.append(f"Memory 2.0 forgetting leak rate is {leak * 100:.1f}% (must be 0)")
    for key, label, worse in (
        ("recall_at_3", "overall recall@3", lambda cur, ref: cur < ref),
        ("abstention_accuracy", "abstention accuracy", lambda cur, ref: cur < ref),
        ("false_memory_rate", "false-memory rate", lambda cur, ref: cur > ref),
    ):
        cur, ref = mem.get(key), base.get(key)
        if cur is not None and ref is not None and worse(cur, ref):
            fails.append(f"{label}: Memory 2.0 {cur * 100:.1f}% vs baseline {ref * 100:.1f}%")
    return fails