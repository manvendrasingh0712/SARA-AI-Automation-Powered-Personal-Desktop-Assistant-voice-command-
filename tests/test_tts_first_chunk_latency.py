"""
Regression tests for D31 (audit batch D22-c): the streaming first-chunk path
is on by default, and it is on for reasons that stay measurable.

WHAT WAS INVESTIGATED
    Telemetry showed tts_start_ms p50 = 1250 ms (n=5 -- preliminary). That
    metric is `tts_first_audio - first_of(llm_first_token, tool_end,
    route_done)`, so on a streamed reply it is NOT synthesis time alone: it
    includes waiting for enough streamed text to form the first chunk. That
    wait is what this batch addresses.

    Config.TTS_FIRST_CHUNK_MIN_CHARS (12) was the obvious knob and is
    deliberately NOT changed: with the v2 path on, engine._chunker_worker's
    v2 branch either `continue`s or sets first_pushed before the
    _FIRST_TRIGGER check is reached, so that value is bypassed for the first
    chunk entirely. Tuning it would have changed nothing.
    test_first_trigger_is_bypassed_under_v2 pins that.

WHAT WAS MEASURED, AND WHAT IT DOES AND DOES NOT SHOW
    Each reply below was streamed word by word through the real decision
    code, counting characters consumed before the first chunk reached the
    synthesis queue:

                     legacy    v2
      mean chars      51.0    46.5     <- marginal; NOT the justification
      no early audio  4/11     0/11    <- the justification
      bad first chunk 3        0       <- the other justification

    The mean difference is small enough to be noise. The change is
    justified by the tail and by correctness:

    * Legacy has no word-boundary fallback. Its first-chunk rule needs a
      strong terminator, or a soft one only after 35 characters, so a reply
      with no mid-text punctuation pushes nothing and falls through to
      _split_adaptive -- which also pushes nothing until it finds more than
      one segment. The user hears silence until the entire reply has
      streamed.
    * Legacy's early flushes were sometimes defective, which is precisely
      why it looked faster on the mean: it emitted 'Dr.' as a whole first
      utterance, cut 'The total comes to 1,234.' inside the number, and cut
      'You can read it at https://example.' inside the URL.

    THESE ARE CHARACTER COUNTS, NOT MILLISECONDS. No audio was synthesised
    and no latency figure is claimed. Characters map to time only through
    the model's token rate, so the real saving has to be read from
    telemetry's tts_start_ms on the Windows machine
    (`python -m bench.voice_latency`).

THE ONE KNOWN REGRESSION
    Hinglish "Haan bilkul, ..." flushes at 60 characters instead of 38:
    legacy's short-but-valid clause flush is deferred to the word boundary
    because chunker.CLAUSE_MIN_CHARS is 25 and "Haan bilkul," is 12.
    That knob is deliberately untouched -- a 12-character first utterance
    followed by a synthesis gap may sound worse than waiting, and that is a
    judgement for the ear on real hardware. It is recorded here rather than
    hidden.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from config import Config
from sara.audio.tts.chunker import first_chunk_end
from sara.audio.tts.engine import (
    TextToSpeech,
    _FIRST_TRIGGER,
    _MIN_CHUNK,
)
from sara.audio.tts.text_prep import _split_adaptive
from sara.audio.voice_flags import VOICE_FLAGS, get_flag, reset_flags

_ENGINE_SRC = Path(inspect.getfile(TextToSpeech)).read_text(encoding="utf-8")


class _Stub:
    """_first_flush_match only reads module constants, never instance state."""


def _legacy_first_push(text: str):
    """engine._chunker_worker's first-push logic with v2 off."""
    buf = ""
    for word in text.split():
        buf += word + " "
        if len(buf.strip()) >= _FIRST_TRIGGER:
            match = TextToSpeech._first_flush_match(_Stub(), buf)
            if match:
                part = buf[: match.end()].strip()
                if part and len(part) >= _MIN_CHUNK:
                    return len(buf.strip()), part
        segs = _split_adaptive(buf)
        if len(segs) > 1:
            for seg in segs[:-1]:
                seg = seg.strip()
                if seg:
                    return len(buf.strip()), seg
    return len(text), None  # nothing pushed early: the whole reply waits


