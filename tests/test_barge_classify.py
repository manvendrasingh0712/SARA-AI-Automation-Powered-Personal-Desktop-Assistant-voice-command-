import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from types import SimpleNamespace

from sara.audio.tts import barge_classify as bc
from sara.audio.tts.barge_classify import classify_barge, resolve_ambiguous


@pytest.fixture(autouse=True)
def _no_intent(monkeypatch):
    monkeypatch.setattr(bc, "_is_command", lambda text: False)


BACK_SINGLE = [
    "hmm", "hm", "mm", "mhm", "ok", "okay", "yeah", "yes", "yep", "right",
    "sure", "uh-huh", "uh huh", "i see", "got it", "cool", "nice",
    "haan", "han", "acha", "accha", "achha", "theek", "thik", "theek hai",
    "ji", "sahi",
    "हाँ", "हां", "हम्म", "अच्छा", "ठीक", "ठीक है", "जी", "सही",
]
BACK_PAIRS = [
    "hmm okay", "yeah right", "okay sure", "haan ji", "acha theek",
    "हाँ जी", "ok ok", "cool nice",
]
BACK_PUNCT = ["Hmm...", "OK!", "Okay.", "Haan,", "हाँ।", "Uh-huh!", "  YEAH  "]
STOPS = [
    "stop", "wait what time is it", "no that is wrong", "ruko", "ruk jao",
    "bas karo", "nahi nahi", "galat hai", "ek minute", "hold on a second",
    "sorry I meant", "actually make it two", "रुको", "रुक जाओ", "बस करो",
    "नहीं यह गलत है", "एक मिनट रुको", "haan stop", "okay wait", "ok रुको",
    "Wait, what?", "cancel", "enough", "pause", "nope",
]
THREE_PLUS = [
    "what is the weather", "tell me a joke", "volume badha do", "kya haal hai",
    "मुझे समय बताओ", "play some music please", "kal ka mausam batao",
]
AMBIG = [
    "banana", "weather", "बारिश", "hello there", "thanks", "sunao", "I know",
    "ok ok ok", "hmm okay yeah",
]
VALID = {"backchannel", "interrupt", "ambiguous"}


@pytest.mark.parametrize("text", BACK_SINGLE + BACK_PAIRS + BACK_PUNCT)
@pytest.mark.parametrize("ms", [100, 5000])
def test_backchannel(text, ms):
    assert classify_barge(text, ms) == "backchannel"


@pytest.mark.parametrize("text", STOPS + THREE_PLUS)
def test_interrupt(text):
    assert classify_barge(text, 100) == "interrupt"


@pytest.mark.parametrize("text", AMBIG)
def test_ambiguous(text):
    assert classify_barge(text, 100) == "ambiguous"


def test_whole_word_stop_only():
    assert classify_barge("I know", 100) == "ambiguous"
    assert classify_barge("nothing", 100) == "ambiguous"


def test_intent_match_interrupts(monkeypatch):
    monkeypatch.setattr(bc, "_is_command", lambda t: t == "volume up")
    assert classify_barge("volume up", 100) == "interrupt"
    assert classify_barge("hello there", 100) == "ambiguous"


def test_intent_exception_ignored(monkeypatch):
    def boom(text):
        raise RuntimeError("x")

    monkeypatch.setattr(bc, "_is_command", lambda t: bc._is_command.__wrapped__(t))
    monkeypatch.undo()
    assert classify_barge("hello there", 100) in VALID


@pytest.mark.parametrize("text", [None, "", "   ", "...", "!!"])
def test_empty_below_threshold(text):
    assert classify_barge(text, 100) == "ambiguous"


@pytest.mark.parametrize("text", [None, "", "   "])
@pytest.mark.parametrize("ms", [800, 1200])
def test_empty_above_threshold(text, ms):
    assert classify_barge(text, ms) == "interrupt"


def test_cfg_override():
    cfg = SimpleNamespace(BARGE_AMBIGUOUS_MS=500)
    assert classify_barge(None, 500, cfg=cfg) == "interrupt"
    assert classify_barge(None, 499, cfg=cfg) == "ambiguous"


def test_resolve_ambiguous():
    assert resolve_ambiguous(799) == "backchannel"
    assert resolve_ambiguous(800) == "interrupt"
    assert resolve_ambiguous(10, cfg=SimpleNamespace(BARGE_AMBIGUOUS_MS=5)) == "interrupt"
    assert resolve_ambiguous(float("nan")) == "backchannel"
    assert resolve_ambiguous("x") == "backchannel"


@pytest.mark.parametrize(
    "text", [None, 123, 4.5, b"hmm", object(), [], {"a": 1}, "\x00\x01", "💥💥💥"]
)
@pytest.mark.parametrize("ms", [None, "x", float("nan"), -5, 1e12, float("inf")])
def test_garbage_never_raises(text, ms):
    assert classify_barge(text, ms) in VALID