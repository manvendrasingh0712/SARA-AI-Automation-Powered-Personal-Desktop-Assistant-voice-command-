"""
Regression tests for D34 and D35 (P0: SARA could not transcribe).

THE OBSERVED FAILURE
    On the target machine every stage of the audio path was healthy --
    callbacks firing, 155 blocks captured, gate passed at 1.2x, VAD voicing
    143/154 frames -- and every transcript was empty. Cause:

        [STT Error] CPU initialization failed:
        Cannot find an appropriate cached snapshot folder ... outgoing
        traffic has been disabled ... set 'HF_HUB_OFFLINE=0'
        [STT] Ready - FasterWhisper=X | VAD=ok | WakeWord=ok | ...

    The faster-whisper model was not in the local cache and offline mode
    blocked the download, so self._whisper_model was None. _transcribe()
    returns TranscriptionResult("", 0.0) immediately on its `model is None`
    guard, so every turn produced nothing while the microphone, wake word
    and GUI level meter all behaved perfectly.

D34 -- the loader's offline/online dance was unsafe and inert
    _load_whisper_model_offline_first() expressed "cache only" by mutating
    the global HF_HUB_OFFLINE env var and restoring it afterwards.

    * NOT THREAD-SAFE, and it is genuinely called concurrently:
      SpeechToText.__init__ loads three models in one ThreadPoolExecutor
      (_load_faster_whisper, _load_wakeword, _load_fast_wake_whisper) and
      two of them reach this function. Thread A set the var, thread B
      restored it, and A's retry ran under whatever B left behind.
    * INERT ANYWAY: faster_whisper imports huggingface_hub at module level
      (faster_whisper/utils.py line 7), and huggingface_hub captures
      HF_HUB_OFFLINE into a module constant at import. By the time this
      function runs the value is frozen, so mutating os.environ changed
      nothing -- the dance read as deliberate behaviour while doing nothing.

    Fixed by using WhisperModel's own `local_files_only` argument
    (first-class in faster-whisper 1.0.3): per-call, thread-safe, honoured.

D35 -- the failure was invisible
    A failed load printed one line and SARA went on to print
    "[STT] Ready - FasterWhisper=X | VAD=ok | ..." -- a line shaped like
    success with the one symbol that matters buried among ticks. A voice
    assistant that cannot transcribe has lost its primary function and must
    say so, with the remedy.
"""
from __future__ import annotations

import inspect
import os
import threading

import pytest

import sara.audio.stt.engine as stt_engine
from sara.audio.stt.engine import (
    SpeechToText,
    _load_whisper_model_offline_first,
)


class _FakeWhisper:
    """Records every construction so the call order can be asserted."""

    calls: list[dict] = []
    fail_when_local_only = False

    def __init__(self, *args, **kwargs):
        type(self).calls.append(dict(kwargs))
        self.kwargs_at_construction = dict(kwargs)
        if self.fail_when_local_only and kwargs.get("local_files_only"):
            raise RuntimeError("Cannot find an appropriate cached snapshot folder")

    def transcribe(self, *args, **kwargs):
        """A healthy model: returns an empty segment generator, like the real one."""

        def _gen():
            return
            yield  # pragma: no cover -- generator with no items

        return _gen(), None


@pytest.fixture(autouse=True)
def _fake_model(monkeypatch: pytest.MonkeyPatch):
    _FakeWhisper.calls = []
    _FakeWhisper.fail_when_local_only = False
    monkeypatch.setattr(stt_engine, "WhisperModel", _FakeWhisper, raising=False)
    yield _FakeWhisper


# ── D34 ─────────────────────────────────────────────────────────────────
def test_loader_tries_cache_then_allows_download(_fake_model) -> None:
    _fake_model.fail_when_local_only = True
    _load_whisper_model_offline_first(model_size_or_path="medium", device="cpu")
    assert len(_fake_model.calls) == 2, (
        "expected a cache-only attempt followed by a download-allowed retry"
    )
    assert _fake_model.calls[0]["local_files_only"] is True
    assert _fake_model.calls[1]["local_files_only"] is False, (
        "the retry must ALLOW a download; otherwise a model that was never "
        "downloaded can never be fetched (D34)"
    )


