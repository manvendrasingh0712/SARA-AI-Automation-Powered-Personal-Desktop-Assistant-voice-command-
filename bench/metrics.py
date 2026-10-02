"""Pure metric functions for SARA-Bench router results (no I/O)."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

Record = Mapping[str, Any]

CHAT = "chat"
ERROR_INTENT = "error"
LATENCY_BUDGET_MS = 200.0
LATENCY_NOISE_FLOOR_MS = 0.5
CONFUSION_LIMIT = 10
THRESHOLD_KEYS: Tuple[str, ...] = (
    "max_accuracy_drop_pts",
    "max_false_trigger_increase_pts",
    "max_p95_latency_increase_pct",
    "min_rows",
)


def _r(value: float) -> float:
    return round(value, 6)


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def _hit(rec: Record) -> bool:
    return rec.get("pred") == rec.get("expected")


def accuracy(records: Sequence[Record]) -> float:
    """Top-1 intent accuracy in [0, 1]; 0.0 for no rows."""
    return _ratio(sum(1 for r in records if _hit(r)), len(records))


def accuracy_by(records: Sequence[Record], key: str) -> Dict[str, Dict[str, Any]]:
    """Accuracy per value of `key` (lang, category, style, kind), sorted by value."""
    buckets: Dict[str, List[Record]] = {}
    for rec in records:
        buckets.setdefault(str(rec.get(key, "")), []).append(rec)
    out: Dict[str, Dict[str, Any]] = {}
    for name in sorted(buckets):
        rows = buckets[name]
        correct = sum(1 for r in rows if _hit(r))
        out[name] = {"rows": len(rows), "correct": correct,
                     "accuracy": _r(_ratio(correct, len(rows)))}
    return out


def per_intent(records: Sequence[Record]) -> Dict[str, Dict[str, Any]]:
    """Precision, recall, F1 and support per intent label (expected or predicted)."""
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    for rec in records:
        expected, pred = str(rec.get("expected")), str(rec.get("pred"))
        if expected == pred:
            tp[expected] += 1
        else:
            fn[expected] += 1
            fp[pred] += 1
    out: Dict[str, Dict[str, Any]] = {}
    for label in sorted(set(tp) | set(fp) | set(fn)):
        precision = _ratio(tp[label], tp[label] + fp[label])
        recall = _ratio(tp[label], tp[label] + fn[label])
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[label] = {"support": tp[label] + fn[label], "precision": _r(precision),
                      "recall": _r(recall), "f1": _r(f1)}
    return out


def macro_f1(intents: Mapping[str, Mapping[str, Any]]) -> float:
    """Mean F1 over intents that have at least one expected row."""
    scores = [v["f1"] for v in intents.values() if v["support"] > 0]
    return sum(scores) / len(scores) if scores else 0.0


def top_confusions(records: Sequence[Record],
                   limit: int = CONFUSION_LIMIT) -> List[Dict[str, Any]]:
    """Most frequent (expected, predicted) mistakes; ties broken alphabetically."""
    pairs = Counter(
        (str(r.get("expected")), str(r.get("pred"))) for r in records if not _hit(r)
    )
    ranked = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    return [{"expected": e, "pred": p, "count": c} for (e, p), c in ranked[:limit]]


def false_trigger_rate(records: Sequence[Record], kind: Optional[str] = None) -> float:
    """Share of negatives and chat-expected adversarial rows routed to any tool."""
    rows = [
        r for r in records
        if (kind is None or r.get("kind") == kind)
        and (r.get("kind") == "negative"
             or (r.get("kind") == "adversarial" and r.get("expected") == CHAT))
    ]
    hits = sum(1 for r in rows if r.get("pred") not in (CHAT, ERROR_INTENT))
    return _ratio(hits, len(rows))


def gate_false_open_rate(records: Sequence[Record]) -> float:
    """Share of chat-expected rows whose tool gate opened."""
    rows = [r for r in records if r.get("expected") == CHAT]
    return _ratio(sum(1 for r in rows if r.get("gate_open") is True), len(rows))


def group_rows(records: Sequence[Record]) -> int:
    """Number of rows that carry expected groups."""
    return sum(1 for r in records if r.get("groups_ok") is not None)


def group_accuracy(records: Sequence[Record]) -> Optional[float]:
    """Exact group-match share over rows with expected groups; None when none exist."""
    rows = [r for r in records if r.get("groups_ok") is not None]
    if not rows:
        return None
    return _ratio(sum(1 for r in rows if r.get("groups_ok") is True), len(rows))


def percentile(values: Sequence[float], pct: int) -> float:
    """Nearest-rank percentile: the value at rank ceil(pct * n / 100); 0.0 if empty."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, (pct * len(ordered) + 99) // 100)
    return ordered[min(rank, len(ordered)) - 1]


