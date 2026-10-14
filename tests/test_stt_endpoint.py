"""Pure-logic tests for adaptive endpointing (no audio, no network)."""
import math
import re
from types import SimpleNamespace

import pytest

from sara.audio.stt import endpoint as ep
from sara.audio.stt.endpoint_cues import CONTINUATION_CUES

CFG = SimpleNamespace(
    ENDPOINT_MIN_MS=350,
    ENDPOINT_MAX_MS=900,
    ENDPOINT_CONTINUATION_EXTRA_MS=400,
    ENDPOINT_NOISE_EXTRA_MS=150,
)
COMPLETE = {"open chrome", "volume badha do", "what time is it", "chrome kholo", "क्रोम खोलो"}


@pytest.fixture(autouse=True)
def _fake_complete(monkeypatch):
    monkeypatch.setattr(ep, "is_complete_command", lambda t: t.strip().lower() in COMPLETE)


def req(text, noise=0.0, lang="en", **kw):
    return ep.required_silence_ms(text, noise, lang, cfg=CFG, **kw)


@pytest.mark.parametrize(
    "text,lang,noise,utt,base,back,want_ms,want_rule",
    [
        ("open chrome", "en", 0.0, 1500, None, 0.0, 350, "complete"),
        ("volume badha do", "hinglish", 0.0, 1500, None, 0.0, 350, "complete"),
        ("what time is it", "en", 0.0, 1500, None, 0.0, 350, "complete"),
        ("क्रोम खोलो", "hi", 0.0, 1500, None, 0.0, 350, "complete"),
        ("chrome kholo", "hinglish", 0.0, 500, None, 0.0, 350, "complete"),
        ("shutdown computer", "en", 0.0, 1500, None, 0.0, 625, "base"),
        ("close chrome", "en", 0.0, 1500, None, 0.0, 625, "base"),
        ("open", "en", 0.0, 1500, None, 0.0, 625, "base"),
        ("set a timer for", "en", 0.0, 1500, None, 0.0, 900, "continuation"),
        (None, "en", 0.0, 1500, None, 0.0, 625, "base"),
        ("", "en", 0.0, 1500, None, 0.0, 625, "base"),
        ("   ", "hi", 0.0, 1500, None, 0.0, 625, "base"),
        ("open chrome and", "en", 0.0, 1500, None, 0.0, 900, "continuation"),
        ("open chrome and", "en", 0.0, 1500, 400, 0.0, 800, "continuation"),
        ("open chrome and", "en", 0.0, 100, 400, 0.0, 800, "continuation"),
        ("um", "en", 0.0, 1500, 400, 0.0, 800, "filler"),
        ("hmm", "en", 0.0, 1500, 400, 0.0, 800, "filler"),
        ("अं", "hi", 0.0, 1500, 400, 0.0, 800, "filler"),
        ("mujhe weather ke liye", "hinglish", 0.0, 1500, 400, 0.0, 800, "continuation"),
        ("मुझे मौसम के लिए", "hi", 0.0, 1500, 400, 0.0, 800, "continuation"),
        ("open chrome", "en", 0.0, 300, None, 0.0, 625, "base"),
        ("open chrome", "en", 0.0, 499.9, None, 0.0, 625, "base"),
        ("open chrome", "en", 0.0, 0.0, None, 0.0, 625, "base"),
        ("open chrome", "en", 0.0, 500, None, 0.0, 350, "complete"),
        ("open chrome", "en", 0.0, 1500, 800, 0.0, 350, "complete"),
        (None, "en", 0.0, 1500, 800, 0.0, 800, "base"),
        (None, "en", 0.0, 1500, 100, 0.0, 350, "base"),
        (None, "en", 0.0, 1500, 5000, 0.0, 900, "base"),
        ("open chrome", "en", 0.0, 1500, None, 100.0, 450, "complete"),
        (None, "en", 0.0, 1500, None, 100.0, 725, "base"),
        (None, "en", 0.0, 1500, None, 300.0, 900, "base"),
        ("open chrome", "en", 0.8, 1500, None, 0.0, 745, "noise"),
        (None, "en", 0.8, 1500, None, 0.0, 745, "noise"),
        (None, "en", 1.0, 1500, None, 0.0, 775, "noise"),
        (None, "en", 0.5, 1500, None, 0.0, 625, "base"),
        (None, "en", 0.4, 1500, None, 0.0, 625, "base"),
        ("open chrome and", "en", 0.9, 1500, None, 0.0, 900, "noise"),
        (None, "en", 7.0, 1500, None, 0.0, 775, "noise"),
        (None, "en", -3.0, 1500, None, 0.0, 625, "base"),
    ],
)
def test_required_silence_table(text, lang, noise, utt, base, back, want_ms, want_rule):
    ms, rule = req(text, noise, lang, utterance_ms=utt, base_ms=base, backoff_ms=back)
    assert ms == pytest.approx(want_ms)
    assert rule == want_rule


