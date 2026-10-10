"""Tests for the memory benchmark: loader, metric maths, baseline, runner with stubbed Memory 2.0, gate."""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import report  # noqa: E402
from bench.memory import loader, metrics, runner  # noqa: E402
from bench.memory.baseline import VectorBaseline  # noqa: E402


def _turn(ts, text, facts=(), events=()):
    return {"ts": ts, "text": text, "facts": list(facts), "events": list(events)}


def _q(qid, q, qtype, expect, **extra):
    return {"id": qid, "q": q, "type": qtype, "at": "2026-03-25T09:00:00", "expect": expect, **extra}


def _fact(obj):
    return {"subject": "user", "predicate": "lives_in", "object": obj}


SCENARIOS = [
    {"id": "T1", "lang": "en", "turns": [
        _turn("2026-02-02T10:00:00", "I live in Ajmer", [_fact("Ajmer")],
              [{"summary": "Trip to Goa", "ts": "2026-02-02T11:00:00"}]),
        _turn("2026-03-11T18:30:00", "I moved to Jaipur", [_fact("Jaipur")])],
     "questions": [
         _q("T1Q1", "where do I live now?", "contradiction", {"any": ["Jaipur"]}, forbid=["Ajmer"]),
         _q("T1Q2", "what is my blood group zzzz", "abstain", {"abstain": True}),
         _q("T1Q3", "tell me about the trip", "single", {"any": ["Goa"]})]},
    {"id": "T2", "lang": "hinglish", "turns": [
        _turn("2026-02-03T10:00:00", "Main Kota mein rehta hoon", [_fact("Kota")]),
        _turn("2026-03-12T18:30:00", "Ab main Udaipur shift ho gaya", [_fact("Udaipur")])],
     "forget": [{"after_turn": 2, "phrase": "Udaipur", "forbid": ["Udaipur"]}],
     "questions": [
         _q("T2Q1", "which city do I live in", "forget", {"none_of": ["Udaipur"]}, forbid=["Udaipur"]),
         _q("T2Q2", "what is my blood group zzzz", "abstain", {"abstain": True})]},
]


def _write(tmp_path, scenarios=SCENARIOS):
    path = tmp_path / "scenarios.jsonl"
    path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in scenarios), encoding="utf-8")
    return path


class FakeStore:
    def __init__(self, db_path=None, embed=None, clock=None):
        self.facts = []

    def upsert_fact(self, subject, predicate, obj, **kwargs):
        if predicate == "lives_in":
            self.facts = [t for t in self.facts if " lives_in " not in t]
        self.facts.append(f"{subject} {predicate} {obj}")

    def add_event(self, summary, ts, **kwargs):
        self.facts.append(summary)

    def close(self):
        self.facts = []


def _retrieve(query, *, store=None, now=None, embed=None):
    words = [w.casefold() for w in query.split() if len(w) > 3]
    return [SimpleNamespace(text=t) for t in store.facts if any(w in t.casefold() for w in words)]


def _find(phrase, *, store=None, embed=None, limit=10):
    return [t for t in store.facts if phrase.casefold() in t.casefold()]


def _retract(hits, *, store=None):
    store.facts = [t for t in store.facts if t not in hits]
    return len(hits)


def _no_retract(hits, *, store=None):
    return 0


def _modules(leak=False):
    return SimpleNamespace(retrieve=_retrieve, find_matches=_find,
                           retract_hits=_no_retract if leak else _retract, store_cls=FakeStore)


def _run(tmp_path, monkeypatch, leak=False, name="a", **kwargs):
    monkeypatch.setattr(runner, "load_modules", lambda: _modules(leak))
    out = tmp_path / f"{name}.json"
    code = runner.run(out, report_md=tmp_path / f"{name}.md",
                      scenarios_path=_write(tmp_path), **kwargs)
    return code, out


def _rec(qtype, passed, rank=None, hits=0, leak=False, ms=1.0):
    return {"id": "x", "scenario": "s", "lang": "en", "type": qtype, "passed": passed,
            "rank": rank, "hits": hits, "leak": leak, "latency_ms": ms}


