"""Offline router benchmark: checkers, collection, simulation, and tasks file validity."""

import json
from pathlib import Path

import pytest

from bench.router import collect, simulate
from bench.router.check import check_answer, is_valid_check
from sara.core.router.tiers import Tier

TASKS_PATH = Path(__file__).resolve().parents[1] / "bench" / "router" / "tasks.jsonl"


def _tiers():
    return [
        Tier(name="cloud_fast", backend="gemini", model="fast-model", cost=1.0),
        Tier(name="cloud_main", backend="gemini", model="main-model", cost=2.0),
    ]


# ── check.py ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, check, expected", [
    ("The answer is 391.", {"kind": "number", "value": 391, "tol": 0}, True),
    ("1,024", {"kind": "number", "value": 1024, "tol": 0}, True),
    ("390", {"kind": "number", "value": 391, "tol": 0}, False),
    ("Paris is it", {"kind": "contains_any", "values": ["paris"]}, True),
    ("London", {"kind": "contains_any", "values": ["paris"]}, False),
    ('"Paris."', {"kind": "equals", "value": "paris"}, True),
    ("Paris, France", {"kind": "equals", "value": "paris"}, False),
    ('```json\n{"a": 1, "b": "X"}\n```', {"kind": "json_fields", "fields": {"a": 1, "b": "x"}}, True),
    ('{"a": 2}', {"kind": "json_fields", "fields": {"a": 1}}, False),
    ("{not json", {"kind": "json_fields", "fields": {"a": 1}}, False),
    ("Yes.", {"kind": "one_word"}, True),
    ("two words", {"kind": "one_word"}, False),
    ("hello there", {"kind": "all_lower"}, True),
    ("Hello", {"kind": "all_lower"}, False),
    ("- a\n- b", {"kind": "bullets", "n": 2}, True),
    ("* a\n\u2022 b", {"kind": "bullets", "n": 2}, True),
    ("- a\n- b", {"kind": "bullets", "n": 3}, False),
    ("one two three", {"kind": "max_words", "n": 3}, True),
    ("one two three four", {"kind": "max_words", "n": 3}, False),
    ("  Namaste friend", {"kind": "starts_with", "value": "namaste"}, True),
    ("Hi there", {"kind": "starts_with", "value": "namaste"}, False),
    ("Sure 42", {"kind": "all", "checks": [
        {"kind": "number", "value": 42, "tol": 0}, {"kind": "max_words", "n": 3}]}, True),
    ("Sure 42 right now always", {"kind": "all", "checks": [
        {"kind": "number", "value": 42, "tol": 0}, {"kind": "max_words", "n": 3}]}, False),
    ("42", {"kind": "unknown_kind"}, False),
    (None, {"kind": "one_word"}, False),
])
def test_check_answer_accepts_and_rejects(text, check, expected):
    assert check_answer(text, check) is expected


def test_is_valid_check_recurses_into_all():
    assert is_valid_check({"kind": "all", "checks": [{"kind": "one_word"}]})
    assert not is_valid_check({"kind": "all", "checks": [{"kind": "bogus"}]})
    assert not is_valid_check({"kind": "all", "checks": []})


