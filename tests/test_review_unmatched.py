"""Audit part 2, O8: clustering of the unmatched-query log."""
import importlib.util
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("review_unmatched", ROOT / "scripts" / "review_unmatched.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_similar_phrases_cluster_and_the_biggest_comes_first():
    m = _mod()
    counts = Counter({"thoda shor kam kar do": 3, "shor thoda kam karo": 2, "mausam kaisa rahega": 1})
    clusters = m.cluster(counts)
    assert sum(clusters[0][1].values()) == 5 and len(clusters) == 2


def test_accept_writes_a_golden_file(tmp_path, monkeypatch):
    m = _mod()
    log = tmp_path / "u.jsonl"
    log.write_text("\n".join(json.dumps({"text": t}) for t in ["thoda shor kam kar do"] * 2 + ["shor kam karo"]), encoding="utf-8")
    out = tmp_path / "learned.jsonl"
    monkeypatch.setattr(m, "OUT", out)
    assert m.main(["--log", str(log), "--accept", "1:set_volume"]) == 0
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert "_defaults" in rows[0] and {r["i"] for r in rows[1:]} == {"set_volume"} and len(rows) >= 2