def _v2_first_push(text: str):
    """engine._chunker_worker's first-push logic with v2 on."""
    buf = ""
    for word in text.split():
        buf += word + " "
        cut = first_chunk_end(buf, False)
        if cut is None:
            continue
        part = buf[:cut].strip()
        if part:
            return len(buf.strip()), part
    return len(text), None


# Replies with no mid-text punctuation: legacy pushes nothing at all for
# these, so the user waits for the whole stream before any audio.
NO_EARLY_AUDIO_UNDER_LEGACY = [
    "I am going to open your project folder and then look through the recent "
    "changes you made yesterday afternoon before the build broke",
    "I have opened the file and the tests are running now so give me a moment "
    "to report back",
]

# Replies where legacy's early flush is defective. (text, bad_first_chunk)
DEFECTIVE_UNDER_LEGACY = [
    (
        "The total comes to 1,234.56 rupees for the month of March and that is "
        "before tax.",
        "1,234.",
    ),
    (
        "You can read it at https://example.com/docs/page and I will summarise "
        "the important parts.",
        "https://example.",
    ),
    (
        "Dr. Sharma called earlier about the appointment and asked you to "
        "confirm the time.",
        "Dr.",
    ),
]


def test_v2_is_the_active_default() -> None:
    assert Config.VOICE_CHUNKER_V2 is True, (
        "VOICE_CHUNKER_V2 is off; the legacy first-chunk path produces no "
        "early audio for replies without mid-text punctuation (D31)"
    )
    assert get_flag("VOICE_CHUNKER_V2") is True


def test_registry_and_config_defaults_agree() -> None:
    """
    get_flag() reads Config; reset_flags() writes the REGISTRY default back.
    If the two disagree, "reset to defaults" silently changes behaviour.
    """
    registry = {f.key: f.default for f in VOICE_FLAGS}["VOICE_CHUNKER_V2"]
    assert registry is True, "registry default disagrees with Config's"
    reset_flags()
    assert get_flag("VOICE_CHUNKER_V2") is True


@pytest.mark.parametrize("text", NO_EARLY_AUDIO_UNDER_LEGACY)
def test_v2_produces_early_audio_where_legacy_produces_none(text: str) -> None:
    """The actual justification for the change."""
    legacy_chars, legacy_part = _legacy_first_push(text)
    v2_chars, v2_part = _v2_first_push(text)

    assert legacy_part is None, (
        "this sample no longer demonstrates the legacy gap; pick a reply with "
        "no mid-text punctuation, or the test has stopped testing anything"
    )
    assert v2_part, f"v2 produced no first chunk for {text[:40]!r}"
    assert v2_chars < legacy_chars, (
        f"v2 consumed {v2_chars} chars before first audio, legacy "
        f"{legacy_chars} -- the bound on the worst case is gone"
    )