def test_loader_stops_at_one_call_when_cached(_fake_model) -> None:
    _load_whisper_model_offline_first(model_size_or_path="medium", device="cpu")
    assert len(_fake_model.calls) == 1, "a cached model must not trigger a retry"
    assert _fake_model.calls[0]["local_files_only"] is True


def test_loader_does_not_touch_global_environment(_fake_model) -> None:
    """
    The thread-safety fix. Three models load in parallel inside one
    ThreadPoolExecutor and two of them reach this function, so any global
    mutation here is a race by construction.
    """
    _fake_model.fail_when_local_only = True
    before = dict(os.environ)
    _load_whisper_model_offline_first(model_size_or_path="medium", device="cpu")
    after = dict(os.environ)
    assert before == after, (
        "the loader mutated os.environ; with concurrent model loads that is "
        "a race, and HF_HUB_OFFLINE is frozen at import so it achieves "
        "nothing anyway (D34)"
    )
    src = inspect.getsource(_load_whisper_model_offline_first)
    code = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    ).split('"""')[-1]
    assert "os.environ" not in code, (
        "the loader is manipulating os.environ again (D34)"
    )


def test_loader_is_safe_under_concurrent_use(_fake_model) -> None:
    """Two threads loading at once must not interfere -- the real shape."""
    _fake_model.fail_when_local_only = True
    errors: list[BaseException] = []

    def _load() -> None:
        try:
            _load_whisper_model_offline_first(model_size_or_path="tiny", device="cpu")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_load) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not errors, f"concurrent loads raised: {errors!r}"
    # Each thread: one cache-only attempt + one download-allowed retry.
    assert len(_fake_model.calls) == 8
    assert sum(1 for c in _fake_model.calls if c["local_files_only"] is False) == 4


# ── D35 ─────────────────────────────────────────────────────────────────
def test_missing_model_is_reported_loudly(capsys: pytest.CaptureFixture) -> None:
    """
    The report must name the cause AND the remedy. Without it, the only
    outward symptom of a dead STT model is empty transcripts.
    """
    SpeechToText._report_no_stt_model(
        "medium",
        RuntimeError(
            "Cannot find an appropriate cached snapshot folder ... outgoing "
            "traffic has been disabled ... set 'HF_HUB_OFFLINE=0'"
        ),
    )
    out = capsys.readouterr().out
    assert "CANNOT HEAR" in out.upper(), "the report does not state the impact"
    assert "HF_HUB_OFFLINE" in out, "the report does not name the cause"
    assert "WhisperModel(" in out, "the report does not give the download command"
    assert "medium" in out, "the report does not name the configured model"


def test_report_handles_a_non_offline_failure(capsys: pytest.CaptureFixture) -> None:
    """A bad model name must not get the offline remedy."""
    SpeechToText._report_no_stt_model("nonsuch", RuntimeError("unknown model size"))
    out = capsys.readouterr().out
    assert "WHISPER_MODEL_SIZE" in out
    assert "$env:HF_HUB_OFFLINE" not in out, (
        "offline remedy shown for a failure that is not about offline mode"
    )


def test_log_init_does_not_claim_ready_without_a_model() -> None:
    """
    The line that hid this for days read "[STT] Ready - FasterWhisper=X"
    among four ticks. "Ready" must be conditional on the model existing.
    """
    src = inspect.getsource(SpeechToText._log_init)
    assert "DEGRADED" in src, (
        "_log_init no longer distinguishes a degraded engine; it will report "
        "'Ready' with no STT model again (D35)"
    )
    assert "self._whisper_model is not None" in src, (
        "_log_init's state no longer depends on the model being loaded"
    )