def latency_stats(records: Sequence[Record]) -> Dict[str, float]:
    """Latency p50/p95/p99 in milliseconds."""
    values = [
        float(r["latency_ms"]) for r in records
        if isinstance(r.get("latency_ms"), (int, float))
    ]
    return {f"p{p}": _r(percentile(values, p)) for p in (50, 95, 99)}


def latency_score(p95_ms: float) -> float:
    """clamp(1 - p95 / 200 ms, 0, 1)."""
    return min(1.0, max(0.0, 1.0 - p95_ms / LATENCY_BUDGET_MS))


def composite(acc: float, false_trigger: float, group: Optional[float],
              lat_score: float) -> float:
    """0.5*acc + 0.2*(1-ft) + 0.2*group + 0.1*lat; group weight is redistributed if None."""
    parts = [(0.5, acc), (0.2, 1.0 - false_trigger), (0.1, lat_score)]
    if group is not None:
        parts.append((0.2, group))
    total = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / total


def compute_metrics(records: Sequence[Record]) -> Dict[str, Any]:
    """All metrics for one run as a JSON-serialisable dict with deterministic ordering."""
    rows = list(records)
    acc = accuracy(rows)
    intents = per_intent(rows)
    false_trigger = false_trigger_rate(rows)
    group = group_accuracy(rows)
    latency = latency_stats(rows)
    score = latency_score(latency["p95"])
    return {
        "rows": len(rows),
        "accuracy": _r(acc),
        "by_lang": accuracy_by(rows, "lang"),
        "by_category": accuracy_by(rows, "category"),
        "by_style": accuracy_by(rows, "style"),
        "by_kind": accuracy_by(rows, "kind"),
        "intents": intents,
        "macro_f1": _r(macro_f1(intents)),
        "confusions": top_confusions(rows),
        "false_trigger_rate": _r(false_trigger),
        "false_trigger_negative": _r(false_trigger_rate(rows, "negative")),
        "false_trigger_adversarial": _r(false_trigger_rate(rows, "adversarial")),
        "gate_false_open_rate": _r(gate_false_open_rate(rows)),
        "group_accuracy": None if group is None else _r(group),
        "group_rows": group_rows(rows),
        "latency": latency,
        "latency_score": _r(score),
        "composite": _r(composite(acc, false_trigger, group, score)) if rows else 0.0,
    }


def check_gate(current: Mapping[str, Any], baseline: Mapping[str, Any],
               thresholds: Mapping[str, float]) -> List[str]:
    """Return one message per failed threshold; empty list means the gate passes."""
    failures: List[str] = []
    rows, min_rows = current["rows"], thresholds["min_rows"]
    if rows < min_rows:
        failures.append(f"rows {rows} below min_rows {min_rows:g}")
    drop = round((baseline["accuracy"] - current["accuracy"]) * 100.0, 6)
    limit = thresholds["max_accuracy_drop_pts"]
    if drop > limit:
        failures.append(f"accuracy dropped {drop:.2f} pts (max {limit:g})")
    rise = round((current["false_trigger_rate"] - baseline["false_trigger_rate"]) * 100.0, 6)
    limit = thresholds["max_false_trigger_increase_pts"]
    if rise > limit:
        failures.append(f"false-trigger rate rose {rise:.2f} pts (max {limit:g})")
    base_p95 = baseline["latency"]["p95"]
    if base_p95 > 0:
        pct = round((current["latency"]["p95"] - base_p95) / base_p95 * 100.0, 6)
        limit = thresholds["max_p95_latency_increase_pct"]
        added_ms = current["latency"]["p95"] - base_p95
        if pct > limit and added_ms > LATENCY_NOISE_FLOOR_MS:
            failures.append(f"p95 latency rose {pct:.1f}% (max {limit:g}%)")
    return failures