"""
sara.audio.tts.engine
TextToSpeech -- the public class. Wires voice_params + text_prep + cache +
synth + player together into speak()/speak_stream().
"""

from __future__ import annotations

from .voice_params import (
    _detect_lang,
    _build_params,
    _fast_variant,
    _speed_override_variant,
)
from .text_prep import clean_for_tts, _split_adaptive
from .synth import _synth_kokoro
from .player import _PersistentPlayer, _drain


import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from config import Config

try:
    import sounddevice as sd

    _SD_OK = True
except (ImportError, OSError):
    _SD_OK = False
    sd = None
    print("[TTS] sounddevice not found — pip install sounddevice")

try:
    import pygame

    _PG_OK = True
except ImportError:
    _PG_OK = False
    pygame = None

try:
    from kokoro_onnx import Kokoro

    _KOKORO_OK = True
except ImportError:
    _KOKORO_OK = False
    Kokoro = None
    print("[TTS] kokoro-onnx not found — pip install kokoro-onnx")

try:
    import onnxruntime as _ort

    _ORT_OK = True
except ImportError:
    _ORT_OK = False
    _ort = None

# ── Constants ─────────────────────────────────────────────────────────────────
_SAMPLE_RATE = 24000  # Kokoro v1.0 native output rate
_CHANNELS = 1
_POLL_S = 0.008
_MIN_CHUNK = 8
_MAX_CHUNK = 180
_FIRST_TRIGGER = 5  # lowered from 8 — flush first micro-chunk sooner
_QUEUE_TIMEOUT = 15.0

_PLAY_BUFFER_MS = int(getattr(Config, "TTS_PLAYBACK_BUFFER_MS", 40))
_PLAY_LATENCY = getattr(Config, "TTS_SD_LATENCY", "low")
_BLOCK_SIZE = max(256, int(_SAMPLE_RATE * _PLAY_BUFFER_MS / 1000))

# Sub-chunk size used when feeding PCM into the persistent player's queue —
# keeps individual queued items small so stop()/clear() during playback
# takes effect within a few blocks instead of after one giant array drains.
_ENQUEUE_CHUNK_SAMPLES = _BLOCK_SIZE * 4

# Bounded queue for handing played blocks off to the AEC far-end feeder
# thread. Small and lossy by design — dropping an occasional block just
# means a few ms less far-end reference data, which AEC tolerates fine;
# blocking the real-time callback to guarantee delivery is far worse.
_FAR_END_QUEUE_MAXSIZE = 64
_FAR_END_IDLE_POLL_S = 0.5

_ORT_INTRA_THREADS = int(getattr(Config, "ORT_INTRA_THREADS", os.cpu_count() or 4))
_ORT_INTER_THREADS = int(getattr(Config, "ORT_INTER_THREADS", 1))

_WARMUP_TEXTS_EN = ["Hi.", "This is a warm up sentence for the model."]
_WARMUP_TEXTS_HI = ["नमस्ते।"]
_WARMUP_WAIT_S = float(getattr(Config, "TTS_WARMUP_WAIT_S", 2.0))

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# CUDA availability, decided once at import time — drives adaptive queue sizing.
_CUDA_AVAILABLE = bool(
    _ORT_OK and "CUDAExecutionProvider" in _ort.get_available_providers()
)

# Adaptive queue sizing — GPU path synthesizes faster, so deeper queues keep
# the pipeline fed without wasting memory on CPU-only setups.
_SYNTH_QUEUE_SIZE = int(
    getattr(Config, "TTS_SYNTH_QUEUE_SIZE", 12 if _CUDA_AVAILABLE else 8)
)
_PLAY_QUEUE_SIZE = int(
    getattr(Config, "TTS_PLAY_QUEUE_SIZE", 6 if _CUDA_AVAILABLE else 4)
)

# Short-phrase PCM cache (greetings, acks, wake responses, etc.)
_PHRASE_CACHE_MAX = int(getattr(Config, "TTS_PHRASE_CACHE_SIZE", 64))
_PHRASE_CACHE_MAXLEN = int(getattr(Config, "TTS_PHRASE_CACHE_MAXLEN", 40))

