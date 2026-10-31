"""
sara.audio.tts.prewarm
Background pre-synthesis of fixed phrases (acks, confirmations) into the
in-memory phrase cache, so speak() hits the cache instead of synthesising.
Never raises into its caller; yields to live speech.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from config import Config
from sara.audio.voice_flags import get_flag

from . import cache as _cache
from . import disk_cache as _disk
from .synth import _synth_kokoro
from .text_prep import clean_for_tts
from .voice_params import _build_params, _detect_lang, _fast_variant
from sara.core.diag import swallowed

_WARMUP_WAIT_S = 30.0
_GAP_S = 0.03
_YIELD_POLL_S = 0.05
_MIN_PHRASES = 8
_MAX_PHRASES = 256

_CONFIRMATIONS: tuple[str, ...] = (
    "Done.", "Okay.", "Sure.", "Got it.", "Cancelled.", "Okay, cancelled.",
    "Say yes or cancel.", "I didn't catch that.", "Sorry, something went wrong.",
    "Yes?", "Haan?", "Theek hai.", "Ho gaya.", "Ek second.",
    "ठीक है।", "हो गया।", "रद्द कर दिया।",
)


def _ack_phrases() -> tuple[str, ...]:
    """The orchestrator's ack phrases; empty when that module is unavailable."""
    try:
        from sara.orchestrator.command_helpers import _ACK_PHRASES

        return tuple(str(p) for p in _ACK_PHRASES)
    except Exception:
        return ()


def _limits(cfg: object) -> tuple[int, int]:
    try:
        maxlen = int(getattr(cfg, "TTS_PHRASE_CACHE_MAXLEN", 40))
    except Exception:
        maxlen = 40
    try:
        cap = int(getattr(cfg, "VOICE_PREWARM_MAX_PHRASES", 64))
    except Exception:
        cap = 64
    return maxlen, max(_MIN_PHRASES, min(_MAX_PHRASES, cap))


def _plan(cfg: object = None) -> list[tuple[str, str, bool]]:
    """(clean text, lang, fast) jobs in priority order, de-duplicated and capped."""
    cfg = cfg if cfg is not None else Config
    maxlen, cap = _limits(cfg)
    plan: list[tuple[str, str, bool]] = []
    seen: set[tuple[str, bool]] = set()

    def add(text: str, fast: bool) -> None:
        try:
            clean = clean_for_tts((text or "").strip())
            if not clean or len(clean) > maxlen or (clean, fast) in seen:
                return
            lang = _detect_lang(clean)
        except Exception:
            return
        seen.add((clean, fast))
        plan.append((clean, lang, fast))

    spoken_fast = list(_ack_phrases()) + [str(getattr(cfg, "WAKE_ACK_PHRASE", "Yes?"))]
    for text in spoken_fast:
        add(text, True)
    for text in _CONFIRMATIONS:
        add(text, False)
    for text in _CONFIRMATIONS:
        add(text, True)
    return plan[:cap]