CUE_CASES = [
    (lang, " ".join(cue))
    for lang, cues in CONTINUATION_CUES.items()
    for cue in cues
    if " ".join(cue) not in ("matlab", "मतलब")
]
PREFIX = {"en": "please call mom", "hinglish": "mujhe weather", "hi": "मुझे मौसम"}


@pytest.mark.parametrize("lang,cue", CUE_CASES)
def test_every_cue_lengthens(lang, cue):
    ms, rule = req(f"{PREFIX[lang]} {cue}", lang=lang, utterance_ms=1500, base_ms=350)
    assert rule == "continuation"
    assert ms == pytest.approx(750)


@pytest.mark.parametrize("lang,cue", [("hinglish", "matlab"), ("hi", "मतलब")])
def test_matlab_after_words_is_continuation(lang, cue):
    assert req(f"{PREFIX[lang]} {cue}", lang=lang, utterance_ms=1500)[1] == "continuation"


@pytest.mark.parametrize(
    "text,lang,want",
    [
        ("open chrome and", "en", True),
        ("open chrome AND!", "en", True),
        ("call mom for", "en", True),
        ("open chrome", "en", False),
        ("", "en", False),
        (None, "en", False),
        ("mujhe weather ke liye", "hinglish", True),
        ("weather batao", "hinglish", False),
        ("मुझे मौसम के लिए", "hi", True),
        ("मौसम बताओ", "hi", False),
        ("open chrome and", "xx", True),
        ("open chrome and", None, True),
        ("aur", "en", False),
        ("ke liye", "en", False),
    ],
)
def test_ends_with_continuation(text, lang, want):
    assert ep.ends_with_continuation(text, lang) is want


@pytest.mark.parametrize(
    "text,want",
    [
        ("um", True), ("Umm.", True), ("hmm hmm", True), ("uh um", True), ("matlab", True),
        ("er", True), ("mm", True), ("अं", True), ("उम", True), ("हम्म", True),
        ("um um um", False), ("um open", False), ("hello", False), ("", False), (None, False),
    ],
)
def test_is_lone_filler(text, want):
    assert ep.is_lone_filler(text) is want


@pytest.mark.parametrize(
    "floor,thr,want",
    [(None, 100, 0.0), (50, 100, 0.5), (200, 100, 1.0), (-5, 100, 0.0), (50, 0, 0.0),
     (50, -1, 0.0), ("x", 100, 0.0), (float("nan"), 100, 0.0), (10, float("inf"), 0.0)],
)
def test_noise_level_from(floor, thr, want):
    assert ep.noise_level_from(floor, thr) == pytest.approx(want)


@pytest.mark.parametrize(
    "text,silence,noise,back,want",
    [
        ("open chrome", 349, 0.0, 0, False),
        ("open chrome", 350, 0.0, 0, True),
        ("open chrome", 349.9, 0.0, 0, False),
        ("open chrome", 400, 0.0, 100, False),
        ("open chrome", 450, 0.0, 100, True),
        ("open chrome and", 899, 0.0, 0, False),
        ("open chrome and", 900, 0.0, 0, True),
        ("shutdown computer", 624, 0.0, 0, False),
        ("shutdown computer", 625, 0.0, 0, True),
        (None, 624, 0.0, 0, False),
        (None, 625, 0.0, 0, True),
        (None, 100, 0.0, 0, False),
        ("open chrome", 0, 0.0, 0, False),
        ("open chrome", 900, 1.0, 300, True),
        ("open chrome and", 5000, 1.0, 300, True),
        ("open chrome", 700, 0.8, 0, False),
        ("open chrome", 745, 0.8, 0, True),
        ("क्रोम खोलो", 350, 0.0, 0, True),
        ("volume badha do", 350, 0.0, 0, True),
    ],
)
def test_decide_table(text, silence, noise, back, want):
    got = ep.decide_endpoint(
        text, silence, noise, "en", utterance_ms=1500, backoff_ms=back, cfg=CFG
    )
    assert got is want