def test_tasks_file_is_valid():
    rows = [json.loads(line) for line in TASKS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 80
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
    assert all(is_valid_check(r["check"]) for r in rows)


# ── collect.py ────────────────────────────────────────────────────────

class _Rate(Exception):
    code = 429


def test_backoff_retries_then_succeeds():
    delays = []
    limiter = collect.RateLimiter(6000, clock=lambda: 0.0, sleeper=lambda s: None)
    calls = {"n": 0}

    def caller(tier, prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Rate("slow down")
        return "42"

    ok, text, _, error = collect.call_with_backoff(caller, _tiers()[0], "p", limiter, delays.append)
    assert ok is True and text == "42" and error is None
    assert delays == [collect.BACKOFF_BASE_S]


def test_backoff_gives_up_after_max_retries():
    delays = []
    limiter = collect.RateLimiter(6000, clock=lambda: 0.0, sleeper=lambda s: None)

    def caller(tier, prompt):
        raise _Rate("quota")

    ok, text, _, error = collect.call_with_backoff(caller, _tiers()[0], "p", limiter, delays.append)
    assert ok is False and text == "" and error == "rate_limited"
    assert delays == [4.0, 8.0, 16.0, 32.0]


def test_collect_dry_run_is_resumable(tmp_path):
    tasks = [{"id": f"T{i}", "prompt": f"question {i}"} for i in range(3)]
    calls = {"n": 0}

    def counting(tier, prompt):
        calls["n"] += 1
        return "ok"

    argv = ["--rpm", "6000"]
    code = collect.run(argv, tiers=_tiers(), tasks=tasks, caller=counting,
                       cache_root=tmp_path, sleeper=lambda s: None, clock=lambda: 0.0)
    assert code == collect.EXIT_OK
    assert calls["n"] == 6
    cell = json.loads((tmp_path / "cloud_fast" / "T0.json").read_text(encoding="utf-8"))
    assert set(cell) == {"text", "latency_ms", "ok", "error"}

    calls["n"] = 0
    collect.run(argv, tiers=_tiers(), tasks=tasks, caller=counting,
                cache_root=tmp_path, sleeper=lambda s: None, clock=lambda: 0.0)
    assert calls["n"] == 0


def test_collect_dry_run_uses_fake_model(tmp_path):
    tasks = [{"id": "T0", "prompt": "question"}]
    code = collect.run(["--dry-run", "--rpm", "6000"], tiers=_tiers(), tasks=tasks,
                       cache_root=tmp_path, sleeper=lambda s: None, clock=lambda: 0.0)
    assert code == collect.EXIT_OK
    assert (tmp_path / "cloud_main" / "T0.json").exists()


def test_collect_needs_two_tiers(tmp_path, capsys):
    code = collect.run(["--dry-run"], tiers=_tiers()[:1], tasks=[{"id": "T0", "prompt": "q"}],
                       cache_root=tmp_path)
    assert code == collect.EXIT_ONE_TIER
    assert "Only one model tier" in capsys.readouterr().out


# ── simulate.py ───────────────────────────────────────────────────────

def _synthetic(tmp_path, count=80):
    tasks = [{"id": f"S{i:03d}", "type": "math",
              "prompt": f"What is {i} plus 1? Reply with only the number.",
              "check": {"kind": "number", "value": i + 1, "tol": 0}} for i in range(count)]
    for task in tasks:
        i = int(task["id"][1:])
        fast_text = str(i + 1) if i % 3 else "999"
        cells = {
            "cloud_fast": {"text": fast_text, "latency_ms": 800.0, "ok": True, "error": None},
            "cloud_main": {"text": str(i + 1), "latency_ms": 2000.0, "ok": True, "error": None},
        }
        for name, cell in cells.items():
            simulate_path = collect.cell_path(tmp_path, name, task["id"])
            collect.write_cell(simulate_path, cell)
    return tasks


def test_gate_verdict_pass_and_fail():
    assert simulate.gate_verdict(0.90, 0.90, 1.0, 2.0)[0] is True
    assert simulate.gate_verdict(0.80, 0.90, 1.0, 2.0)[0] is False
    assert simulate.gate_verdict(0.95, 0.90, 3.0, 2.0)[0] is False


def test_simulate_writes_outputs_deterministically(tmp_path):
    cache = tmp_path / "cache"
    tasks = _synthetic(cache)
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    assert simulate.run(["--seeds", "3", "--out", str(out_a)], tiers=_tiers(), tasks=tasks,
                        cache_root=cache) == simulate.EXIT_OK
    assert simulate.run(["--seeds", "3", "--out", str(out_b)], tiers=_tiers(), tasks=tasks,
                        cache_root=cache) == simulate.EXIT_OK

    summary = json.loads((out_a / "simulation.json").read_text(encoding="utf-8"))
    assert {"always_cheapest", "always_main", "static", "linucb"} <= set(summary["policies"])
    assert (out_a / "SIMULATION.md").exists()
    assert (out_a / "simulation.json").read_text(encoding="utf-8") == \
        (out_b / "simulation.json").read_text(encoding="utf-8")

    gate = json.loads((out_a / "gate.json").read_text(encoding="utf-8"))
    assert isinstance(gate["passed"], bool)
    assert isinstance(gate["message"], str) and gate["message"]


def test_simulate_exit_three_with_one_tier(tmp_path):
    assert simulate.run(["--out", str(tmp_path)], tiers=_tiers()[:1], tasks=[],
                        cache_root=tmp_path) == simulate.EXIT_TOO_SMALL


def test_simulate_exit_three_with_too_few_complete_tasks(tmp_path):
    tasks = _synthetic(tmp_path, count=10)
    assert simulate.run(["--out", str(tmp_path / "o")], tiers=_tiers(), tasks=tasks,
                        cache_root=tmp_path) == simulate.EXIT_TOO_SMALL