def phrases(cfg: object = None) -> list[tuple[str, str]]:
    """Unique (text, lang) pairs to pre-synthesise, in priority order."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for text, lang, _ in _plan(cfg):
        if text not in seen:
            seen.add(text)
            out.append((text, lang))
    return out


def cache_key_for(tts: object, text: str, lang: str, fast: bool) -> tuple:
    """Exactly the key TextToSpeech.speak() builds for an already-cleaned text."""
    return (text, lang, bool(fast), tts.get_speed())  # type: ignore[attr-defined]


def _effective_params(tts: object, lang: str, fast: bool):
    params = _build_params(lang)
    if fast:
        params = _fast_variant(params)
    return tts._apply_speed_override(params)  # type: ignore[attr-defined]


def _should_abort(tts: object) -> bool:
    return bool(getattr(tts, "_shutdown_done", False)) or not get_flag("VOICE_ACK_CACHE")


def _wait_ready(tts: object, clock: Callable[[], float]) -> bool:
    """Wait (<= 30 s) for the TTS warm-up; False on timeout or abort."""
    event = getattr(tts, "_warmup_done", None)
    if event is None:
        return False
    deadline = clock() + _WARMUP_WAIT_S
    while not event.is_set():
        if _should_abort(tts) or clock() >= deadline:
            return False
        event.wait(0.2)
    return True


def _wait_turn(tts: object, sleeper: Callable[[float], None]) -> bool:
    """Block while speech is active; False when prewarm should stop."""
    while True:
        if _should_abort(tts):
            return False
        if not tts._speaking.is_set():  # type: ignore[attr-defined]
            return True
        sleeper(_YIELD_POLL_S)


def _prewarm_one(
    tts: object, text: str, lang: str, fast: bool, directory, model_fp: str
) -> str:
    """Fill one cache entry. Returns 'hit', 'disk', 'synth' or 'skip'."""
    speed = tts.get_speed()  # type: ignore[attr-defined]
    key = (text, lang, bool(fast), speed)
    if _cache._phrase_cache_get(key) is not None:
        _cache.pin(key)
        return "hit"
    params = _effective_params(tts, lang, fast)
    digest = None
    if directory is not None:
        digest = _disk.entry_hash(
            text, lang, fast, speed, params.voice, params.speed, model_fp
        )
        loaded = _disk.load(directory, digest)
        if loaded is not None:
            _cache.pin(key)
            _cache._phrase_cache_put(key, loaded.copy())
            return "disk"
    pcm = _synth_kokoro(
        text, tts._kokoro, params, tts._synth_lock  # type: ignore[attr-defined]
    )
    if pcm is None or len(pcm) == 0 or tts.get_speed() != speed:  # type: ignore[attr-defined]
        return "skip"
    _cache.pin(key)
    _cache._phrase_cache_put(key, pcm.copy())
    if directory is not None and digest is not None:
        _disk.store(directory, digest, pcm)
    return "synth"


def _run(
    tts: object, sleeper: Callable[[float], None], clock: Callable[[], float]
) -> None:
    started = clock()
    plan: list[tuple[str, str, bool]] = []
    done = 0
    from_disk = stored = 0
    try:
        if not _wait_ready(tts, clock):
            return
        plan = _plan()
        directory = _disk.cache_dir() if get_flag("VOICE_ACK_CACHE_DISK") else None
        model_fp = (
            _disk.model_fingerprint(
                str(getattr(Config, "KOKORO_MODEL_PATH", "models/kokoro-v1.0.onnx"))
            )
            if directory is not None
            else ""
        )
        _cache.unpin_all()
        for text, lang, fast in plan:
            if not _wait_turn(tts, sleeper):
                break
            try:
                status = _prewarm_one(tts, text, lang, fast, directory, model_fp)
            except Exception:
                status = "skip"
            if status != "skip":
                done += 1
            from_disk += status == "disk"
            stored += status == "synth" and directory is not None
            sleeper(_GAP_S)
    except Exception as _exc:
        swallowed(None, "prewarm._run:200", _exc)
    finally:
        try:
            print(
                f"[TTS] prewarmed {done} phrases in {clock() - started:.1f} s "
                f"({max(0, len(plan) - done)} skipped)"
            )
            if from_disk or stored:
                print(f"[TTS] disk cache: {from_disk} loaded, {stored} stored")
        except Exception as _exc:
            swallowed(None, "prewarm._run:210", _exc)


def start_prewarm(
    tts: object,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[threading.Thread]:
    """Start the daemon prewarm thread; None when off, disabled or without a model."""
    try:
        if not get_flag("VOICE_ACK_CACHE"):
            return None
        if getattr(tts, "_disabled", True) or getattr(tts, "_kokoro", None) is None:
            return None
        running = getattr(tts, "_prewarm_thread", None)
        if running is not None and running.is_alive():
            return running
        thread = threading.Thread(
            target=_run, args=(tts, sleeper, clock), daemon=True, name="TTS-Prewarm"
        )
        tts._prewarm_thread = thread  # type: ignore[attr-defined]
        thread.start()
        return thread
    except Exception:
        print("[TTS] prewarm could not start; continuing without it")
        return None