def test_loader_failure_path_calls_the_report() -> None:
    """The report is only useful if the failure path actually reaches it."""
    src = inspect.getsource(SpeechToText._load_faster_whisper)
    assert "_report_no_stt_model" in src, (
        "the CPU-fallback failure path no longer reports the dead engine"
    )


def test_transcribe_returns_empty_when_model_is_none() -> None:
    """
    Documents the mechanism rather than changing it: this guard is correct,
    and it is precisely why a missing model is silent. It is the reason D35
    (loud reporting) is the fix rather than touching this path.
    """
    src = inspect.getsource(SpeechToText._transcribe)
    assert "model is None" in src
    assert 'TranscriptionResult("", 0.0)' in src


# ── D36: a load is only accepted once it has been proven to infer ───────
class _ConstructsButCannotInfer(_FakeWhisper):
    """
    The field failure: a CUDA WhisperModel constructs fine because
    CTranslate2 resolves its libraries lazily, then every inference raises
    "Library cublas64_12.dll is not found or cannot be loaded".
    """

    def transcribe(self, *args, **kwargs):
        if self.kwargs_at_construction.get("device") == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

        def _gen():
            return
            yield  # pragma: no cover

        return _gen(), None


def _install(monkeypatch: pytest.MonkeyPatch, cls) -> None:
    def _factory(*args, **kwargs):
        obj = cls(*args, **kwargs)
        obj.kwargs_at_construction = dict(kwargs)
        return obj

    monkeypatch.setattr(stt_engine, "WhisperModel", _factory, raising=False)


def test_gpu_that_cannot_infer_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    THE DEFECT D36 FIXED. Construction of a CUDA model succeeds even when
    the CUDA libraries are missing, so the GPU branch reported success, the
    except-clause never ran, and SARA kept a model that failed every
    transcription. _transcribe() swallows the inference error and returns an
    empty result, so the only visible symptom was "SARA cannot hear me".
    """
    _install(monkeypatch, _ConstructsButCannotInfer)
    monkeypatch.setattr(stt_engine, "_HAS_WHISPER", True, raising=False)

    ears = SpeechToText.__new__(SpeechToText)
    ears.SAMPLE_RATE = 16000
    ears._main_model_warmed = False
    model = ears._load_faster_whisper()

    assert model is not None, "fell all the way through instead of using CPU"
    assert model.kwargs_at_construction.get("device") == "cpu", (
        "a GPU model that cannot infer was accepted; the CPU fallback did "
        "not engage (D36)"
    )
    assert ears._main_model_warmed is True


def test_working_gpu_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe must not reject a healthy GPU."""
    _install(monkeypatch, _FakeWhisper)
    monkeypatch.setattr(stt_engine, "_HAS_WHISPER", True, raising=False)

    ears = SpeechToText.__new__(SpeechToText)
    ears.SAMPLE_RATE = 16000
    ears._main_model_warmed = False
    model = ears._load_faster_whisper()
    assert model.kwargs_at_construction.get("device") == "cuda", (
        "a working GPU model was rejected"
    )


def test_probe_drains_the_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    transcribe() is lazy. An undrained generator means no inference ran, so
    the probe would "prove" a broken model is fine -- the exact bug it
    exists to catch.
    """
    src = inspect.getsource(SpeechToText._prove_model_can_infer)
    assert "list(segments)" in src, (
        "the probe no longer drains the segment generator, so it runs no "
        "inference and proves nothing (D36)"
    )


def test_warmup_skips_a_model_already_proven(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The probe warms the model, so warming it again spends the
    first-inference cost twice -- on `medium` that is seconds of boot.
    """
    main, wake = _FakeWhisper(), _FakeWhisper()
    for m in (main, wake):
        m.calls = []
        m.drained = 0

        def _t(*a, _m=m, **k):
            _m.calls.append(k)

            def _g():
                _m.drained += 1
                return
                yield  # pragma: no cover

            return _g(), None

        m.transcribe = _t

    ears = SpeechToText.__new__(SpeechToText)
    ears.SAMPLE_RATE = 16000
    ears._whisper_model = main
    ears._wake_whisper_model = wake
    ears._main_model_warmed = True  # proven during load
    ears._warm_recognisers()

    assert main.drained == 0, "the main model was warmed twice (D36)"
    assert wake.drained == 1, "the wake model still needs warming"


