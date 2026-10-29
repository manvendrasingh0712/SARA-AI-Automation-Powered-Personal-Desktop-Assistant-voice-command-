"""
Regression tests for D30 (audit batch D22-b): the recognisers are warmed at
startup so the first spoken command does not pay first-run cost.

THE DEFECT
----------
Loading a Whisper model and running one are different costs. `__init__`
already paid the load (three models, in parallel). The first actual
transcription additionally pays CTranslate2's kernel/graph bring-up, and
that was charged to whatever the user said FIRST.

`sara/audio/tts/prewarm.py` pre-synthesises TTS phrases, but there was no
STT equivalent anywhere -- a grep for "warm" in sara/audio/stt/engine.py
returned nothing -- so nothing absorbed that cost.

WHAT IS AND IS NOT MEASURED HERE
--------------------------------
These are contract tests. faster-whisper cannot be installed in the CI/dev
container used for this audit, so the actual saving is NOT measured and is
NOT claimed anywhere. telemetry's `stt_ms` on the first turn of a session
is the instrument for that, and it has to be read on the Windows machine.
What is asserted here is that the warm-up is wired correctly, respects its
config flag, cannot be silently a no-op, cannot break boot, and runs at the
point that makes it safe.

WHY PLACEMENT IS THE SAFETY ARGUMENT
------------------------------------
The warm-up runs inside `__init__`, after the models are assigned and
before `_open_mic_stream()` / `_start_threads()`. At that moment no caller
holds a reference to the instance, so no `listen()` or preview transcribe
can be in flight and the model objects cannot be used concurrently -- which
is why no lock is needed and why a real command can never be delayed by it.
A background thread would have raced the orchestrator's first `listen()` on
the same model object, and faster-whisper does not promise that is safe.
test_warmup_runs_before_mic_and_threads pins that ordering.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from config import Config
import sara.audio.stt.engine as stt_engine

_SRC = Path(inspect.getfile(stt_engine)).read_text(encoding="utf-8")


class _FakeModel:
    """
    Stands in for a faster-whisper model. `transcribe()` returns a GENERATOR,
    exactly as the real one does, so a warm-up that forgets to drain it would
    record no inference here either -- see
    test_warmup_drains_the_segment_generator.
    """

    def __init__(self, name: str, raises: bool = False) -> None:
        self.name = name
        self.raises = raises
        self.calls: list[dict] = []
        self.drained = 0

    def transcribe(self, audio, **kwargs):
        if self.raises:
            raise RuntimeError("simulated recogniser failure")
        self.calls.append({"n_samples": len(audio), "kwargs": kwargs})

        def _gen():
            self.drained += 1
            return
            yield  # pragma: no cover -- generator with no items

        return _gen(), None


class _Recorder:
    """Minimal stand-in exposing only what _warm_recognisers touches."""

    SAMPLE_RATE = 16000

    def __init__(self, main=None, wake=None) -> None:
        self._whisper_model = main
        self._wake_whisper_model = wake

    @property
    def models(self) -> list:
        return [m for m in (self._whisper_model, self._wake_whisper_model) if m]

    @property
    def calls(self) -> list:
        """(model, n_samples) for every warm-up inference attempted."""
        return [(m, c["n_samples"]) for m in self.models for c in m.calls]

    _warm_recognisers = stt_engine.SpeechToText._warm_recognisers


def test_warmup_method_exists() -> None:
    assert hasattr(stt_engine.SpeechToText, "_warm_recognisers"), (
        "the STT warm-up was removed; the first spoken command of every "
        "session goes back to paying recogniser first-run cost (D30)"
    )


def test_warmup_warms_both_models() -> None:
    """
    Both are on the first command's critical path: the main model runs the
    command, the tiny one serves the live-caption preview and the STT
    wake-word fallback.
    """
    main, wake = _FakeModel("main"), _FakeModel("wake")
    rec = _Recorder(main=main, wake=wake)
    rec._warm_recognisers()
    warmed = [m for m, _ in rec.calls]
    assert main in warmed, "the main recogniser was not warmed"
    assert wake in warmed, "the tiny wake/preview recogniser was not warmed"


def test_warmup_clip_is_long_enough_to_be_useful() -> None:
    """
    A clip too short to contain a frame warms nothing while the feature
    still looks enabled. 0.25s is the floor.
    """
    rec = _Recorder(main=_FakeModel("main"))
    rec._warm_recognisers()
    assert rec.calls, "no warm-up inference was attempted"
    for _, n_samples in rec.calls:
        seconds = n_samples / _Recorder.SAMPLE_RATE  # float32 samples
        assert seconds >= 0.20, f"warm-up clip is only {seconds:.3f}s"


def test_warmup_respects_the_disable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "STT_WARMUP_ENABLED", False, raising=False)
    rec = _Recorder(main=_FakeModel("main"), wake=_FakeModel("wake"))
    rec._warm_recognisers()
    assert rec.calls == [], "warm-up ran despite STT_WARMUP_ENABLED=False"


def test_warmup_floor_survives_a_too_small_config_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured 0.05s must still produce a clip _transcribe will accept."""
    monkeypatch.setattr(Config, "STT_WARMUP_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "STT_WARMUP_SECONDS", 0.05, raising=False)
    rec = _Recorder(main=_FakeModel("main"))
    rec._warm_recognisers()
    assert rec.calls
    seconds = rec.calls[0][1] / _Recorder.SAMPLE_RATE
    assert seconds >= 0.20, f"floor not applied: {seconds:.3f}s"


def test_warmup_never_raises_into_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A warm-up is an optimisation. If it throws, SARA must still start --
    otherwise a broken optimisation becomes a boot failure.
    """
    monkeypatch.setattr(Config, "STT_WARMUP_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "STT_WARMUP_SECONDS", 0.25, raising=False)
    bad, good = _FakeModel("bad", raises=True), _FakeModel("good")
    rec = _Recorder(main=bad, wake=good)
    rec._warm_recognisers()  # must not propagate
    assert good.calls, (
        "a failure warming the first model stopped the second from being "
        "warmed; each step must be independently guarded"
    )
    assert good.drained == 1, "the surviving model's generator was not drained"


def test_warmup_tolerates_absent_models() -> None:
    """Optional model loads can fail; warm-up must skip, not crash."""
    rec = _Recorder(main=None, wake=None)
    rec._warm_recognisers()
    assert rec.calls == []


def test_warmup_runs_before_mic_and_threads() -> None:
    """
    The safety property: warming happens while no caller can hold a
    reference to the instance, so the models cannot be used concurrently.
    If this ever moves after _open_mic_stream()/_start_threads(), the
    warm-up can race the orchestrator's first listen() on the same model.
    """
    warm = _SRC.index("self._warm_recognisers()")
    mic = _SRC.index("self._open_mic_stream()")
    threads = _SRC.index("self._start_threads()")
    assert warm < mic, "_warm_recognisers() must run before _open_mic_stream()"
    assert warm < threads, "_warm_recognisers() must run before _start_threads()"


def test_warmup_flags_are_real_config_fields() -> None:
    """
    Both flags are declared on Config rather than only read via getattr
    with a default -- the audit recorded that pattern as "looked
    configurable via .env but silently was not".
    """
    assert isinstance(getattr(Config, "STT_WARMUP_ENABLED", None), bool)
    assert isinstance(getattr(Config, "STT_WARMUP_SECONDS", None), float)
    cfg = Path(inspect.getfile(Config)).read_text(encoding="utf-8")
    for name in ("STT_WARMUP_ENABLED", "STT_WARMUP_SECONDS"):
        assert re.search(rf"^\s+{name}\s*:", cfg, re.MULTILINE), (
            f"{name} is not declared as a Config field"
        )


# ── D33: the warm-up must be isolated from the live command path ────────
def test_warmup_does_not_go_through_the_live_transcribe_path() -> None:
    """
    THE DEFECT D33 FIXED. The warm-up originally called self._transcribe(),
    which is the live command path and mutates instance state on any
    non-empty result:

        self._recent_transcript = (self._recent_transcript + " " + text)
        self._update_detected_language(text)

    Whisper hallucinates on pure silence, and _transcribe()'s hallucination
    filters catch many such phrases but not all. Anything slipping through
    would have set self._detected_lang -- read by core_wiring to choose the
    reply language -- from garbage, before the user had said a word.

    Warming must not be able to influence a later turn, so it must not
    share a code path with one.
    """
    src = inspect.getsource(stt_engine.SpeechToText._warm_recognisers)
    # Strip comments and the docstring: both legitimately MENTION
    # self._transcribe() to explain why it is not used, and matching prose
    # would make this test fail on its own documentation.
    code_only = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    body = code_only.split('"""')[-1]  # everything after the docstring
    assert "self._transcribe(" not in body, (
        "the warm-up is calling self._transcribe() again; that path mutates "
        "_recent_transcript and _detected_lang (D33)"
    )
    assert "model.transcribe(" in body, "the warm-up no longer invokes the model"


def test_warmup_cannot_touch_language_or_transcript_state() -> None:
    """
    The behavioural half of the test above: run a warm-up against a model
    that returns a hallucinated segment and prove no state moved.
    """

    class _Seg:
        text = "Thank you."
        no_speech_prob = 0.0

    class _Hallucinating(_FakeModel):
        def transcribe(self, audio, **kwargs):
            self.calls.append({"n_samples": len(audio), "kwargs": kwargs})

            def _gen():
                self.drained += 1
                yield _Seg()

            return _gen(), None

    rec = _Recorder(main=_Hallucinating("main"))
    rec._recent_transcript = ""
    rec._detected_lang = "english"
    rec._warm_recognisers()
    assert rec._recent_transcript == "", (
        "warm-up polluted _recent_transcript with a silence hallucination"
    )
    assert rec._detected_lang == "english", (
        "warm-up changed _detected_lang from a silence hallucination; that "
        "value selects the reply language for the user's first real turn"
    )


def test_warmup_drains_the_segment_generator() -> None:
    """
    faster-whisper's transcribe() is LAZY -- it returns a generator and does
    the work on iteration. A warm-up that does not drain it computes nothing
    while still looking like it succeeded, which is the worst kind of
    no-op: the cost disappears and so does the benefit.
    """
    model = _FakeModel("main")
    rec = _Recorder(main=model)
    rec._warm_recognisers()
    assert model.calls, "transcribe() was never called"
    assert model.drained == 1, (
        "the segment generator was not drained, so no inference actually "
        "ran and the warm-up warms nothing"
    )


def test_warmup_uses_cheap_settings() -> None:
    """
    The only goal is making CTranslate2 build its kernels; the discarded
    result's accuracy is irrelevant. Beam search and the temperature
    fallback made this several times slower than needed -- paid
    synchronously during boot, for both models.
    """
    model = _FakeModel("main")
    _Recorder(main=model)._warm_recognisers()
    kwargs = model.calls[0]["kwargs"]
    assert kwargs.get("beam_size") == 1, "warm-up is using beam search again"
    assert kwargs.get("best_of") == 1
    assert kwargs.get("temperature") == 0.0, (
        "warm-up is using the temperature fallback again, which runs "
        "inference several times over"
    )
    assert kwargs.get("vad_filter") is False