# v15: live speed-override range for set_speed() (GUI Voice Control
# slider). Mirrors sara/orchestrator/tts_worker.py's _KOKORO_SPEED_MIN/MAX
# — kept as a local constant rather than importing from tts_worker.py to
# avoid the audio layer depending on the orchestrator layer. If you ever
# change one, change the other; they document the same underlying
# Kokoro-speed range and should not drift apart.
_SPEED_OVERRIDE_MIN = 0.6
_SPEED_OVERRIDE_MAX = 1.4


# ══════════════════════════════════════════════════════════════════════════════
#  LANGUAGE DETECTION
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
#  SEGMENT
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class _Seg:
    text: str
    lang: str
    pcm: np.ndarray | None = None
    sample_rate: int = _SAMPLE_RATE
    ready: threading.Event = field(default_factory=threading.Event)
    failed: bool = False


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN CLASS
# ══════════════════════════════════════════════════════════════════════════════


class TextToSpeech:
    """
    Kokoro-ONNX-backed TTS for SARA AI.

    - Persistent in-process model (loaded once, warmed up in background).
    - GPU-accelerated via CUDAExecutionProvider when available (tuned for RTX 3050),
      CPU fallback otherwise.
    - Streaming pipeline: Chunker -> Synth -> Playback, running on persistent worker pools.
    - Persistent output device stream, non-blocking real-time callback,
      with AEC far-end feed handled on its own background thread (v11).
    - v12: optional manual language force (self._forced_lang) so a caller
      (e.g. a GUI EN/HI toggle) can pin speech to one language instead of
      relying purely on per-sentence script auto-detection.
    - v14 (LATENCY): short-phrase PCM cache — repeated fixed phrases
      (wake ack, sleep/goodbye lines, etc.) skip Kokoro synthesis
      entirely on a cache hit, and also skip waiting on the warm-up
      thread since a cache hit implies warm-up already ran.
    """

    def __init__(self, aec=None) -> None:
        self._stop = threading.Event()
        self._speaking = threading.Event()
        self._lock = threading.Lock()
        self._aec = aec

        # v13: persistent "interrupted" latch — separate from self._stop.
        # self._stop only cancels the ONE segment currently mid-playback
        # (cleared again at the top of every speak() call). This latch
        # stays SET across multiple speak() calls until something
        # explicitly clears it (a new command / wake event), so a
        # multi-sentence reply where each sentence goes through its own
        # speak() call actually stays silent after Stop is pressed,
        # instead of resuming on the next sentence.
        self._interrupted = threading.Event()

        # v14 (LATENCY): short-phrase PCM cache — greetings/acks/sleep
        # lines repeat verbatim every cycle, so cache synthesized audio
        # and skip Kokoro synthesis on hits (only playback time remains).
        # FIFO eviction once _PHRASE_CACHE_MAX entries held. Keyed on
        # (text, lang, fast, speed_override) since fast=True uses
        # different synth params and a live speed override (v15, see
        # set_speed() below) must not reuse audio cached at a different
        # speed. Only used by the non-streaming speak() path —
        # speak_stream() sentences vary too much to benefit and aren't
        # cached.
        self._phrase_cache: dict[tuple[str, str, bool, float | None], np.ndarray] = {}
        self._phrase_cache_order: list[tuple[str, str, bool, float | None]] = []
        self._phrase_cache_lock = threading.Lock()

        # v12: None -> automatic per-sentence detection via _detect_lang().
        # "en" / "hi" -> caller has manually forced that language via
        # set_language(), overriding auto-detection everywhere below.
        self._forced_lang: str | None = None

        # v15 (BUGFIX): live speed override for the Voice Control page's
        # slider. Previously TextToSpeech had NO set_speed() method at
        # all, so TTSWorker.set_speed()'s hasattr() check silently found
        # nothing and the slider was a no-op. None = no override (use the
        # per-language Config speed as before); a float = override every
        # segment's speed to this value regardless of language, until
        # cleared.
        self._speed_override: float | None = None
        self._speed_lock = threading.Lock()

        self._disabled = False
        self._synth_lock = threading.Lock()

        # ── Kokoro ONNX Initialization (persistent, in-process) ────────────────
        if not _KOKORO_OK:
            print(
                "[TTS] kokoro-onnx is unavailable — TTS is disabled. "
                "Run: pip install kokoro-onnx."
            )
            self._disabled = True
            self._warmup_done = threading.Event()
            self._warmup_done.set()
            self._player = None
            self._chunker_pool = None
            self._synth_pool = None
            self._play_pool = None
            self._volume = float(getattr(Config, "TTS_VOLUME", 1.0))
            return

        model_path = getattr(Config, "KOKORO_MODEL_PATH", "models/kokoro-v1.0.onnx")
        voices_path = getattr(Config, "KOKORO_VOICES_PATH", "models/voices-v1.0.bin")

        if not os.path.isfile(model_path) or not os.path.isfile(voices_path):
            print(
                "[TTS] Kokoro model or voices file is missing — TTS is disabled. "
                f"model={model_path} voices={voices_path}"
            )
            self._disabled = True
            self._warmup_done = threading.Event()
            self._warmup_done.set()
            self._player = None
            self._chunker_pool = None
            self._synth_pool = None
            self._play_pool = None
            self._volume = float(getattr(Config, "TTS_VOLUME", 1.0))
            return

        self._synth_lock = threading.Lock()  # serializes calls into the ONNX session

        session_options = None
        providers = None
        if _ORT_OK:
            session_options = _ort.SessionOptions()
            session_options.intra_op_num_threads = _ORT_INTRA_THREADS
            session_options.inter_op_num_threads = _ORT_INTER_THREADS
            session_options.execution_mode = _ort.ExecutionMode.ORT_SEQUENTIAL
            session_options.graph_optimization_level = (
                _ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            session_options.enable_mem_pattern = True
            session_options.enable_cpu_mem_arena = True

            available = _ort.get_available_providers()
            use_gpu = (
                getattr(Config, "KOKORO_USE_GPU", True)
                and "CUDAExecutionProvider" in available
            )
            if use_gpu:
                providers = [
                    (
                        "CUDAExecutionProvider",
                        {
                            "device_id": 0,
                            "arena_extend_strategy": "kSameAsRequested",
                            "gpu_mem_limit": int(
                                getattr(Config, "CUDA_GPU_MEM_LIMIT_BYTES", 3 * 1024**3)
                            ),
                            "cudnn_conv_algo_search": "HEURISTIC",
                            "cudnn_conv_use_max_workspace": "1",
                            "do_copy_in_default_stream": True,
                            "enable_cuda_graph": "0",  # variable-length text -> dynamic shapes, graphs don't help
                        },
                    ),
                    "CPUExecutionProvider",
                ]
            else:
                providers = ["CPUExecutionProvider"]

        # NOTE: kokoro_onnx.Kokoro's own constructor never accepts
        # session_options=/providers= kwargs in any version -- it always
        # builds its own onnxruntime.InferenceSession internally. To use a
        # custom SessionOptions/providers list (GPU tuning, thread counts),
        # build the session ourselves and hand it to Kokoro via the
        # Kokoro.from_session(session, voices_path) classmethod instead.
        try:
            if _ORT_OK:
                try:
                    session = _ort.InferenceSession(
                        model_path,
                        sess_options=session_options,
                        providers=providers,
                    )
                    self._kokoro = Kokoro.from_session(session, voices_path)
                except Exception as e:
                    print(
                        f"[TTS] Custom ONNX session setup failed ({e}); "
                        f"falling back to Kokoro's default session."
                    )
                    self._kokoro = Kokoro(model_path, voices_path)
            else:
                self._kokoro = Kokoro(model_path, voices_path)
        except Exception as e:
            print(
                f"[TTS] Kokoro initialization failed ({e}) — TTS is disabled."
            )
            self._disabled = True
            self._warmup_done = threading.Event()
            self._warmup_done.set()
            self._player = None
            self._chunker_pool = None
            self._synth_pool = None
            self._play_pool = None
            self._volume = float(getattr(Config, "TTS_VOLUME", 1.0))
            return

        if getattr(Config, "DEBUG_MODE", False) and _ORT_OK:
            active = providers[0] if isinstance(providers, list) else "default"
            print(
                f"[TTS] ONNX providers requested: {active} | available: {_ort.get_available_providers()}"
            )

        self._warmup_done = threading.Event()
        threading.Thread(target=self._warm_up, daemon=True, name="TTS-WarmUp").start()

        # Persistent worker pools — avoids spawning new OS threads on every
        # speak()/speak_stream() call and every playback segment.
        self._chunker_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="TTS-Chunker"
        )
        self._synth_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="TTS-Synth"
        )
        self._play_pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="TTS-Play"
        )

        # Single persistent output stream instead of open/close per
        # segment — also the real-time AEC far-end feed source (v11:
        # feed itself is off-thread, see _PersistentPlayer).
        self._player = _PersistentPlayer(aec=self._aec)

        if _PG_OK and pygame is not None:
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.pre_init(
                        frequency=_SAMPLE_RATE,
                        size=-16,
                        channels=2,
                        buffer=_BLOCK_SIZE,
                    )
                    pygame.mixer.init()
            except Exception:
                pass

        self._volume: float = float(getattr(Config, "TTS_VOLUME", 1.0))

        if getattr(Config, "DEBUG_MODE", False):
            aec_status = "wired" if self._aec is not None else "not provided"
            print(
                f"[TTS v12] Ready | Active Engine: KOKORO-ONNX (in-process) | AEC: {aec_status}"
            )

    def _warm_up(self) -> None:
        try:
            t0 = time.time()
            for txt in _WARMUP_TEXTS_EN:
                _synth_kokoro(txt, self._kokoro, _build_params("en"), self._synth_lock)
            for txt in _WARMUP_TEXTS_HI:
                _synth_kokoro(txt, self._kokoro, _build_params("hi"), self._synth_lock)

            # v14 (LATENCY): pre-warm the wake-ack into the phrase cache
            # using the exact same (text, lang, fast) key core_wiring.py's
            # ack call will use (fast=True), so even the FIRST wake cycle
            # after boot is a cache hit instead of paying full synthesis
            # cost once more.
            try:
                ack_text = clean_for_tts(getattr(Config, "WAKE_ACK_PHRASE", "Yes?"))
                if ack_text and len(ack_text) <= _PHRASE_CACHE_MAXLEN:
                    ack_params = _fast_variant(_build_params("en"))
                    ack_pcm = _synth_kokoro(
                        ack_text, self._kokoro, ack_params, self._synth_lock
                    )
                    if ack_pcm is not None and len(ack_pcm) > 0:
                        with self._phrase_cache_lock:
                            # v15: 4-tuple key now — None here matches
                            # "no speed override active", which is the
                            # state at warm-up time (nothing has called
                            # set_speed() yet this session).
                            key = (ack_text, "en", True, None)
                            if key not in self._phrase_cache:
                                self._phrase_cache_order.append(key)
                            self._phrase_cache[key] = ack_pcm.copy()
            except Exception as e:
                if getattr(Config, "DEBUG_MODE", False):
                    print(f"[TTS] wake-ack pre-cache failed (non-fatal): {e}")

            if getattr(Config, "DEBUG_MODE", False):
                print(f"[TTS] warm-up complete in {time.time() - t0:.3f}s")
        except Exception as e:
            if getattr(Config, "DEBUG_MODE", False):
                print(f"[TTS] warm-up failed: {e}")
        finally:
            self._warmup_done.set()

    # ── Synthesis router ─────────────────────────────────────────────────────

    def _synth(self, text: str, lang: str) -> np.ndarray | None:
        params = _build_params(lang)
        return _synth_kokoro(text, self._kokoro, params, self._synth_lock)

    def set_language(self, lang: str) -> None:
        """
        v12: manually force a language, or return to automatic detection.

        - "auto"                  -> self._forced_lang = None (per-sentence
                          auto-detection via _detect_lang() decides, as before)
        - "en" / "hi" / "hinglish" -> self._forced_lang = lang (every segment
                          spoken via speak()/speak_stream() uses this language,
                          ignoring what script the text happens to contain).
                          "hinglish" is stored and reported exactly as given
                          (see get_language()) — only voice SELECTION
                          (_build_params(), voice_params.py) treats it the
                          same as "hi".
        Any other value is ignored (forced language stays unchanged).
        """
        if lang == "auto":
            self._forced_lang = None
        elif lang in ("en", "hi", "hinglish"):
            self._forced_lang = lang

    def get_language(self) -> str:
        """v12: returns the actual current state — the forced language if
        one is set, otherwise 'auto' (meaning per-sentence auto-detect)."""
        return self._forced_lang if self._forced_lang is not None else "auto"

    def set_speed(self, speed: float | None) -> None:
        """v15 (BUGFIX): live speed override, called by TTSWorker.set_speed()
        (sara/orchestrator/tts_worker.py), itself called from the GUI's
        Voice Control page slider. This method did not exist before —
        TTSWorker's hasattr() guard silently found nothing and the slider
        did nothing.

        `speed=None` clears the override (falls back to the normal
        per-language Config speed, i.e. KOKORO_SPEED_EN / KOKORO_SPEED_HI).
        Any float is clamped to [_SPEED_OVERRIDE_MIN, _SPEED_OVERRIDE_MAX]
        and applied to EVERY segment spoken from this point on — English
        or Hindi — until cleared, since a single GUI slider controls "how
        fast Sara talks" without a separate control per language.
        """
        with self._speed_lock:
            if speed is None:
                self._speed_override = None
            else:
                self._speed_override = max(
                    _SPEED_OVERRIDE_MIN, min(_SPEED_OVERRIDE_MAX, float(speed))
                )

    def get_speed(self) -> float | None:
        """v15: current override, or None if using per-language Config speed."""
        with self._speed_lock:
            return self._speed_override

    def _apply_speed_override(self, params):
        """v15: shared by every call site that builds _VoiceParams below —
        applies the live override (if any) on top of whatever _build_params()/
        _fast_variant() already produced."""
        override = self.get_speed()
        if override is None:
            return params
        return _speed_override_variant(params, override)

    # ── Public API ────────────────────────────────────────────────────────────

    def speak(self, text: str, fast: bool = False) -> None:
        text = clean_for_tts((text or "").strip())
        if not text:
            return
        # v13: if the user hit Stop and nothing has cleared it since
        # (see clear_interrupt()), skip this segment entirely — this is
        # what actually silences the REST of a multi-sentence reply, not
        # just the sentence that was playing at the moment Stop was
        # pressed.
        if self._interrupted.is_set():
            return

        if self._disabled:
            if getattr(Config, "DEBUG_MODE", False):
               print(f"[TTS] disabled speak(): {text[:50]}")
            return

        with self._lock:
            self._stop.clear()
            self._speaking.set()
            try:
               # v12: use the manually forced language if one is set,
               # otherwise fall back to auto-detection exactly as before.
               lang = (
                   self._forced_lang
                   if self._forced_lang is not None
                   else _detect_lang(text)
               )
               params = _build_params(lang)
               if fast:
                   params = _fast_variant(params)
               params = self._apply_speed_override(params)

               # v14 (LATENCY): phrase cache check FIRST, before waiting on
               # warm-up at all. A cache hit means Kokoro already
               # synthesized this exact (text, lang, fast) combo once
               # before — which can only happen after warm-up already ran
               # — so there's no reason to block on _warmup_done here.
               #
               # v15 (BUGFIX): cache key now also includes the current
               # speed override. Before this fix, changing speed via
               # set_speed() while a phrase was already cached at the OLD
               # speed would silently keep returning the stale-speed
               # cached audio forever — the override would have no
               # audible effect on any previously-cached phrase.
               cacheable = len(text) <= _PHRASE_CACHE_MAXLEN
               cache_key = (text, lang, fast, self.get_speed()) if cacheable else None
               pcm = None
               if cache_key is not None:
                   with self._phrase_cache_lock:
                       cached = self._phrase_cache.get(cache_key)
                   if cached is not None:
                       pcm = cached.copy()

               if pcm is None:
                   # Cache miss — behave exactly as before.
                   self._warmup_done.wait(timeout=_WARMUP_WAIT_S)
                   pcm = _synth_kokoro(text, self._kokoro, params, self._synth_lock)
                   if cache_key is not None and pcm is not None and len(pcm) > 0:
                       with self._phrase_cache_lock:
                           if cache_key not in self._phrase_cache:
                               if len(self._phrase_cache_order) >= _PHRASE_CACHE_MAX:
                                   oldest = self._phrase_cache_order.pop(0)
                                   self._phrase_cache.pop(oldest, None)
                               self._phrase_cache_order.append(cache_key)
                           self._phrase_cache[cache_key] = pcm.copy()

               if pcm is not None and len(pcm) > 0:
                   self._player.play_and_wait(pcm, self._stop, self._volume)
               elif getattr(Config, "DEBUG_MODE", False):
                   print(
                       f'[TTS] speak(): empty audio — lang={lang} text="{text[:50]}"'
                   )
            finally:
               self._speaking.clear()

    def speak_stream(self, text_chunks: Iterator[str], fast: bool = False) -> bool:
        # v13: same latch guard as speak() — see its comment above.
        if self._interrupted.is_set():
            return True

        if self._disabled:
            if getattr(Config, "DEBUG_MODE", False):
               print("[TTS] disabled speak_stream()");
            for _ in text_chunks:
               pass
            return False

        with self._lock:
            self._stop.clear()
            self._speaking.set()
            interrupted = False

            self._warmup_done.wait(timeout=_WARMUP_WAIT_S)

            # -- Kokoro Async Queue Path --
            synth_q: queue.Queue[str | None] = queue.Queue(maxsize=_SYNTH_QUEUE_SIZE)
            play_q: queue.Queue[_Seg | None] = queue.Queue(maxsize=_PLAY_QUEUE_SIZE)

            self._chunker_pool.submit(self._chunker_worker, text_chunks, synth_q)
            self._synth_pool.submit(self._synth_worker, synth_q, play_q, fast)

            try:
               interrupted = self._playback_worker(play_q)
            finally:
               self._stop.set()
               _drain(synth_q)
               _drain(play_q)
               self._speaking.clear()

        return interrupted

    def stop(self) -> None:
        self._stop.set()
        # v13: latch the interruption so subsequent speak() calls for the
        # SAME reply (each sentence often goes through its own speak()
        # call from the caller's loop) stay silent too, not just the one
        # segment that was playing right now.
        self._interrupted.set()
        # Drop whatever's still queued on the device immediately instead of
        # letting it drain out naturally — this is what makes barge-in feel
        # instant rather than "stops after the current buffered chunk".
        if getattr(self, "_player", None) is not None:
            self._player.clear()

    def clear_interrupt(self) -> None:
        """v13: call this when a genuinely NEW command/utterance begins
        (typed command, wake word, etc.) so speak() actually speaks again
        after a prior Stop press latched self._interrupted."""
        self._interrupted.clear()

    def is_interrupted(self) -> bool:
        """v13: True if stop() was called and nothing has cleared it yet."""
        return self._interrupted.is_set()

    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def shutdown(self) -> None:
        self.stop()
        for pool in (
            getattr(self, "_chunker_pool", None),
            getattr(self, "_synth_pool", None),
            getattr(self, "_play_pool", None),
        ):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        player = getattr(self, "_player", None)
        if player is not None:
            player.close()

    def __del__(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass

    # ── Pipeline workers ─────────────────────────────────────────────────────

    def _chunker_worker(self, chunks: Iterator[str], synth_q: queue.Queue) -> None:
        buf = ""
        first_pushed = False
        try:
            for raw in chunks:
                if self._stop.is_set():
                    break
                buf += raw + " "

                if not first_pushed and len(buf.strip()) >= _FIRST_TRIGGER:
                    m = re.search(r"[,.!?;:\n।\u0964]", buf)
                    if m:
                        part = buf[: m.end()].strip()
                        if part:
                            synth_q.put(part)
                            first_pushed = True
                            buf = buf[m.end() :]
                        continue

                segs = _split_adaptive(buf)
                if len(segs) > 1:
                    for s in segs[:-1]:
                        s = s.strip()
                        if s and not self._stop.is_set():
                            synth_q.put(s)
                            first_pushed = True
                    buf = segs[-1]

            tail = buf.strip()
            if tail and not self._stop.is_set():
                synth_q.put(tail)
        except Exception as e:
            if getattr(Config, "DEBUG_MODE", False):
                print(f"[TTS Chunker] {e}")
        finally:
            if self._stop.is_set():
                _drain(synth_q)
            synth_q.put(None)

    def _synth_worker(
        self, synth_q: queue.Queue, play_q: queue.Queue, fast: bool
    ) -> None:
        def _put_seg(seg: _Seg) -> None:
            while True:
                try:
                    play_q.put(seg, timeout=0.1)
                    return
                except queue.Full:
                    if self._stop.is_set():
                        return

        def _make_seg(text: str) -> _Seg:
            # v12: use the manually forced language if one is set,
            # otherwise fall back to auto-detection exactly as before.
            lang = (
                self._forced_lang
                if self._forced_lang is not None
                else _detect_lang(text)
            )
            seg = _Seg(text=text, lang=lang, sample_rate=_SAMPLE_RATE)
            params = _build_params(lang)
            if fast:
                params = _fast_variant(params)
            params = self._apply_speed_override(params)
            try:
                pcm = _synth_kokoro(text, self._kokoro, params, self._synth_lock)
                seg.pcm = pcm
                seg.failed = pcm is None or len(pcm) == 0
            except Exception as e:
                seg.failed = True
                if getattr(Config, "DEBUG_MODE", False):
                    print(f"[TTS Synth] {e}")
            seg.ready.set()
            return seg

        try:
            while True:
                try:
                    item = synth_q.get(timeout=_QUEUE_TIMEOUT)
                except queue.Empty:
                    break

                if item is None:
                    break

                clean = clean_for_tts(item)
                if not clean:
                    continue

                seg = _make_seg(clean)

                if not self._stop.is_set():
                    _put_seg(seg)

                if self._stop.is_set():
                    break

        except Exception as e:
            if getattr(Config, "DEBUG_MODE", False):
                print(f"[TTS Synth outer] {e}")
        finally:
            if self._stop.is_set():
                _drain(play_q)
            play_q.put(None)

    def _playback_worker(self, play_q: queue.Queue) -> bool:
        interrupted = False
        next_seg: _Seg | None = None
        have_next = False

        while True:
            if have_next:
                seg = next_seg
                have_next = False
                next_seg = None
            else:
                try:
                    seg = play_q.get(timeout=15)
                except queue.Empty:
                    break

            if seg is None:
                break
            if self._stop.is_set():
                interrupted = True
                break

            seg.ready.wait(timeout=10)
            if seg.failed or seg.pcm is None or len(seg.pcm) == 0:
                if getattr(Config, "DEBUG_MODE", False):
                    print(
                        f'[TTS Playback] skipping failed seg: "{seg.text[:40]}" lang={seg.lang}'
                    )
                continue
            if self._stop.is_set():
                interrupted = True
                break

            play_done = threading.Event()
            play_interrupted = [False]

            def _play_bg(pcm_data: np.ndarray) -> None:
                play_interrupted[0] = self._player.play_and_wait(
                    pcm_data, self._stop, self._volume
                )
                play_done.set()

            play_future = self._play_pool.submit(_play_bg, seg.pcm)

            if not have_next:
                try:
                    next_seg = play_q.get_nowait()
                    have_next = True
                    if next_seg is not None and not next_seg.ready.is_set():
                        next_seg.ready.wait(timeout=8)
                except queue.Empty:
                    pass

            play_done.wait()

            try:
                play_future.result(timeout=30)
            except Exception:
                pass

            if play_interrupted[0] or self._stop.is_set():
                interrupted = True
                break

        return interrupted