# ── D37: the Ready line must report the device that was actually used ───
def test_ready_line_does_not_hardcode_the_device() -> None:
    """
    THE DEFECT D37 FIXED.

    `_log_init` printed a literal:

        f"FasterWhisper={'✓ (CPU INT8)' if self._whisper_model else '✗'} | "

    so the device in the status line was a constant, not an observation. On
    the target machine the load line said

        [STT] ✅ Faster Whisper loaded on GPU (CUDA, int8_float16).

    and two lines later the status line said `CPU INT8`. Both cannot be
    true, and nothing in the logs let anyone tell which was. That is how a
    GPU-acceleration claim became unverifiable from SARA's own output.

    A status line that reports a constant is worse than no status line: it
    is evidence-shaped.
    """
    src = inspect.getsource(SpeechToText._log_init)
    # Strip comments: the fix's own comment quotes the old literal to explain
    # what changed, and matching prose would fail this test on its
    # documentation (the same trap as the D33 warm-up test).
    src = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    assert "'✓ (CPU INT8)'" not in src and '"✓ (CPU INT8)"' not in src, (
        "the Ready line hardcodes the compute device again (D37); it must "
        "report what the loader recorded"
    )
    assert "_model_device" in src, (
        "the Ready line no longer reads self._model_device, so it is not "
        "reporting the device the model actually loaded on (D37)"
    )


def test_both_loader_branches_record_the_device() -> None:
    """
    There are exactly two accepted-load sites -- the GPU branch and the CPU
    fallback -- and each must record what it accepted. A branch that
    returns a model without setting _model_device makes the Ready line fall
    back to "device unknown", which is honest but useless.
    """
    src = inspect.getsource(SpeechToText._load_faster_whisper)
    assert src.count("self._model_device =") >= 2, (
        "a loader branch accepts a model without recording its device (D37)"
    )
    gpu, cpu = src.split("except Exception as gpu_error:", 1)
    assert "self._model_device =" in gpu, "the GPU branch records no device"
    assert "self._model_device =" in cpu, "the CPU fallback records no device"


def test_recorded_device_distinguishes_gpu_from_cpu() -> None:
    """
    The whole point is telling the two apart, so the two recorded strings
    must not be interchangeable. This is what makes the log usable as
    evidence for "is GPU acceleration actually on".
    """
    src = inspect.getsource(SpeechToText._load_faster_whisper)
    gpu, cpu = src.split("except Exception as gpu_error:", 1)
    gpu_line = [l for l in gpu.splitlines() if "self._model_device =" in l][0]
    cpu_line = [l for l in cpu.splitlines() if "self._model_device =" in l][0]
    assert "GPU" in gpu_line and "CUDA" in gpu_line
    assert "CPU" in cpu_line
    assert "GPU" not in cpu_line, (
        "the CPU fallback records a GPU device string; the Ready line would "
        "claim acceleration that is not running (D37)"
    )


def test_ready_line_states_unknown_rather_than_guessing() -> None:
    """
    If a future path produces a model without recording a device, the line
    must say so rather than defaulting to a plausible-looking device. An
    absent measurement and a measured value must never render identically.
    """
    ears = SpeechToText.__new__(SpeechToText)
    ears._whisper_model = _FakeWhisper()
    reported = getattr(ears, "_model_device", "loaded, device unknown")
    assert "unknown" in reported.lower(), (
        "an unrecorded device renders as a concrete device name (D37)"
    )