@pytest.mark.parametrize("text", [None, "open chrome", "open chrome and", "um", "shutdown computer"])
@pytest.mark.parametrize("noise,back", [(0.0, 0.0), (1.0, 300.0)])
def test_bounds_and_monotonic(text, noise, back):
    seq = [
        ep.decide_endpoint(text, s, noise, "en", utterance_ms=2000, backoff_ms=back, cfg=CFG)
        for s in range(0, 1600, 10)
    ]
    assert not any(seq[: 35])
    assert all(seq[90:])
    first = seq.index(True)
    assert all(seq[first:])


def test_cfg_clamps():
    low = SimpleNamespace(ENDPOINT_MIN_MS=50, ENDPOINT_MAX_MS=100)
    assert ep.required_silence_ms(None, 0.0, "en", cfg=low) == (300.0, "base")
    high = SimpleNamespace(ENDPOINT_MIN_MS=800, ENDPOINT_MAX_MS=400)
    assert ep.required_silence_ms(None, 0.0, "en", cfg=high) == (850.0, "base")
    wide = SimpleNamespace(ENDPOINT_MIN_MS=350, ENDPOINT_MAX_MS=5000)
    assert ep.required_silence_ms(None, 0.0, "en", cfg=wide)[0] == pytest.approx(1425)
    assert ep.required_silence_ms(None, 0.0, "en", cfg=object()) == (625.0, "base")


GARBAGE = [
    None,
    "",
    " " * 10,
    pytest.param("x" * 10**6, id="huge"),
    "😀😀",
    "open क्रोम 😀 and",
    123,
    ["a"],
    b"bytes",
    "\x00\x01",
]


@pytest.mark.parametrize("junk", GARBAGE)
def test_garbage_never_raises(junk):
    ms, rule = ep.required_silence_ms(junk, junk, junk, utterance_ms=junk, base_ms=junk,
                                      backoff_ms=junk, cfg=CFG)
    assert 350 <= ms <= 900 and isinstance(rule, str)
    assert ep.decide_endpoint(junk, junk, junk, junk, cfg=CFG) in (True, False)
    assert ep.ends_with_continuation(junk, junk) in (True, False)
    assert ep.is_lone_filler(junk) in (True, False)
    assert ep.noise_level_from(junk, junk) == 0.0 or math.isfinite(ep.noise_level_from(junk, junk))


def test_determinism():
    runs = {req("open chrome and", 0.7, "en", utterance_ms=900, backoff_ms=50) for _ in range(20)}
    assert len(runs) == 1


FAKE_RX = {
    "open_app": re.compile(r"^open (.*)$"),
    "volume_up": re.compile(r"^volume badha do$"),
    "get_time": re.compile(r"^what time is it$"),
    "shutdown": re.compile(r"^shutdown computer$"),
    "close_app": re.compile(r"^close (.*)$"),
    "play_music": re.compile(r"^play music( \w+)?$"),
    "hindi_open": re.compile(r"^क्रोम$"),
}
FAKE_TIERS = {"open_app": 0, "volume_up": 1, "get_time": 0, "shutdown": 3, "close_app": 2,
              "play_music": 1, "hindi_open": 1}


@pytest.fixture
def fake_intents(monkeypatch):
    monkeypatch.undo()
    from sara.core.intent import engine as ie
    from sara.core.security import tiers

    def detect(text):
        for name, rx in FAKE_RX.items():
            m = rx.match(text.strip().lower())
            if m:
                return name, m
        return "chat", None

    monkeypatch.setattr(ie, "detect_intent", detect)
    monkeypatch.setattr(tiers, "tier_of", lambda i: FAKE_TIERS.get(i, 2))


@pytest.mark.parametrize(
    "text,want",
    [
        ("open chrome", True), ("volume badha do", True), ("what time is it", True),
        ("shutdown computer", False), ("close chrome", False), ("open", False),
        ("क्रोम", False), ("play music", False), ("play music loud", True),
        ("open chrome and", False), ("um um", False), ("", False), (None, False),
        ("random words here", False), ("x " * 500, False),
    ],
)
def test_is_complete_command(fake_intents, text, want):
    assert ep.is_complete_command(text) is want


def test_is_complete_command_swallows_errors(fake_intents, monkeypatch):
    from sara.core.intent import engine as ie

    monkeypatch.setattr(ie, "detect_intent", lambda t: 1 / 0)
    assert ep.is_complete_command("open chrome") is False