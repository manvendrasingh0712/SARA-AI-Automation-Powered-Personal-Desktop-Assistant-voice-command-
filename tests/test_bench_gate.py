"""Tests for the bench regression gate, baseline workflow and determinism."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import metrics, report, run_bench, runner  # noqa: E402
from bench.dataset import Row  # noqa: E402


def _row(i, text, expected, kind="positive"):
    return Row(
        id=f"t-{i:04d}", text=text, lang="en", expected_intent=expected,
        expected_groups=(), category="chat" if expected == "chat" else "apps",
        kind=kind, style="formal", note="", source="t.jsonl",
    )


ROWS = [_row(i, f"open thing {i}", "open_app") for i in range(10)] + [
    _row(10 + i, f"hello there {i}", "chat", "negative") for i in range(10)
]
TRUTH = {r.text: r.expected_intent for r in ROWS}


def _fake(chooser):
    def detect(text):
        return chooser(text), None
    detect.cache_clear = lambda: None
    return detect


def _write_thresholds(path, min_rows):
    path.write_text(json.dumps({
        "max_accuracy_drop_pts": 1.0,
        "max_false_trigger_increase_pts": 0.5,
        "max_p95_latency_increase_pct": 1e9,
        "min_rows": min_rows,
    }), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    thresholds = tmp_path / "thresholds.json"
    _write_thresholds(thresholds, 10)
    monkeypatch.setattr(run_bench, "THRESHOLDS_PATH", thresholds)
    monkeypatch.setattr(run_bench, "BASELINE_PATH", tmp_path / "baseline.json")
    monkeypatch.setattr(run_bench, "load_rows", lambda: list(ROWS))
    monkeypatch.setattr(runner, "detect_intent", _fake(lambda t: TRUTH[t]))
    monkeypatch.setattr(runner, "has_probable_tool_intent", lambda t: False)
    return tmp_path


def _args(tmp, *extra):
    return ["--out", str(tmp / "latest.json"), "--warmup", "0", *extra]


def test_gate_passes_against_itself(env, capsys):
    baseline = env / "baseline.json"
    assert run_bench.main(_args(env, "--accept-baseline")) == 0
    assert baseline.is_file()
    md = env / "RESULTS.md"
    code = run_bench.main(_args(env, "--compare", str(baseline), "--report-md", str(md)))
    assert code == 0
    assert "GATE PASSED" in capsys.readouterr().out
    assert "## Changes vs baseline" in md.read_text(encoding="utf-8")


def test_gate_fails_when_everything_routes_to_chat(env, monkeypatch, capsys):
    baseline = env / "baseline.json"
    assert run_bench.main(_args(env, "--accept-baseline")) == 0
    monkeypatch.setattr(runner, "detect_intent", _fake(lambda t: "chat"))
    assert run_bench.main(_args(env, "--compare", str(baseline))) == 2
    assert "accuracy" in capsys.readouterr().err


def test_missing_baseline_exits_3(env, capsys):
    missing = env / "missing.json"
    assert run_bench.main(_args(env, "--compare", str(missing))) == 3
    assert "--accept-baseline" in capsys.readouterr().err
    assert not missing.exists()


def test_accept_baseline_refuses_under_min_rows(env, capsys):
    _write_thresholds(env / "thresholds.json", 300)
    assert run_bench.main(_args(env, "--accept-baseline")) == 2
    assert not (env / "baseline.json").exists()
    assert "min_rows" in capsys.readouterr().err


def test_markdown_deterministic_with_markers():
    meta = {"timestamp": "t", "git_commit": "abc", "python": "3.11", "os": "w",
            "cpu": "c", "mode": "router"}
    good = [
        {"id": "x", "text": "t", "lang": "en", "expected": "a", "pred": "a",
         "groups_ok": None, "gate_open": False, "latency_ms": 1.0,
         "kind": "positive", "style": "formal", "category": "apps"}
        for _ in range(4)
    ]
    worse = [dict(r) for r in good]
    worse[0]["pred"] = "b"
    current = metrics.compute_metrics(good)
    base = metrics.compute_metrics(worse)
    first = report.render_markdown(meta, current, meta, base)
    assert first == report.render_markdown(meta, current, meta, base)
    assert report.UP in first
    assert "No baseline" in report.render_markdown(meta, current)


def _stable(payload):
    data = json.loads(json.dumps(payload))
    for rec in data["results"]:
        rec.pop("latency_ms")
    for key in ("latency", "latency_score", "composite"):
        data["metrics"].pop(key)
    return data["metrics"], data["results"]


def test_two_runs_identical(tmp_path):
    outs = []
    for name in ("a", "b"):
        out = tmp_path / f"{name}.json"
        assert run_bench.main(["--out", str(out), "--warmup", "0"]) == 0
        outs.append(_stable(json.loads(out.read_text(encoding="utf-8"))))
    assert outs[0] == outs[1]