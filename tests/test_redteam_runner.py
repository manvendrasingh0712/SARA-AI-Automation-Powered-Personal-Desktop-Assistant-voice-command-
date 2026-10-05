"""Tests for bench.redteam_runner (fake detector/policy, temp dirs, no network)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import redteam_runner as rt  # noqa: E402
from bench import report  # noqa: E402

TIERS = {"weather": 0, "open_app": 1, "shutdown_system": 3}


def make_sec(allow=()):
    return SimpleNamespace(
        detector=SimpleNamespace(
            scan=lambda text: SimpleNamespace(flagged=any(w in text for w in ("evil", "ev il")))),
        policy=SimpleNamespace(
            decide=lambda tool, origin, tainted=None, mode=None: SimpleNamespace(
                decision="allow" if tool in allow else "deny"),
            is_sensitive_path=lambda p: "secret" in p),
        tiers=SimpleNamespace(TIERS=TIERS, tier_of=lambda n: TIERS.get(n, 2)),
    )


ATTACKS = [
    {"id": "a1", "cat": "x", "src": "web", "goal": "open_app", "covered": True, "t": "so evil"},
    {"id": "a2", "cat": "x", "src": "web", "goal": "open_app", "covered": True, "t": "calm"},
    {"id": "a3", "cat": "y", "src": "web", "goal": "shutdown_system", "covered": True, "parts": ["ev", "il"]},
    {"id": "a4", "cat": "z", "src": "web", "goal": "exfil", "covered": False, "t": "calm"},
    {"id": "a5", "cat": "y", "src": "file", "goal": "read_file", "covered": True, "t": "calm",
     "path": "C:/secret/key"},
]
BENIGN = [
    {"id": "b1", "cat": "p", "src": "web", "t": "hello"},
    {"id": "b2", "cat": "p", "src": "web", "t": "evil but benign"},
    {"id": "b3", "cat": "q", "src": "note", "t": "fine"},
    {"id": "b4", "cat": "q", "src": "note", "t": "fine too"},
]
GOOD_ATTACKS = [ATTACKS[0], ATTACKS[2], ATTACKS[4]]
CLEAN_BENIGN = [BENIGN[0], BENIGN[2], BENIGN[3]]


def test_metrics_maths_and_split_chunks():
    res = rt.evaluate(ATTACKS, BENIGN, make_sec(allow=("open_app",)))
    assert res["rows"] == {"attacks": 5, "covered": 4, "uncovered": 1, "benign": 4}
    assert res["recall"]["covered"] == 0.75
    assert res["recall"]["by_cat"]["x"]["rate"] == 0.5
    assert res["recall"]["by_cat"]["y"]["rate"] == 1.0
    assert res["asr"]["overall"] == 0.4
    assert res["asr"]["covered"] == 0.25
    assert res["success_ids"] == ["a2", "a4"]
    assert res["missed_ids"] == ["a2"]
    assert res["uncovered"] == {"rows": 1, "detected": 0, "recall": 0.0, "missed_ids": ["a4"]}
    assert res["fpr"]["overall"] == 0.25
    assert res["fpr"]["by_cat"]["p"]["rate"] == 0.5
    assert res["fpr"]["ids"] == ["b2"]
    assert res["latency_ms"]["p50"] is not None and res["latency_ms"]["p95"] is not None


def test_split_chunk_only_flagged_when_joined():
    row = {"parts": ["ev", "il"]}
    assert rt.texts_of(row) == ["ev", "il", "ev il"]
    assert rt.is_flagged(row, make_sec()) is True
    assert rt.is_flagged({"parts": ["ev", "x"]}, make_sec()) is False


def test_invariant_detects_violation():
    assert rt.check_invariant(make_sec())["ok"] is True
    bad = rt.check_invariant(make_sec(allow=("open_app",)))
    assert bad["ok"] is False
    assert "open_app/llm_tool" in bad["violations"]


def test_determinism():
    first = rt.evaluate(ATTACKS, BENIGN, make_sec())
    second = rt.evaluate(ATTACKS, BENIGN, make_sec())
    first.pop("latency_ms")
    second.pop("latency_ms")
    assert first == second


def test_check_gate_messages():
    res = rt.evaluate(ATTACKS, BENIGN, make_sec(allow=("open_app",)))
    text = " | ".join(rt.check_gate(res))
    assert "recall" in text and "attack success" in text
    assert "false-positive" in text and "invariant" in text
    assert rt.check_gate(rt.evaluate(GOOD_ATTACKS, CLEAN_BENIGN, make_sec())) == []


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture()
def data(tmp_path, monkeypatch):
    attacks, benign = tmp_path / "attacks.jsonl", tmp_path / "benign.jsonl"
    _write(attacks, GOOD_ATTACKS)
    _write(benign, CLEAN_BENIGN)
    monkeypatch.setattr(rt, "ATTACKS", attacks)
    monkeypatch.setattr(rt, "BENIGN", benign)
    monkeypatch.setattr(rt, "load_security", lambda: make_sec())
    return tmp_path


def test_gate_exit_0_and_outputs(data):
    md = data / "RESULTS.md"
    md.write_text("# SARA-Bench results\n\n## Headline\n\nkept\n", encoding="utf-8")
    assert rt.run(data / "redteam.json", gate=True, report_md=md) == 0
    payload = json.loads((data / "redteam.json").read_text(encoding="utf-8"))
    assert payload["metrics"]["recall"]["covered"] == 1.0
    text = md.read_text(encoding="utf-8")
    assert "## Security" in text and "## Headline" in text and "kept" in text


def test_gate_exit_2_on_low_recall(data, capsys):
    _write(rt.ATTACKS, GOOD_ATTACKS + [{"id": "m", "cat": "x", "goal": "weather", "covered": True, "t": "calm"}])
    assert rt.run(data / "out.json", gate=True, report_md=data / "R.md") == 2
    assert "recall" in capsys.readouterr().err


def test_gate_exit_2_on_invariant(data, monkeypatch, capsys):
    monkeypatch.setattr(rt, "load_security", lambda: make_sec(allow=("open_app",)))
    assert rt.run(data / "out.json", gate=True, report_md=data / "R.md") == 2
    assert "invariant" in capsys.readouterr().err


def test_exit_4_without_security(data, monkeypatch):
    monkeypatch.setattr(rt, "load_security", lambda: None)
    assert rt.run(data / "out.json", gate=True, report_md=data / "R.md") == 4


def test_no_gate_never_fails_on_metrics(data):
    _write(rt.ATTACKS, [{"id": "m", "cat": "x", "goal": "weather", "covered": True, "t": "calm"}])
    assert rt.run(data / "out.json", gate=False, report_md=data / "R.md") == 0


def test_update_security_section_replaces_only_its_own(tmp_path):
    md = tmp_path / "RESULTS.md"
    md.write_text("# T\n\n## A\n\none\n\n## Security\n\nold\n\n## B\n\ntwo\n", encoding="utf-8")
    report.update_security_section(md, "## Security\n\nnew\n")
    text = md.read_text(encoding="utf-8")
    assert "old" not in text and "new" in text
    assert "one" in text and "two" in text and text.count("## Security") == 1