RECORDS = [
    _rec("single", True, 1, 1, ms=1.0), _rec("single", True, 3, 3, ms=2.0),
    _rec("single", False, None, 2, ms=3.0), _rec("abstain", False, None, 2, ms=4.0),
    _rec("abstain", True, None, 0, ms=5.0), _rec("forget", False, None, 1, True, ms=6.0),
]
GOOD = {"forgetting_leak_rate": 0.0, "recall_at_3": 0.8, "abstention_accuracy": 0.9,
        "false_memory_rate": 0.1}


def test_real_scenarios_file():
    scenarios = loader.load_scenarios()
    questions = [q for s in scenarios for q in s["questions"]]
    assert len(scenarios) >= 30 and len(questions) >= 120
    assert len({s["id"] for s in scenarios}) == len(scenarios)
    assert len({q["id"] for q in questions}) == len(questions)
    assert {q["type"] for q in questions} <= loader.QUESTION_TYPES
    assert all("expect" in q for q in questions)


def test_loader_rejects_bad_rows(tmp_path):
    def copy():
        return json.loads(json.dumps(SCENARIOS[0]))

    no_expect, bad_type = copy(), copy()
    del no_expect["questions"][0]["expect"]
    bad_type["questions"][0]["type"] = "weird"
    for rows, text in (([copy(), copy()], "duplicate"), ([no_expect], "expect"), ([bad_type], "unknown type")):
        with pytest.raises(ValueError, match=text):
            loader.load_scenarios(_write(tmp_path, rows))
    with pytest.raises(ValueError):
        loader.load_scenarios(tmp_path / "missing.jsonl")


def test_judge_rules():
    contra = {"type": "contradiction", "expect": {"any": ["Jaipur"]}, "forbid": ["Ajmer"]}
    assert metrics.judge(contra, ["lives in JAIPUR"])["passed"] is True
    assert metrics.judge(contra, ["lives in Ajmer", "lives in Jaipur"])["passed"] is False
    assert metrics.judge({"type": "single", "expect": {"any": ["x"]}}, ["a", "b", "c", "x"]) == {
        "passed": False, "rank": 4, "leak": False}
    assert metrics.judge({"type": "contradiction_history", "expect": {"any": ["a"]}}, [])["passed"] is False
    assert metrics.judge({"type": "abstain", "expect": {"abstain": True}}, [])["passed"] is True
    assert metrics.judge({"type": "abstain", "expect": {"abstain": True}}, ["x"])["passed"] is False
    forget = {"type": "forget", "expect": {"none_of": ["Jaipur"]}}
    assert metrics.judge(forget, ["likes tea"])["passed"] is True
    assert metrics.judge(forget, ["lives in jaipur"]) == {"passed": False, "rank": None, "leak": True}


def test_metric_maths():
    overall = metrics.compute_metrics(RECORDS)["overall"]
    assert overall["recall_at_1"] == pytest.approx(1 / 3, abs=1e-3)
    assert overall["recall_at_3"] == pytest.approx(2 / 3, abs=1e-3)
    assert overall["recall_at_5"] == pytest.approx(2 / 3, abs=1e-3)
    assert overall["mrr"] == pytest.approx(4 / 9, abs=1e-3)
    assert overall["false_memory_rate"] == 0.5 and overall["abstention_accuracy"] == 0.5
    assert overall["forgetting_leak_rate"] == 1.0 and overall["accuracy"] == 0.5
    assert overall["contradiction_accuracy"] is None
    result = metrics.compute_metrics(RECORDS)
    assert result["latency_ms"] == {"p50": 3.0, "p95": 6.0}
    assert sorted(result["by_type"]) == ["abstain", "forget", "single"]
    assert result["by_type"]["abstain"]["recall_at_3"] is None
    assert metrics.percentile([], 50) is None


def test_gate_rules():
    assert metrics.check_gate(GOOD, GOOD) == []
    for key, value, text in (("forgetting_leak_rate", 0.1, "forgetting leak"), ("recall_at_3", 0.7, "recall@3"),
                             ("abstention_accuracy", 0.8, "abstention"), ("false_memory_rate", 0.2, "false-memory")):
        failures = metrics.check_gate({**GOOD, key: value}, GOOD)
        assert len(failures) == 1 and text in failures[0]
    assert metrics.check_gate({**GOOD, "recall_at_3": None}, GOOD) == []


