"""tests/test_bench_dataset.py -- golden files: schema, registered intents, ids, determinism."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import dataset  # noqa: E402
from sara.core.intent import patterns  # noqa: E402

REGISTERED = {name for name, _ in patterns._INTENT_PATTERNS}


def _golden_files():
    return sorted(dataset.GOLDEN_DIR.glob("*.jsonl"))


def test_golden_directory_has_files():
    assert _golden_files(), "bench/golden has no .jsonl files (check .gitignore negation)"


def test_all_golden_rows_load_and_use_registered_intents():
    rows = dataset.load_rows()
    assert rows
    for row in rows:
        assert row.expected_intent == "chat" or row.expected_intent in REGISTERED, row.id


def test_row_ids_are_unique():
    ids = [r.id for r in dataset.load_rows()]
    assert len(ids) == len(set(ids))


def test_every_intent_has_an_english_positive_row():
    english = {r.expected_intent for r in dataset.load_rows() if r.lang == "en" and r.kind == "positive"}
    missing = REGISTERED - english
    assert not missing, sorted(missing)


def test_malformed_line_raises_with_location(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"_defaults":{"lang":"en","kind":"positive","style":"casual"}}\n{not json}\n', encoding="utf-8")
    with pytest.raises(ValueError) as err:
        dataset.load_rows([bad])
    assert "bad.jsonl" in str(err.value)


def test_unknown_language_is_rejected(tmp_path):
    bad = tmp_path / "lang.jsonl"
    bad.write_text(
        '{"_defaults":{"lang":"xx","kind":"positive","style":"casual"}}\n'
        + json.dumps({"t": "hello", "i": "chat"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        dataset.load_rows([bad])


def test_noisy_file_matches_generator():
    from bench.tools import augment_noisy

    assert augment_noisy.main(["--check"]) in (0, None)