@pytest.mark.parametrize("text,bad_chunk", DEFECTIVE_UNDER_LEGACY)
def test_v2_refuses_the_unsafe_early_cuts_legacy_made(text: str, bad_chunk: str) -> None:
    """
    Legacy's apparent speed advantage came from cutting in places it should
    not. v2 must not reproduce any of them.
    """
    _, legacy_part = _legacy_first_push(text)
    _, v2_part = _v2_first_push(text)

    assert legacy_part is not None and legacy_part.endswith(bad_chunk), (
        f"this sample no longer reproduces the legacy defect "
        f"(got {legacy_part!r}); the test has stopped testing anything"
    )
    assert v2_part, "v2 produced no first chunk"
    assert not v2_part.endswith(bad_chunk), (
        f"v2 reproduced the legacy defect: first chunk {v2_part!r} ends in "
        f"{bad_chunk!r}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "The total comes to 1,234.56 rupees.",
        "You can read it at https://example.com/docs/page now.",
        "Dr. Sharma called earlier.",
        "Mail me at someone@example.com please.",
        "ठीक है, मैं अभी आपका प्रोजेक्ट फ़ोल्डर खोल रही हूँ और फिर बदलाव देखती हूँ।",
    ],
)
def test_v2_first_chunk_never_ends_mid_token(text: str) -> None:
    """
    Intelligibility guard. A first chunk ending inside a number, a URL, an
    e-mail address or a Devanagari conjunct is spoken as nonsense, which is
    worse than waiting for the next boundary.
    """
    _, part = _v2_first_push(text)
    if part is None:
        return  # nothing flushed early is acceptable; a bad flush is not
    assert not re.search(r"\d[.,]$", part), f"cut inside a number: {part!r}"
    assert not re.search(r"https?://\S*\.$", part), f"cut inside a URL: {part!r}"
    assert not re.search(r"\S+@\S*\.$", part), f"cut inside an e-mail: {part!r}"
    assert not part.endswith("्"), f"cut after a virama: {part!r}"
    bare = part.rstrip(".?!।॥…\"'”’)]}").strip()
    assert len(bare) >= 2, (
        f"first chunk is effectively empty speech: {part!r} -- legacy's 'Dr.' "
        f"failure mode"
    )


def test_first_trigger_is_bypassed_under_v2() -> None:
    """
    Why TTS_FIRST_CHUNK_MIN_CHARS was investigated and left alone: the v2
    branch returns or sets first_pushed before the _FIRST_TRIGGER check, so
    that value cannot affect the first chunk while v2 is on. If this
    ordering ever changes, the config comment becomes wrong.
    """
    worker = _ENGINE_SRC[_ENGINE_SRC.index("def _chunker_worker") :]
    worker = worker[: worker.index("\n    def ", 10)]
    v2_at = worker.index("if use_v2 and not first_pushed:")
    trigger_at = worker.index("_FIRST_TRIGGER")
    assert v2_at < trigger_at, (
        "the v2 branch no longer precedes the _FIRST_TRIGGER check; "
        "TTS_FIRST_CHUNK_MIN_CHARS may now affect the first chunk and the "
        "note on Config.VOICE_CHUNKER_V2 needs correcting"
    )
    assert "first_pushed = True" in worker[v2_at:trigger_at], (
        "the v2 branch no longer marks first_pushed, so the legacy trigger "
        "can fire a second first-chunk"
    )


def test_v2_branch_preserves_cancellation_and_fallback() -> None:
    """
    Enabling a path must not cost stop/barge-in responsiveness or error
    handling. The v2 branch must still honour self._stop, still route its
    queue put through _stop_aware_put, and still fall back to the legacy
    splitter if first_chunk_end() raises.
    """
    worker = _ENGINE_SRC[_ENGINE_SRC.index("def _chunker_worker") :]
    worker = worker[: worker.index("\n    def ", 10)]
    v2_block = worker[worker.index("if use_v2 and not first_pushed:") :]
    v2_block = v2_block[: v2_block.index("if not first_pushed and len(buf.strip())")]

    assert "self._stop.is_set()" in v2_block, "v2 branch stopped checking self._stop"
    assert "_stop_aware_put" in v2_block, (
        "v2 branch no longer uses _stop_aware_put, so a full synthesis queue "
        "can block a stop() indefinitely"
    )
    assert "except Exception" in v2_block and "use_v2 = False" in v2_block, (
        "v2 branch lost its fallback; a raise in first_chunk_end() would now "
        "kill the chunker instead of reverting to the legacy splitter"
    )


def test_short_replies_are_not_chopped() -> None:
    """A reply that is already one short sentence must not be split at all."""
    for text in ("Done.", "Okay.", "Opening Chrome now."):
        chars, part = _v2_first_push(text)
        assert part is None or part.strip() == text.strip(), (
            f"{text!r} was split into {part!r}; short replies must be spoken "
            f"in one piece"
        )
