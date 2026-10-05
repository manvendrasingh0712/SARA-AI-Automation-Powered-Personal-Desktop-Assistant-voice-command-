"""CLI: python -m bench.run_bench --mode router --out bench/results/latest.json"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import metrics, redteam_runner, report  # noqa: E402
from bench.dataset import Row, load_rows  # noqa: E402
from bench.runner import Result, run_router  # noqa: E402

THRESHOLDS_PATH: Path = ROOT / "bench" / "thresholds.json"
BASELINE_PATH: Path = ROOT / "bench" / "baseline.json"
EXIT_FAIL = 2
EXIT_NO_BASELINE = 3
ACCEPT_HINT = "python -m bench.run_bench --mode router --accept-baseline"


def _git_commit() -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


def _meta(row_count: int, mode: str) -> Dict[str, Any]:
    return {
        "python": platform.python_version(),
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "git_commit": _git_commit(),
        "row_count": row_count,
        "mode": mode,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _record(row: Row, res: Result) -> Dict[str, Any]:
    groups_ok: Optional[bool] = None
    if row.expected_groups:
        groups_ok = (
            res.pred_intent == row.expected_intent
            and res.pred_groups == row.expected_groups
        )
    return {
        "id": row.id,
        "text": row.text,
        "lang": row.lang,
        "expected": row.expected_intent,
        "pred": res.pred_intent,
        "groups_ok": groups_ok,
        "gate_open": res.gate_open,
        "latency_ms": res.latency_ms,
        "kind": row.kind,
        "style": row.style,
        "category": row.category,
    }


def _pct(num: int, den: int) -> str:
    return f"{100.0 * num / den:.1f}%" if den else "n/a"


def _summary(records: List[Dict[str, Any]]) -> str:
    total = len(records)
    correct = sum(1 for r in records if r["pred"] == r["expected"])
    parts = [f"rows={total}", f"acc={_pct(correct, total)}"]
    for lang in sorted({r["lang"] for r in records}):
        subset = [r for r in records if r["lang"] == lang]
        hit = sum(1 for r in subset if r["pred"] == r["expected"])
        parts.append(f"{lang}={_pct(hit, len(subset))}")
    negatives = [r for r in records if r["kind"] == "negative"]
    false_triggers = sum(1 for r in negatives if r["pred"] != "chat")
    parts.append(f"neg_false_triggers={false_triggers}/{len(negatives)}")
    return "SARA-Bench router: " + " ".join(parts)


def _load_thresholds() -> Optional[Dict[str, float]]:
    """Read bench/thresholds.json; None when missing or malformed."""
    try:
        raw = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
        return {key: float(raw[key]) for key in metrics.THRESHOLD_KEYS}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _load_baseline(path: Path) -> Optional[Dict[str, Any]]:
    """Read a results JSON; None when missing or malformed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    records = data.get("results") if isinstance(data, dict) else None
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        return None
    return data


def _accept_baseline(payload: Dict[str, Any], thresholds: Optional[Dict[str, float]],
                     target: Path) -> int:
    """Write the baseline; refuse when the run has fewer than min_rows rows."""
    if thresholds is None:
        print(f"ERROR: cannot read {THRESHOLDS_PATH.name}", file=sys.stderr)
        return EXIT_FAIL
    rows = len(payload["results"])
    if rows < thresholds["min_rows"]:
        print(f"ERROR: baseline refused: {rows} rows < min_rows {thresholds['min_rows']:g}",
              file=sys.stderr)
        return EXIT_FAIL
    try:
        report.write_json(target, payload)
    except OSError as exc:
        print(f"ERROR: cannot write {target} ({type(exc).__name__})", file=sys.stderr)
        return EXIT_FAIL
    print(f"Baseline written: {target}")
    return 0


def _run_gate(current: Dict[str, Any], base_metrics: Optional[Dict[str, Any]],
              thresholds: Optional[Dict[str, float]], source: Path) -> int:
    """Return 0 on pass, 2 on threshold failure, 3 when the baseline is missing."""
    if base_metrics is None:
        print(f"ERROR: baseline not found or unreadable: {source}\n"
              f"Create it with: {ACCEPT_HINT}", file=sys.stderr)
        return EXIT_NO_BASELINE
    if thresholds is None:
        print(f"ERROR: cannot read {THRESHOLDS_PATH.name}", file=sys.stderr)
        return EXIT_FAIL
    failures = metrics.check_gate(current, base_metrics, thresholds)
    if failures:
        print("GATE FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return EXIT_FAIL
    print("GATE PASSED")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.run_bench")
    parser.add_argument("--mode", choices=["router", "redteam"], default="router")
    parser.add_argument("--out", default=None,
                        help="results JSON (default: bench/results/latest.json, or redteam.json)")
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--compare", metavar="PATH", default=None,
                        help="gate this run against a baseline results JSON")
    parser.add_argument("--accept-baseline", action="store_true",
                        help="write this run as the baseline (refused below min_rows)")
    parser.add_argument("--report-md", metavar="PATH", default=None,
                        help="also write a RESULTS.md report")
    parser.add_argument("--gate", action="store_true",
                        help="redteam mode: exit 2 when a security threshold fails")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if args.mode == "redteam":
        return redteam_runner.run(
            Path(args.out or redteam_runner.DEFAULT_OUT), args.gate,
            Path(args.report_md) if args.report_md else None)
    args.out = args.out or "bench/results/latest.json"

    try:
        rows = load_rows()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if not rows:
        print("ERROR: no golden rows found", file=sys.stderr)
        return EXIT_FAIL

    results = run_router(rows, warmup=args.warmup)
    records = [_record(row, res) for row, res in zip(rows, results)]
    meta = _meta(len(records), args.mode)
    current = metrics.compute_metrics(records)
    payload = {"meta": meta, "metrics": current, "results": records}
    try:
        report.write_json(Path(args.out), payload)
    except OSError as exc:
        print(f"ERROR: cannot write {args.out} ({type(exc).__name__})", file=sys.stderr)
        return EXIT_FAIL
    print(_summary(records))

    target = Path(args.compare) if args.compare else BASELINE_PATH
    thresholds = _load_thresholds()
    if args.accept_baseline:
        return _accept_baseline(payload, thresholds, target)
    baseline = _load_baseline(target) if args.compare else None
    base_metrics = metrics.compute_metrics(baseline["results"]) if baseline else None
    if args.report_md:
        text = report.render_markdown(
            meta, current, (baseline or {}).get("meta"), base_metrics)
        try:
            report.write_markdown(Path(args.report_md), text)
        except OSError as exc:
            print(f"ERROR: cannot write {args.report_md} ({type(exc).__name__})",
                  file=sys.stderr)
            return EXIT_FAIL
    if not args.compare:
        return 0
    return _run_gate(current, base_metrics, thresholds, target)


if __name__ == "__main__":
    sys.exit(main())