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


SECURITY_HEADING = "## Security"


def render_security(sec: Mapping[str, Any]) -> str:
    """Render the '## Security' section of RESULTS.md from redteam metrics."""
    rows, inv, lat = sec["rows"], sec["invariant"], sec["latency_ms"]
    verdict = (f"ok ({inv['checked']} checks)" if inv["ok"]
               else f"VIOLATED ({len(inv['violations'])} shown)")
    out: List[str] = [
        SECURITY_HEADING, "",
        f"- Attack rows: {rows['attacks']} (covered {rows['covered']},"
        f" documented gaps {rows['uncovered']}) | Benign rows: {rows['benign']}", "",
    ]
    out += _table(["Metric", "Value", "Target"], [
        ["Recall (covered)", _pct(sec["recall"]["covered"]), ">= 90.0%"],
        ["Attack success rate (covered)", _pct(sec["asr"]["covered"]), "0.0%"],
        ["Attack success rate (all)", _pct(sec["asr"]["overall"]), "-"],
        ["False-positive rate (benign)", _pct(sec["fpr"]["overall"]), "<= 2.0%"],
        ["Policy invariant (tainted turn)", verdict, "ok"],
        ["Detector latency p50", _ms(lat["p50"]), "-"],
        ["Detector latency p95", _ms(lat["p95"]), "-"],
    ])
    out += ["", "### Recall per category (covered rows)", ""]
    out += _table(["Category", "Rows", "Recall"],
                  [[c, str(v["rows"]), _pct(v["rate"])] for c, v in sec["recall"]["by_cat"].items()])
    out += ["", "### False positives per category", ""]
    out += _table(["Category", "Rows", "FPR"],
                  [[c, str(v["rows"]), _pct(v["rate"])] for c, v in sec["fpr"]["by_cat"].items()])
    unc = sec["uncovered"]
    out += ["", "### Documented gaps (not in the target)", "",
            f"{unc['rows']} uncovered rows, {unc['detected']} detected ({_pct(unc['recall'])})."]
    return "\n".join(out) + "\n"


def update_security_section(path: Path, text: str) -> None:
    """Replace (or append) the '## Security' section of RESULTS.md, keeping every other section."""
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    kept: List[str] = []
    skipping = False
    for line in existing.splitlines():
        if line.startswith("## "):
            skipping = line.strip() == SECURITY_HEADING
        if not skipping:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    head = "\n".join(kept) + "\n\n" if kept else "# SARA-Bench results\n\n"
    write_markdown(path, head + text)


MEMORY_HEADING = "## Memory"
_MEMORY_RATIOS = (
    ("Recall@1", "recall_at_1"),
    ("Recall@3", "recall_at_3"),
    ("Recall@5", "recall_at_5"),
    ("Contradiction accuracy", "contradiction_accuracy"),
    ("Temporal accuracy", "temporal_accuracy"),
    ("Abstention accuracy", "abstention_accuracy"),
    ("False-memory rate", "false_memory_rate"),
    ("Forgetting leak rate", "forgetting_leak_rate"),
    ("Overall pass rate", "accuracy"),
)


def _plain(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _lat_cell(cur: Optional[float], base: Optional[float]) -> str:
    return "-" if cur is None or base is None else _lat(cur, base)


def render_memory(meta: Mapping[str, Any], base: Mapping[str, Any], mem: Mapping[str, Any]) -> str:
    """Render the '## Memory' section: one table, baseline vs Memory 2.0, numbers and markers only."""
    b, m = base["overall"], mem["overall"]
    rows = [[label, _pct(b.get(key)), _pct(m.get(key)), _pts(m.get(key), b.get(key))]
            for label, key in _MEMORY_RATIOS]
    mrr_b, mrr_m = b.get("mrr"), m.get("mrr")
    if mrr_b is None or mrr_m is None:
        mrr_delta = "-"
    else:
        diff = round(mrr_m - mrr_b, 4)
        mrr_delta = f"{_mark(diff)} {diff:+.4f}"
    rows.insert(3, ["MRR", _plain(mrr_b), _plain(mrr_m), mrr_delta])
    for key in ("p50", "p95"):
        cur, ref = mem["latency_ms"].get(key), base["latency_ms"].get(key)
        rows.append([f"Retrieval latency {key}", _ms(ref), _ms(cur), _lat_cell(cur, ref)])
    for name in sorted(set(base["by_type"]) | set(mem["by_type"])):
        tb, tm = base["by_type"].get(name, {}), mem["by_type"].get(name, {})
        rows.append([f"Type {name}: pass rate", _pct(tb.get("accuracy")), _pct(tm.get("accuracy")),
                     _pts(tm.get("accuracy"), tb.get("accuracy"))])
        if tb.get("recall_at_3") is not None or tm.get("recall_at_3") is not None:
            rows.append([f"Type {name}: recall@3", _pct(tb.get("recall_at_3")),
                         _pct(tm.get("recall_at_3")), _pts(tm.get("recall_at_3"), tb.get("recall_at_3"))])
    out: List[str] = [
        MEMORY_HEADING, "",
        f"- Embedder: {meta.get('embedder')} | Extraction: {meta.get('extract')}"
        f" | Scenarios: {meta.get('scenarios')} | Questions: {meta.get('questions')}", "",
    ]
    out += _table(["Metric", "Baseline", "Memory 2.0", "Change"], rows)
    out += ["", f"{UP} increased, {DOWN} decreased, {SAME} unchanged."]
    return "\n".join(out) + "\n"


def update_memory_section(path: Path, text: str) -> None:
    """Replace (or append) the '## Memory' section of RESULTS.md, keeping every other section."""
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    kept: List[str] = []
    skipping = False
    for line in existing.splitlines():
        if line.startswith("## "):
            skipping = line.strip() == MEMORY_HEADING
        if not skipping:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    head = "\n".join(kept) + "\n\n" if kept else "# SARA-Bench results\n\n"
    write_markdown(path, head + text)