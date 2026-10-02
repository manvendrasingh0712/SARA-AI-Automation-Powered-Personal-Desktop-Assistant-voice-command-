"""Tests for bench.metrics on tiny fixtures."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import metrics  # noqa: E402

THRESH = {
    "max_accuracy_drop_pts": 1.0,
    "max_false_trigger_increase_pts": 0.5,
    "max_p95_latency_increase_pct": 25.0,
    "min_rows": 10.0,
}


def _rec(exp, pred=None, *, lang="en", kind="positive", gate=False, groups=None,
         lat=1.0, category="apps", style="formal"):
    return {"id": "x", "text": "t", "lang": lang, "expected": exp,
            "pred": exp if pred is None else pred, "groups_ok": groups,
            "gate_open": gate, "latency_ms": lat, "kind": kind, "style": style,
            "category": category}


def _base(lat=1.0, bad_pos=0, bad_neg=0):
    rows = [_rec("open_app", "chat" if i < bad_pos else None, lat=lat) for i in range(10)]
    rows += [_rec("chat", "open_app" if i < bad_neg else None, kind="negative", lat=lat)
             for i in range(10)]
    return rows


def test_accuracy_edges():
    assert metrics.accuracy([]) == 0.0
    assert metrics.accuracy([_rec("a")]) == 1.0
    assert metrics.accuracy([_rec("a"), _rec("a", "b")]) == 0.5


def test_accuracy_by_groups_and_orders_keys():
    rows = [_rec("a", lang="hi"), _rec("a", "b", lang="en"), _rec("a", lang="en")]
    out = metrics.accuracy_by(rows, "lang")
    assert list(out) == ["en", "hi"]
    assert out["en"] == {"rows": 2, "correct": 1, "accuracy": 0.5}
    assert out["hi"]["accuracy"] == 1.0
    assert metrics.accuracy_by([], "lang") == {}


def test_per_intent_and_macro_with_empty_class():
    rows = [_rec("a"), _rec("a", "b"), _rec("b"), _rec("c", "error")]
    out = metrics.per_intent(rows)
    assert out["a"]["precision"] == 1.0
    assert out["a"]["recall"] == 0.5
    assert out["a"]["f1"] == pytest.approx(0.666667, abs=1e-6)
    assert out["b"]["precision"] == 0.5
    assert out["b"]["recall"] == 1.0
    assert out["c"]["f1"] == 0.0
    assert out["error"]["support"] == 0
    assert out["error"]["f1"] == 0.0
    assert metrics.macro_f1(out) == pytest.approx(0.444444, abs=1e-6)
    assert metrics.macro_f1({}) == 0.0
    assert metrics.per_intent([]) == {}


def test_top_confusions_order_and_limit():
    rows = [_rec("a", "b")] * 3 + [_rec("c", "d")] * 3 + [_rec("a", "c")]
    top = metrics.top_confusions(rows)
    assert top[0] == {"expected": "a", "pred": "b", "count": 3}
    assert top[1] == {"expected": "c", "pred": "d", "count": 3}
    assert top[2]["count"] == 1
    many = [_rec(f"i{n}", "x") for n in range(12)]
    assert len(metrics.top_confusions(many)) == 10
    assert metrics.top_confusions([_rec("a")]) == []


def test_false_trigger_rate():
    assert metrics.false_trigger_rate([_rec("open_app")]) == 0.0
    rows = [
        _rec("chat", "open_app", kind="negative"),
        _rec("chat", kind="negative"),
        _rec("chat", "weather", kind="adversarial"),
        _rec("open_app", "chat", kind="adversarial"),
        _rec("chat", "error", kind="negative"),
    ]
    assert metrics.false_trigger_rate(rows) == pytest.approx(2 / 4)
    assert metrics.false_trigger_rate(rows, "negative") == pytest.approx(1 / 3)
    assert metrics.false_trigger_rate(rows, "adversarial") == 1.0


def test_gate_false_open_rate():
    assert metrics.gate_false_open_rate([]) == 0.0
    rows = [_rec("chat", gate=True), _rec("chat"), _rec("open_app", gate=True)]
    assert metrics.gate_false_open_rate(rows) == 0.5


def test_group_accuracy():
    assert metrics.group_accuracy([_rec("a")]) is None
    rows = [_rec("a", groups=True), _rec("a", groups=False), _rec("a")]
    assert metrics.group_accuracy(rows) == 0.5
    assert metrics.group_rows(rows) == 2


def test_percentile_nearest_rank():
    assert metrics.percentile([], 95) == 0.0
    assert metrics.percentile([7.0], 99) == 7.0
    values = [float(n) for n in range(10, 0, -1)]
    assert metrics.percentile(values, 50) == 5.0
    assert metrics.percentile(values, 95) == 10.0
    assert metrics.percentile([float(n) for n in range(1, 21)], 95) == 19.0


def test_latency_score_clamps():
    assert metrics.latency_score(0.0) == 1.0
    assert metrics.latency_score(100.0) == 0.5
    assert metrics.latency_score(200.0) == 0.0
    assert metrics.latency_score(400.0) == 0.0


def test_composite_with_and_without_groups():
    assert metrics.composite(1.0, 0.0, 1.0, 1.0) == pytest.approx(1.0)
    assert metrics.composite(0.8, 0.1, 0.5, 0.5) == pytest.approx(0.73)
    assert metrics.composite(0.8, 0.1, None, 0.5) == pytest.approx(0.7875)


def test_compute_metrics_empty_and_single_row():
    empty = metrics.compute_metrics([])
    assert empty["rows"] == 0
    assert empty["composite"] == 0.0
    assert empty["group_accuracy"] is None
    assert empty["latency"] == {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    one = metrics.compute_metrics([_rec("open_app", lat=3.0)])
    assert one["rows"] == 1
    assert one["accuracy"] == 1.0
    assert one["false_trigger_rate"] == 0.0
    assert one["latency"]["p99"] == 3.0
    assert 0.0 < one["composite"] <= 1.0


def test_check_gate_pass_and_each_failure():
    base = metrics.compute_metrics(_base())
    assert metrics.check_gate(base, base, THRESH) == []
    acc = metrics.check_gate(metrics.compute_metrics(_base(bad_pos=1)), base, THRESH)
    assert any("accuracy" in f for f in acc)
    trig = metrics.check_gate(metrics.compute_metrics(_base(bad_neg=1)), base, THRESH)
    assert any("false-trigger" in f for f in trig)
    slow = metrics.check_gate(metrics.compute_metrics(_base(lat=2.0)), base, THRESH)
    assert any("p95" in f for f in slow)
    few = dict(THRESH, min_rows=100.0)
    assert any("rows" in f for f in metrics.check_gate(base, base, few))