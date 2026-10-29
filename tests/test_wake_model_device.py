"""
Regression tests for D52: the wake/preview model must try the GPU first.

WHY THIS MODEL'S SPEED MATTERS MORE THAN ITS NAME SUGGESTS
    _load_fast_wake_whisper() loads the small model used for wake checks
    AND for the live-caption PREVIEW. That preview's partial transcript is
    the input the adaptive endpoint reads to recognise a finished command.

    Measured on the target machine (D51), replayed against endpoint.py:

        silence= 352ms  text=None          -> need=625  rule=base      fired=False
        silence= 448ms  text=None          -> need=625  rule=base      fired=False
        silence= 544ms  text=None          -> need=625  rule=base      fired=False
        silence= 640ms  text='open chrome' -> need=350  rule=complete  fired=True

    The partial has to arrive within ENDPOINT_MIN_MS (350ms) for the fast
    path to be reachable at all. It arrived at ~640ms, so every recognised
    command waited `base` (625ms) instead of 350ms -- 288ms each, spent
    waiting on an inference that a literal `device="cpu"` had pinned off
    the GPU while the MAIN Whisper model ran on CUDA on the same machine.

A NOTE ON WHAT D51 WAS *NOT*
    An earlier draft of D51 claimed the debug line reported a rule that
    was not the one applied. Replaying the decision (above) disproved
    that: at 640ms the partial really had arrived and the rule really was
    "complete". The log was honest; the timing was the defect.

WHY THIS IS SAFE
    The GPU path is probed with _prove_model_can_infer() before being
    accepted -- CTranslate2 resolves its CUDA libraries lazily, so a
    successful constructor proves nothing (D36). Any failure falls through
    to the original CPU load, so the worst case is exactly the previous
    behaviour, and WAKE_MODEL_USE_GPU=False restores it deliberately.
"""
from __future__ import annotations

import inspect

from config import Config
from sara.audio.stt.engine import SpeechToText

_SRC = inspect.getsource(SpeechToText._load_fast_wake_whisper)


def _code() -> str:
    return "\n".join(
        l for l in _SRC.splitlines() if not l.lstrip().startswith("#")
    )


def test_the_gpu_is_attempted() -> None:
    code = _code()
    assert 'device="cuda"' in code, (
        "the wake/preview model is pinned to the CPU again, so the live "
        "preview cannot deliver a partial inside ENDPOINT_MIN_MS and the "
        "350ms complete-command path stays unreachable (D52)"
    )


def test_the_gpu_load_is_proven_before_being_accepted() -> None:
    """
    CTranslate2 resolves CUDA lazily: constructing a model on a broken CUDA
    install succeeds and only fails at the first inference, where the error
    is swallowed and the transcript comes back empty. That is D36, and this
    loader must not reintroduce it.
    """
    code = _code()
    gpu_part = code.split('device="cuda"', 1)[1].split("except Exception", 1)[0]
    assert "_prove_model_can_infer" in gpu_part, (
        "the GPU model is accepted without proving it can infer (D36/D52)"
    )


def test_the_cpu_fallback_survives() -> None:
    """The worst case must be the old behaviour, not no model at all."""
    code = _code()
    assert 'device="cpu"' in code, "the CPU fallback was removed (D52)"
    cuda_at = code.index('device="cuda"')
    cpu_at = code.index('device="cpu"')
    assert cpu_at > cuda_at, (
        "the CPU load comes before the GPU attempt, so the GPU is never used"
    )


def test_a_total_failure_still_returns_none_rather_than_raising() -> None:
    """
    The docstring promises wake checks fall back to the main model and
    preview is skipped. That contract depends on returning None, not on
    propagating an exception into __init__.
    """
    code = _code()
    tail = code[code.rindex("except Exception"):]
    assert "return None" in tail, (
        "a failed wake-model load now raises instead of returning None, so "
        "a boot that used to degrade gracefully will crash"
    )


def test_the_switch_is_a_real_config_field() -> None:
    """
    Read via getattr with a default is not enough -- the audit has
    repeatedly found flags that looked tunable via .env and silently were
    not (D31, D40). This one must actually exist on Config.
    """
    assert isinstance(getattr(Config, "WAKE_MODEL_USE_GPU", None), bool), (
        "WAKE_MODEL_USE_GPU is not declared on Config, so setting it in "
        ".env would do nothing (D52)"
    )
    from pathlib import Path
    cfg = Path(inspect.getfile(Config)).read_text(encoding="utf-8")
    import re
    assert re.search(r"^\s+WAKE_MODEL_USE_GPU\s*:", cfg, re.MULTILINE), (
        "WAKE_MODEL_USE_GPU is not declared as a Config FIELD"
    )


def test_the_switch_is_honoured() -> None:
    code = _code()
    assert "WAKE_MODEL_USE_GPU" in code, (
        "the loader no longer reads WAKE_MODEL_USE_GPU, so there is no way "
        "to keep this model on the CPU when the GPU is tight"
    )
    # The GPU attempt must sit inside the switch, not beside it.
    guard_at = code.index("WAKE_MODEL_USE_GPU")
    cuda_at = code.index('device="cuda"')
    assert guard_at < cuda_at, "the GPU attempt is not gated by the switch"


