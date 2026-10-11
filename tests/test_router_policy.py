"""Router policy and tiers: legacy equivalence, floors, cascade, loader."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sara.core.router import features
from sara.core.router.features import FEATURE_DIM, Features, extract_features
from sara.core.router.policy import (
    HIGH_COMPLEXITY,
    cascade_next,
    floor_tier,
    is_simple_query,
    static_choice,
)
from sara.core.router.tiers import Tier, load_tiers

KEYWORDS = (
    "volume brightness mute unmute time date battery pause resume stop skip next "
    "previous louder quieter screenshot"
).split()

HANDWRITTEN = [
    "what time is it", "set volume to 50", "sometimes I feel tired", "tell me a joke",
    "explain how photosynthesis works in detail", "what's the date today",
    "mute", "next song please", "stop", "who won the match yesterday",
    "volume badha do", "aaj ka time kya hai", "battery kitni bachi hai",
    "mujhe ek kahani sunao", "kal ka mausam kaisa rahega", "screenshot le lo yaar",
    "write a poem about the sea", "what is the difference between TCP and UDP",
    "समय क्या हुआ है", "आवाज़ कम करो", "मुझे एक कविता सुनाओ", "बैटरी कितनी बची है",
    "pause the music and then tell me the weather in Jaipur tomorrow",
    "resume", "previous track", "hello", "how are you", "thanks", "",
]

TIERS_G = [
    Tier("cloud_fast", "gemini", "f", 1.0),
    Tier("cloud_main", "gemini", "m", 2.0),
    Tier("cloud_pro", "gemini", "p", 6.0),
]
TIERS_O = [Tier("local_fast", "ollama", "lf", 0.0), Tier("local_main", "ollama", "lm", 0.5)]


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(features, "_detect_fn", None)
    monkeypatch.setattr(features, "_tool_fn", lambda text: False)
    monkeypatch.setattr(features, "_resolved", True)


def _corpus() -> list:
    out = list(HANDWRITTEN)
    for w in KEYWORDS:
        out += [w + " x" * k for k in range(9)]
        out += [f"please {w} now", f"{w} karo yaar", f"{w} बढ़ाओ", f"can you {w} it for me today"]
    out += [f"set volume to {n}" for n in range(0, 100, 5)]
    golden = Path(__file__).resolve().parents[1] / "bench" / "golden"
    try:
        for path in sorted(golden.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                for key in ("text", "prompt", "utterance", "input"):
                    if isinstance(row.get(key), str):
                        out.append(row[key])
                        break
    except Exception:
        pass
    return out


def _feat(**kw) -> Features:
    base = dict(
        vector=np.zeros(FEATURE_DIM), needs_tools=False, lang="en",
        complexity=0.0, think_cue=False, simple=False, followup=False,
    )
    base.update(kw)
    return Features(**base)


def _cfg(**kw):
    base = dict(
        LLM_BACKEND="gemini", GEMINI_MODEL="m", GEMINI_FAST_MODEL="f", GEMINI_PRO_MODEL="p",
        OLLAMA_MODEL="lm", OLLAMA_FAST_MODEL="lf", ROUTER_TIERS="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_static_choice_reproduces_legacy_rule():
    from sara.core.llm.engine import _is_simple_query

    corpus = _corpus()
    assert len(corpus) >= 200
    for text in corpus:
        assert is_simple_query(text) == _is_simple_query(text), text
        feat = extract_features(text)
        if not feat.think_cue:
            expected = "cloud_fast" if _is_simple_query(text) else "cloud_main"
            assert static_choice(feat, TIERS_G) == expected, text
            expected_local = "local_fast" if _is_simple_query(text) else "local_main"
            assert static_choice(feat, TIERS_O) == expected_local, text


def test_think_cue_goes_pro_only_when_pro_exists():
    feat = _feat(think_cue=True)
    assert static_choice(feat, TIERS_G) == "cloud_pro"
    assert static_choice(feat, TIERS_G[:2]) == "cloud_main"
    assert static_choice(feat, TIERS_O) == "local_main"


def test_floors():
    assert floor_tier(_feat(), TIERS_G) == "cloud_fast"
    assert floor_tier(_feat(needs_tools=True), TIERS_G) == "cloud_main"
    assert floor_tier(_feat(complexity=HIGH_COMPLEXITY), TIERS_G) == "cloud_main"
    assert floor_tier(_feat(), TIERS_O) == "local_fast"
    assert floor_tier(_feat(lang="hinglish"), TIERS_O) == "local_main"
    assert floor_tier(_feat(lang="hi"), TIERS_O) == "local_main"
    assert floor_tier(_feat(needs_tools=True), TIERS_O) == "local_main"


def test_cascade_next():
    assert cascade_next("cloud_fast", TIERS_G) == "cloud_main"
    assert cascade_next("cloud_main", TIERS_G) == "cloud_pro"
    assert cascade_next("cloud_pro", TIERS_G) is None
    assert cascade_next("nope", TIERS_G) is None
    assert cascade_next("local_fast", TIERS_O) == "local_main"
    assert cascade_next("local_main", TIERS_G + TIERS_O) is None


def test_default_tiers():
    tiers = load_tiers(_cfg())
    assert [t.name for t in tiers] == ["cloud_fast", "cloud_main", "cloud_pro"]
    assert [t.cost for t in tiers] == [1.0, 2.0, 6.0]
    assert [t.name for t in load_tiers(_cfg(GEMINI_PRO_MODEL=""))] == ["cloud_fast", "cloud_main"]
    local = load_tiers(_cfg(LLM_BACKEND="ollama"))
    assert [t.name for t in local] == ["local_fast", "local_main"]
    assert [t.cost for t in local] == [0.0, 0.5]


def test_fewer_than_two_tiers_is_a_noop_list():
    assert len(load_tiers(_cfg(GEMINI_FAST_MODEL="m", GEMINI_PRO_MODEL=""))) == 1
    assert len(load_tiers(_cfg(LLM_BACKEND="ollama", OLLAMA_FAST_MODEL=""))) == 1


def test_json_override_sorts_dedups_and_filters():
    raw = json.dumps([
        {"name": "a", "backend": "gemini", "model": "x", "cost": 3},
        {"name": "b", "backend": "gemini", "model": "y", "cost": 1},
        {"name": "c", "backend": "gemini", "model": "y", "cost": 2},
        {"name": "d", "backend": "gemini", "model": "z", "cost": 4, "enabled": False},
    ])
    assert [t.name for t in load_tiers(_cfg(ROUTER_TIERS=raw))] == ["b", "a"]


@pytest.mark.parametrize("raw", ["{not json", '{"a": 1}', '[{"name": "x"}]'])
def test_malformed_json_falls_back_to_defaults(raw, caplog):
    with caplog.at_level(logging.WARNING, logger="sara.router.tiers"):
        tiers = load_tiers(_cfg(ROUTER_TIERS=raw))
    assert [t.name for t in tiers] == ["cloud_fast", "cloud_main", "cloud_pro"]
    assert "malformed" in caplog.text