"""tests/test_security_redteam.py -- detector quality on bench/redteam (recall, false positives, speed)."""

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sara.core.security import detector, policy  # noqa: E402

RED = ROOT / "bench" / "redteam"


def _rows(name):
    with open(RED / name, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _detected(row):
    texts = [row["t"]] if "t" in row else list(row["parts"]) + [" ".join(row["parts"])]
    if any(detector.scan(t).flagged for t in texts):
        return True
    return bool(row.get("path")) and policy.is_sensitive_path(row["path"])


def test_recall_on_covered_attacks_is_at_least_90_percent():
    covered = [r for r in _rows("attacks.jsonl") if r["covered"]]
    missed = [r["id"] for r in covered if not _detected(r)]
    recall = 1 - len(missed) / len(covered)
    assert recall >= 0.90, f"recall {recall:.3f}, missed {missed}"


def test_every_attack_category_has_recall_of_at_least_80_percent():
    by_cat = {}
    for r in _rows("attacks.jsonl"):
        if r["covered"]:
            by_cat.setdefault(r["cat"], []).append(_detected(r))
    weak = {c: sum(v) / len(v) for c, v in by_cat.items() if sum(v) / len(v) < 0.80}
    assert not weak, weak


def test_false_positive_rate_on_benign_is_at_most_2_percent():
    benign = _rows("benign.jsonl")
    flagged = [r["id"] for r in benign if detector.scan(r["t"]).flagged]
    assert len(flagged) / len(benign) <= 0.02, flagged


def test_scan_of_20kb_text_is_fast():
    text = "Weather report today. Local news and sports updates follow below with more text. " * 250
    times = []
    for _ in range(20):
        start = time.perf_counter()
        detector.scan(text)
        times.append((time.perf_counter() - start) * 1000.0)
    assert statistics.median(times) <= 5.0, statistics.median(times)


def test_single_weak_signal_does_not_flag():
    assert not detector.scan("Please do not tell anyone about the party, it is a secret.").flagged
    assert not detector.scan("Press Alt+F4 to close a window quickly.").flagged