def test_the_device_is_recorded_for_the_log() -> None:
    """
    D37's lesson: a device that is never recorded gets reported as a
    hardcoded guess. Both branches here record what they accepted.
    """
    code = _code()
    assert code.count("self._wake_model_device =") >= 2, (
        "a wake-model load branch accepts a model without recording its "
        "device, so the log cannot say which one is in use (D37/D52)"
    )
    gpu_part, cpu_part = code.split("except Exception", 1)
    assert "GPU" in gpu_part and "CUDA" in gpu_part
    assert "CPU" in cpu_part and "GPU" not in cpu_part.split("self._wake_model_device =")[1][:80]


def test_main_model_loader_is_untouched_by_this() -> None:
    """
    D52 is about the small model. The main loader's own GPU-then-CPU chain
    (D36/D37) must still be there and still be separate.
    """
    main = inspect.getsource(SpeechToText._load_faster_whisper)
    assert 'device="cuda"' in main and 'device="cpu"' in main
    assert "_prove_model_can_infer" in main


# ── the two mistakes found in self-review of this very change ───────────
def test_a_proven_wake_model_is_not_warmed_twice() -> None:
    """
    SELF-REVIEW FINDING. D52's GPU path calls _prove_model_can_infer(),
    which runs one inference and therefore warms the model. Without a flag,
    _warm_recognisers() then warmed it AGAIN -- on the target machine the
    wake warm-up measured 563-719ms, so that was most of a second of
    duplicated boot cost.
    """
    src = _code()
    gpu_part = src.split('device="cuda"', 1)[1].split("except Exception", 1)[0]
    assert "_wake_model_warmed = True" in gpu_part, (
        "the proven wake model is not marked as warmed, so _warm_recognisers "
        "pays its first-inference cost a second time"
    )


def test_the_cpu_fallback_does_NOT_claim_to_be_warmed() -> None:
    """
    The CPU path does not probe, so that model's first-inference cost is
    still unpaid and it must still be warmed. Marking it warmed would move
    that cost onto the user's first spoken command.
    """
    src = _code()
    cpu_part = src.split("except Exception", 1)[1]
    assert "_wake_model_warmed = True" not in cpu_part, (
        "the unproven CPU-loaded wake model claims to be warmed, so its "
        "first-inference cost lands on the user's first command instead"
    )


def test_warm_recognisers_honours_the_wake_flag() -> None:
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._warm_recognisers)
    assert "_wake_model_warmed" in src, (
        "_warm_recognisers no longer checks the wake flag, so a probed "
        "model is warmed twice"
    )


def test_warm_recognisers_still_warms_an_unflagged_wake_model() -> None:
    """Behavioural: no flag set means the wake model must be warmed."""
    import numpy as np  # noqa: F401
    from sara.audio.stt.engine import SpeechToText

    class _Model:
        def __init__(self):
            self.drained = 0

        def transcribe(self, audio, **kw):
            def _g():
                self.drained += 1
                return
                yield  # pragma: no cover

            return _g(), None

    main, wake = _Model(), _Model()
    ears = SpeechToText.__new__(SpeechToText)
    ears.SAMPLE_RATE = 16000
    ears._whisper_model = main
    ears._wake_whisper_model = wake
    ears._main_model_warmed = True          # proven at load
    ears._warm_recognisers()
    assert main.drained == 0, "the proven main model was warmed again"
    assert wake.drained == 1, (
        "an unproven wake model was skipped; only a PROBED model may be"
    )

    main2, wake2 = _Model(), _Model()
    ears2 = SpeechToText.__new__(SpeechToText)
    ears2.SAMPLE_RATE = 16000
    ears2._whisper_model = main2
    ears2._wake_whisper_model = wake2
    ears2._main_model_warmed = True
    ears2._wake_model_warmed = True         # D52 GPU path probed it
    ears2._warm_recognisers()
    assert wake2.drained == 0, (
        "a probed wake model was still warmed, duplicating ~0.6s of boot "
        "cost measured on the target machine"
    )


def test_the_preview_device_is_actually_REPORTED() -> None:
    """
    SELF-REVIEW FINDING. _wake_model_device was written on both load paths
    and never read anywhere -- dead code that looks like a feature, which is
    exactly why D59 was reverted. It has to reach the log, because "is the
    preview model on the GPU" is the question D51's 288ms hinges on.
    """
    from sara.audio.stt.engine import SpeechToText

    src = inspect.getsource(SpeechToText._log_init)
    code = "\n".join(
        l for l in src.splitlines() if not l.lstrip().startswith("#")
    )
    assert "_wake_model_device" in code, (
        "the Ready line does not report the preview model's device, so "
        "recording it is dead code (D52)"
    )
