"""Results JSON and RESULTS.md writers for SARA-Bench (deterministic output)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Mapping, Optional

UP, DOWN, SAME = "▲", "▼", "="

_RATIO_METRICS = (
    ("Accuracy", "accuracy"),
    ("Macro-F1", "macro_f1"),
    ("False-trigger rate", "false_trigger_rate"),
    ("Gate false-open rate", "gate_false_open_rate"),
    ("Group accuracy", "group_accuracy"),
)
ACCEPT_HINT = "python -m bench.run_bench --mode router --accept-baseline"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write the results JSON."""
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=1))


def write_markdown(path: Path, text: str) -> None:
    """Atomically write RESULTS.md."""
    _atomic_write(path, text)


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ms(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.4f} ms"


def _mark(delta: float) -> str:
    return UP if delta > 0 else DOWN if delta < 0 else SAME


def _pts(cur: Optional[float], base: Optional[float]) -> str:
    if cur is None or base is None:
        return "-"
    delta = round((cur - base) * 100.0, 1)
    return f"{_mark(delta)} {delta:+.1f} pts"


def _lat(cur: float, base: Optional[float]) -> str:
    if base is None:
        return "-"
    delta = round(cur - base, 4)
    rel = f" ({(cur - base) / base * 100.0:+.1f}%)" if base > 0 else ""
    return f"{_mark(delta)} {delta:+.4f} ms{rel}"


def _table(header: List[str], rows: List[List[str]]) -> List[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _group_section(title: str, label: str, cur: Mapping[str, Any],
                   base: Mapping[str, Any]) -> List[str]:
    rows = []
    for name in sorted(cur):
        prev = (base.get(name) or {}).get("accuracy")
        rows.append([name, str(cur[name]["rows"]), _pct(cur[name]["accuracy"]),
                     _pts(cur[name]["accuracy"], prev)])
    return [f"## {title}", ""] + _table([label, "Rows", "Accuracy", "vs baseline"], rows) + [""]


def _changes(cur: Mapping[str, Any], base: Mapping[str, Any]) -> List[List[str]]:
    rows = [[label, _pct(base.get(key)), _pct(cur.get(key)), _pts(cur.get(key), base.get(key))]
            for label, key in _RATIO_METRICS]
    base_lat = base.get("latency") or {}
    for key in ("p50", "p95", "p99"):
        rows.append([f"Latency {key}", _ms(base_lat.get(key)), _ms(cur["latency"][key]),
                     _lat(cur["latency"][key], base_lat.get(key))])
    prev = base.get("composite")
    delta = "-" if prev is None else f"{_mark(cur['composite'] - prev)} {cur['composite'] - prev:+.4f}"
    rows.append(["Composite", "n/a" if prev is None else f"{prev:.4f}",
                 f"{cur['composite']:.4f}", delta])
    return rows


def render_markdown(meta: Mapping[str, Any], current: Mapping[str, Any],
                    baseline_meta: Optional[Mapping[str, Any]] = None,
                    baseline: Optional[Mapping[str, Any]] = None) -> str:
    """Render RESULTS.md; `baseline` (metrics dict) enables the comparison columns."""
    base: Mapping[str, Any] = baseline or {}
    out: List[str] = [
        "# SARA-Bench results", "",
        f"- Timestamp (UTC): {meta.get('timestamp') or 'n/a'}",
        f"- Git commit: {meta.get('git_commit') or 'n/a'}",
        f"- Python: {meta.get('python') or 'n/a'} | OS: {meta.get('os') or 'n/a'}"
        f" | CPU: {meta.get('cpu') or 'n/a'}",
        f"- Mode: {meta.get('mode') or 'router'} | Rows: {current['rows']}",
    ]
    if baseline is None:
        out.append("- Baseline: none")
    else:
        bm = baseline_meta or {}
        out.append(f"- Baseline: commit {bm.get('git_commit') or 'n/a'},"
                   f" {bm.get('timestamp') or 'n/a'}")
    out += ["", "## Headline", ""]
    out += _table(["Metric", "Value"], [
        ["Rows", str(current["rows"])],
        ["Accuracy", _pct(current["accuracy"])],
        ["Macro-F1", _pct(current["macro_f1"])],
        ["False-trigger rate", _pct(current["false_trigger_rate"])],
        ["Gate false-open rate", _pct(current["gate_false_open_rate"])],
        ["Group accuracy", f"{_pct(current['group_accuracy'])} ({current['group_rows']} rows)"],
        ["Latency p95", _ms(current["latency"]["p95"])],
        ["Composite", f"{current['composite']:.4f}"],
    ])
    out.append("")
    out += _group_section("Per language", "Language", current["by_lang"], base.get("by_lang", {}))
    out += _group_section("Per category", "Category", current["by_category"],
                          base.get("by_category", {}))
    out += ["## Top confusions", ""]
    if current["confusions"]:
        out += _table(["Expected", "Predicted", "Count"],
                      [[c["expected"], c["pred"], str(c["count"])] for c in current["confusions"]])
    else:
        out.append("No confusions.")
    out += ["", "## Latency", ""]
    out += _table(["Percentile", "Latency"],
                  [[key, _ms(current["latency"][key])] for key in ("p50", "p95", "p99")]
                  + [["score", f"{current['latency_score']:.4f}"]])
    out += ["", "## Changes vs baseline", ""]
    if baseline is None:
        out.append(f"No baseline to compare. Create it with `{ACCEPT_HINT}`.")
    else:
        out += _table(["Metric", "Baseline", "Current", "Change"], _changes(current, base))
        out += ["", f"{UP} increased, {DOWN} decreased, {SAME} unchanged."]
    return "\n".join(out) + "\n"