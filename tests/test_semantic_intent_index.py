"""Audit part 2, O1: the semantic index (not wired in: measured too weak, see docs/audit/OPTIMIZATIONS_REPORT.md)."""
import numpy as np

from sara.core.intent.semantic import SemanticIntentIndex

VECS = {"volume up": [1, 0, 0], "louder please": [0.95, 0.1, 0], "next song": [0, 1, 0], "skip it": [0.1, 0.96, 0],
        "wipe disk": [0, 0, 1], "hello there": [0.3, 0.3, 0.3]}


def embed(t):
    v = VECS.get(t)
    return None if v is None else np.array(v, dtype=np.float32)


def tier(i):
    return {"set_volume": 1, "next_track": 1, "wipe": 3}[i]


def _index():
    idx = SemanticIntentIndex(tau_high=0.9, margin=0.05)
    idx.build({"set_volume": ["volume up"], "next_track": ["next song"], "wipe": ["wipe disk"], "chat": ["hello there"]}, embed, tier)
    return idx


def test_only_low_risk_intents_are_indexed():
    idx = _index()
    assert set(idx._labels) == {"set_volume", "next_track"}


def test_close_phrase_is_classified_and_far_phrase_is_not():
    idx = _index()
    assert idx.classify("louder please", embed)[0] == "set_volume"
    assert idx.classify("skip it", embed)[0] == "next_track"
    assert idx.classify("hello there", embed)[0] is None      # no clear winner


def test_unknown_embedding_gives_none():
    assert _index().classify("never seen", embed)[0] is None
