"""
bench.router.simulate
Replays routing policies over the cached oracle table and writes the router gate.

Usage: python -m bench.router.simulate [--seeds 20] [--out bench/router/results]

Policies compared on the same per-seed 30 % test split: always-cheapest, always-main, always-pro
(when a *_pro tier exists), STATIC (policy.static_choice) and LINUCB (trained on the other 70 %
with sequential bandit updates, evaluated greedily). STATIC and LINUCB both obey the guardrail
floors from policy.floor_tier. Deterministic for a given seed count.
Exit codes: 0 done, 2 bad arguments, 3 cache too small (fewer than 2 tiers or 60 complete tasks).
Reply text is never printed or written to the outputs.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from bench.router.check import check_answer
from bench.router.collect import CACHE_ROOT, TASKS_PATH, cell_path, load_tasks, read_cell
from sara.core.router.features import FEATURE_DIM, extract_features
from sara.core.router.linucb import LinUCB
from sara.core.router.policy import floor_tier, static_choice
from sara.core.router.rewards import compute_reward
from sara.core.router.tiers import load_tiers

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = "bench/router/results"
MIN_COMPLETE = 60
TRAIN_FRACTION = 0.7
PASSES = 3
ACCURACY_MARGIN = 0.01
EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_TOO_SMALL = 3
METRICS = ("accuracy", "mean_latency_ms", "regret", "quota_units", "quota_saved_pct")
TOO_FEW_TIERS_MSG = (
    "Only one model tier is configured. "
    "Set GEMINI_FAST_MODEL (and optionally GEMINI_PRO_MODEL) in .env first."
)


def _costs(tiers: Sequence[Any]) -> dict[str, float]:
    return {t.name: float(t.cost) for t in tiers}


def _config_value(name: str, default: float) -> float:
    try:
        from config import Config

        return float(getattr(Config, name, default))
    except Exception:
        return float(default)


def build_rows(tasks: Sequence[Mapping[str, Any]], tiers: Sequence[Any],
               cache_root: Path) -> tuple[list[dict], dict[str, int]]:
    """Complete tasks (an ok cell on every tier) with correctness and latency per tier."""
    names = [t.name for t in tiers]
    rows: list[dict] = []
    incomplete = {name: 0 for name in names}
    for task in tasks:
        task_id = str(task["id"])
        cells: dict[str, tuple[bool, float]] = {}
        for tier in tiers:
            cell = read_cell(cell_path(cache_root, tier.name, task_id))
            if not cell or cell.get("ok") is not True:
                incomplete[tier.name] += 1
                continue
            correct = check_answer(str(cell.get("text") or ""), task.get("check") or {})
            cells[tier.name] = (bool(correct), float(cell.get("latency_ms") or 0.0))
        if len(cells) == len(names):
            rows.append({
                "id": task_id,
                "feat": extract_features(str(task.get("prompt", "")), None),
                "cells": cells,
            })
    return rows, incomplete


def _allowed(feat: Any, tiers: Sequence[Any]) -> list[str]:
    costs = _costs(tiers)
    floor_cost = costs[floor_tier(feat, tiers)]
    return [name for name, cost in costs.items() if cost >= floor_cost]


def _with_floor(name: str, feat: Any, tiers: Sequence[Any]) -> str:
    costs = _costs(tiers)
    floor = floor_tier(feat, tiers)
    return name if costs[name] >= costs[floor] else floor


def _main_name(tiers: Sequence[Any]) -> str:
    for tier in tiers:
        if tier.name.endswith("_main"):
            return tier.name
    non_pro = [t for t in tiers if not t.name.endswith("_pro")]
    return (non_pro or list(tiers))[-1].name


def _pro_name(tiers: Sequence[Any]) -> Optional[str]:
    return next((t.name for t in tiers if t.name.endswith("_pro")), None)


def _train(rows: Sequence[dict], idx: Sequence[int], tiers: Sequence[Any], rng: random.Random,
           alpha: float, lam_lat: float, lam_cost: float) -> LinUCB:
    """Sequential bandit updates over several shuffled passes; reward = correct - penalties."""
    costs = _costs(tiers)
    bandit = LinUCB([t.name for t in tiers], FEATURE_DIM, alpha=alpha)
    order = list(idx)
    for _ in range(PASSES):
        rng.shuffle(order)
        for i in order:
            feat = rows[i]["feat"]
            arm = bandit.choose(feat.vector, _allowed(feat, tiers))
            correct, latency = rows[i]["cells"][arm]
            reward = compute_reward(1.0 if correct else 0.0, latency, costs[arm], lam_lat, lam_cost)
            bandit.update(arm, feat.vector, reward)
    return bandit


def _greedy(bandit: LinUCB) -> LinUCB:
    """Copy of the trained bandit with exploration switched off (alpha 0).

    Assumes LinUCB.to_state() carries its alpha under the key "alpha".
    """
    state = bandit.to_state()
    state["alpha"] = 0.0
    greedy = LinUCB.from_state(state)
    if greedy is None:
        raise RuntimeError("router state could not be copied for greedy evaluation")
    return greedy


def _score(picks: Sequence[str], rows: Sequence[dict], idx: Sequence[int],
           tiers: Sequence[Any], main_name: str) -> dict:
    costs = _costs(tiers)
    n = len(idx)
    hits = oracle = 0
    latency = quota = 0.0
    mix: dict[str, int] = {}
    for i, name in zip(idx, picks):
        cells = rows[i]["cells"]
        correct, cell_latency = cells[name]
        hits += int(correct)
        oracle += int(any(value[0] for value in cells.values()))
        latency += cell_latency
        quota += costs[name]
        mix[name] = mix.get(name, 0) + 1
    main_quota = costs[main_name] * n
    return {
        "accuracy": hits / n,
        "mean_latency_ms": latency / n,
        "regret": (oracle - hits) / n,
        "quota_units": quota,
        "quota_saved_pct": 100.0 * (1.0 - quota / main_quota) if main_quota else 0.0,
        "mix": mix,
    }


def _run_seed(seed: int, rows: Sequence[dict], tiers: Sequence[Any], alpha: float,
              lam_lat: float, lam_cost: float) -> dict[str, dict]:
    rng = random.Random(seed)
    idx = list(range(len(rows)))
    rng.shuffle(idx)
    cut = int(round(len(idx) * TRAIN_FRACTION))
    train, test = idx[:cut], idx[cut:]
    main = _main_name(tiers)
    pro = _pro_name(tiers)
    feats = [rows[i]["feat"] for i in test]

    picks: dict[str, list[str]] = {
        "always_cheapest": [tiers[0].name] * len(test),
        "always_main": [main] * len(test),
    }
    if pro is not None:
        picks["always_pro"] = [pro] * len(test)
    picks["static"] = [_with_floor(static_choice(f, tiers), f, tiers) for f in feats]
    greedy = _greedy(_train(rows, train, tiers, rng, alpha, lam_lat, lam_cost))
    picks["linucb"] = [greedy.choose(f.vector, _allowed(f, tiers)) for f in feats]
    return {name: _score(seq, rows, test, tiers, main) for name, seq in picks.items()}


def aggregate(per_seed: Sequence[dict[str, dict]]) -> dict[str, dict]:
    """Mean and population std per metric over seeds, plus the summed tier mix."""
    agg: dict[str, dict] = {}
    for policy in per_seed[0]:
        entry: dict[str, Any] = {}
        for metric in METRICS:
            values = [run[policy][metric] for run in per_seed]
            entry[metric] = {"mean": statistics.fmean(values), "std": statistics.pstdev(values)}
        mix: dict[str, int] = {}
        for run in per_seed:
            for name, count in run[policy]["mix"].items():
                mix[name] = mix.get(name, 0) + count
        entry["mix"] = dict(sorted(mix.items()))
        agg[policy] = entry
    return agg


def gate_verdict(lin_acc: float, static_acc: float, lin_quota: float,
                 static_quota: float) -> tuple[bool, str]:
    """Pass when LinUCB is within one accuracy point of static and uses no more quota."""
    passed = lin_acc >= static_acc - ACCURACY_MARGIN and lin_quota <= static_quota
    verdict = "gate passed." if passed else "gate not passed; keep Smart routing off or shadow."
    message = (
        f"LinUCB accuracy {lin_acc * 100:.1f}% vs static {static_acc * 100:.1f}%, "
        f"quota {lin_quota:.1f} vs {static_quota:.1f} units: {verdict}"
    )
    return passed, message


def _pm(stat: Mapping[str, float], scale: float, suffix: str) -> str:
    return f"{stat['mean'] * scale:.1f} ± {stat['std'] * scale:.1f}{suffix}"


def _mix_text(mix: Mapping[str, int]) -> str:
    return " / ".join(f"{name} {count}" for name, count in mix.items()) or "-"


def _markdown(agg: Mapping[str, dict], n_rows: int, seeds: int) -> str:
    lines = [
        "# Router simulation",
        "",
        f"{n_rows} tasks, {seeds} seeds, 70 % train / 30 % test per seed. Mean ± std over seeds.",
        "",
        "| Policy | Accuracy | Mean latency (ms) | Regret vs oracle | Quota units "
        "| Quota saved vs main | Tier mix (all seeds) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, m in agg.items():
        lines.append(
            f"| {name} | {_pm(m['accuracy'], 100, '%')} | {_pm(m['mean_latency_ms'], 1, '')} "
            f"| {_pm(m['regret'], 100, '%')} | {_pm(m['quota_units'], 1, '')} "
            f"| {_pm(m['quota_saved_pct'], 1, '%')} | {_mix_text(m['mix'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _resolve(out: str) -> Path:
    path = Path(out)
    return path if path.is_absolute() else ROOT / path


def _write_outputs(out: Path, agg: Mapping[str, dict], n_rows: int, seeds: int,
                   gate: Mapping[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    summary = {"tasks": n_rows, "seeds": seeds, "policies": agg}
    (out / "simulation.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (out / "SIMULATION.md").write_text(_markdown(agg, n_rows, seeds), encoding="utf-8")
    (out / "gate.json").write_text(json.dumps(gate, indent=2), encoding="utf-8")


def _parse(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bench.router.simulate",
        description="Replay routing policies over the cached oracle table.",
    )
    parser.add_argument("--seeds", type=int, default=20, help="number of random splits")
    parser.add_argument("--out", default=DEFAULT_OUT, help="output directory")
    args = parser.parse_args(argv)
    if args.seeds < 1:
        parser.error("--seeds must be at least 1")
    return args


def run(argv: Optional[Sequence[str]] = None, *, tiers: Optional[Sequence[Any]] = None,
        tasks: Optional[Sequence[Mapping[str, Any]]] = None,
        cache_root: Path = CACHE_ROOT) -> int:
    """Simulate every policy and write the outputs. Returns the process exit code."""
    args = _parse(argv)
    chosen = list(tiers) if tiers is not None else load_tiers()
    if len(chosen) < 2:
        print(TOO_FEW_TIERS_MSG)
        return EXIT_TOO_SMALL

    selected = list(tasks) if tasks is not None else load_tasks(TASKS_PATH)
    rows, incomplete = build_rows(selected, chosen, cache_root)
    if len(rows) < MIN_COMPLETE:
        counts = ", ".join(f"{name} missing {n}" for name, n in incomplete.items())
        print(f"Only {len(rows)} tasks have a reply from every tier; need {MIN_COMPLETE} "
              f"({counts}). Run python -m bench.router.collect first.")
        return EXIT_TOO_SMALL

    alpha = _config_value("ROUTER_ALPHA", 0.8)
    lam_lat = _config_value("ROUTER_LAMBDA_LATENCY", 0.15)
    lam_cost = _config_value("ROUTER_LAMBDA_COST", 0.05)
    per_seed = [_run_seed(seed, rows, chosen, alpha, lam_lat, lam_cost) for seed in range(args.seeds)]
    agg = aggregate(per_seed)

    passed, message = gate_verdict(
        agg["linucb"]["accuracy"]["mean"], agg["static"]["accuracy"]["mean"],
        agg["linucb"]["quota_units"]["mean"], agg["static"]["quota_units"]["mean"],
    )
    gate = {"passed": passed, "message": message, "ts": time.time()}
    out = _resolve(args.out)
    _write_outputs(out, agg, len(rows), args.seeds, gate)
    print(f"[bench.router] simulated {len(rows)} tasks over {args.seeds} seeds -> {out}")
    print(f"[bench.router] {message}")
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())