def test_baseline_mechanics():
    vec = {"a": [1.0, 0.0], "b": [0.0, 1.0], "ab": [1.0, 1.0], "zero": [0.0, 0.0]}

    def toy(text):
        return np.array(vec[text], dtype=np.float32) if text in vec else None

    base = VectorBaseline(toy, k=5, floor=0.30)
    for text in ("a", "b", "zero", "unknown"):
        base.add(text)
    assert base.query("ab") == ["a", "b"] and base.query("a") == ["a"]
    assert VectorBaseline(toy, k=1).query("ab") == []
    assert base.forget("unknown") is False
    assert base.forget("ab") is True and base.query("a") == []
    assert base.forget("ab") is True and base.forget("ab") is False
    tight = VectorBaseline(toy, floor=0.8)
    tight.add("a")
    assert tight.query("ab") == []


def test_evaluate_both_systems(tmp_path):
    embed = runner.make_embedder("hash")
    ticks = itertools.count()
    records = runner.evaluate_memory2(SCENARIOS, embed, _modules(), tmp_path, lambda: next(ticks) * 0.002)
    assert [r["id"] for r in records] == ["T1Q1", "T1Q2", "T1Q3", "T2Q1", "T2Q2"]
    assert all(r["passed"] for r in records)
    assert all(r["latency_ms"] == pytest.approx(2.0) for r in records)
    assert set(records[0]) == {"id", "scenario", "lang", "type", "passed", "rank", "hits", "leak", "latency_ms"}
    base = runner.evaluate_baseline(SCENARIOS, embed)
    assert [r["id"] for r in base] == [r["id"] for r in records]


def test_run_gate_passes(tmp_path, monkeypatch):
    code, out = _run(tmp_path, monkeypatch, gate=True)
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["metrics"]["memory2"]["overall"]["forgetting_leak_rate"] == 0.0
    assert "## Memory" in (tmp_path / "a.md").read_text(encoding="utf-8")


def test_run_gate_fails_on_leak(tmp_path, monkeypatch, capsys):
    code, _ = _run(tmp_path, monkeypatch, leak=True, gate=True)
    assert code == 2 and "forgetting leak" in capsys.readouterr().err
    assert _run(tmp_path, monkeypatch, leak=True, name="b")[0] == 0


def test_run_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, "load_modules", lambda: None)
    assert runner.run(tmp_path / "o.json", report_md=tmp_path / "o.md", scenarios_path=_write(tmp_path)) == 4
    assert "missing" in capsys.readouterr().err
    monkeypatch.setattr(runner, "load_modules", lambda: _modules())
    monkeypatch.setattr(runner, "make_embedder", lambda name: None)
    assert runner.run(tmp_path / "o.json", report_md=tmp_path / "o.md", scenarios_path=_write(tmp_path)) == 4
    assert runner.run(tmp_path / "o.json", extract="llm") == 2
    assert runner.run(tmp_path / "o.json", scenarios_path=tmp_path / "none.jsonl") == 2


def _strip(payload):
    for system in payload["results"].values():
        for record in system:
            record.pop("latency_ms")
    for system in payload["metrics"].values():
        system.pop("latency_ms")
    return payload


def test_determinism(tmp_path, monkeypatch):
    runs = []
    for name in ("one", "two"):
        _, out = _run(tmp_path, monkeypatch, name=name)
        runs.append(_strip(json.loads(out.read_text(encoding="utf-8"))))
    assert runs[0] == runs[1]


def test_report_section_replaces_in_place(tmp_path):
    data = metrics.compute_metrics(RECORDS)
    meta = {"embedder": "hash", "extract": "gold", "scenarios": 2, "questions": 6}
    text = report.render_memory(meta, data, data)
    assert "| Metric | Baseline | Memory 2.0 | Change |" in text and "Recall@3" in text
    path = tmp_path / "RESULTS.md"
    path.write_text("# SARA-Bench results\n\n## Security\n\nkeep me\n", encoding="utf-8")
    report.update_memory_section(path, text)
    report.update_memory_section(path, text)
    content = path.read_text(encoding="utf-8")
    assert content.count("## Memory") == 1 and "keep me" in content and "## Security" in content