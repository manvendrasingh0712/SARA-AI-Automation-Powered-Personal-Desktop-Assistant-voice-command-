"""
sara/audio/aec.py
Acoustic Echo Cancellation (AEC) wrapper — WebRTC Audio Processing Module
via the `aec-audio-processing` package (Windows-wheel-friendly binding of
Google's WebRTC APM: AEC + Noise Suppression + AGC + VAD).

WHY THIS FILE EXISTS
---------------------
Without real AEC, TTS speaker output leaking into the mic (no headphones)
cannot be reliably told apart from genuine speech using energy/VAD
heuristics alone — that was the root cause of Sara "hearing herself" and,
symmetrically, misfiring on real speech when those heuristics were made
stricter to compensate. This module does real reference-signal-based
cancellation instead of guessing.

HOW IT'S WIRED (see tts.py / stt.py / core.py for the other end of this)
-------------------------------------------------------------------------
  - TTS's persistent output callback pushes the EXACT samples being sent
    to the speaker into `feed_far_end()` in real time (the "reverse"/
    far-end/reference stream).
  - STT's mic callback runs every raw mic chunk through
    `process_near_end()` BEFORE it reaches the ring/pre-speech buffers —
    what comes out has the correlated echo component removed.
  - One AECProcessor instance is shared between TTS and STT (constructed
    once in build_core_objects()).

CONCURRENCY DESIGN NOTE (important — read before "fixing" this)
-----------------------------------------------------------------
`feed_far_end()` and `process_near_end()` are called continuously and
CONCURRENTLY from two different threads for the entire lifetime of a
conversation: TTS's background far-end-feeder thread calls
`feed_far_end()` whenever Sara is speaking, while STT's AEC-worker
thread calls `process_near_end()` for every mic chunk, regardless of
whether TTS is speaking. This is the INTENDED, standard usage pattern
for a real-time duplex echo canceller (WebRTC APM is explicitly
designed around one thread continuously feeding the "render"/reverse
stream while another continuously feeds the "capture"/near-end stream)
— it is not an accidental race to "fix" by adding a single global lock
across both calls. Each side already uses its own lock (`_far_lock`,
`_near_lock`) to protect its own buffer bookkeeping; a single shared
lock across both sides would serialize the two independent real-time
audio paths against each other and could introduce audio glitches or
deadlock risk without a clear correctness benefit, so this file
deliberately does NOT add one. If the underlying `aec-audio-processing`
native binding ever turns out to NOT be safe for concurrent
far-end/near-end calls (undocumented upstream), that would need to be
addressed via a library-level fix or a lock added at that point with
real testing against real audio hardware — not speculatively here.

API-NAME SAFETY NOTE
---------------------
The published `aec-audio-processing` docs confirm: AudioProcessor(...),
set_stream_format(...), set_reverse_stream_format(...),
set_stream_delay(...), process_stream(...), has_voice(),
aec_enabled()/ns_enabled()/agc_enabled(). They do NOT explicitly document
the far-end/reverse-stream *processing* call name. Standard WebRTC APM
bindings universally call this `process_reverse_stream`, so that's tried
first — but `_resolve_reverse_method()` probes a short list of plausible
names and, if none exist, disables far-end feeding with a loud one-time
warning (never a silent failure) and prints `dir(ap)` so the real method
name can be read off directly and added to the candidate list.

PRODUCTION-AUDIT FIXES (this revision)
-----------------------------------------
1. The two "warn once, then suppress forever" flags (previously
   `_warned_no_reverse`, `_warned_process_error`) have been replaced
   with timestamp-based rate limiting (at most one warning per 60s per
   category). Previously, a single transient error would permanently
   silence ALL future error visibility for the rest of the process's
   lifetime, including genuinely new/different failures — meaning a
   persistent, ongoing problem (e.g. a driver issue causing every frame
   to fail) could degrade to fully silent passthrough with zero further
   log output. Rate limiting keeps noise down while ensuring an ongoing
   problem is never invisible forever.
2. `feed_far_end()`'s error log message no longer misleadingly labels
   every possible exception as being about "no reverse method" — the
   message is now generic and accurate for whatever actually failed.
3. Added a growth-guard on `_near_buf` (mirroring the guard that
   already existed on `_far_buf`), so a stall anywhere downstream can
   no longer let the near-end buffer grow unboundedly in memory.
4. Added `close()` for lifecycle symmetry with TextToSpeech/SpeechToText
   (both of which already expose an explicit close/shutdown). Safe to
   call multiple times; the underlying native AudioProcessor object
   doesn't require explicit teardown, but this gives callers one
   consistent shutdown pattern across all three audio-related classes
   and a clear place to add real cleanup if a future library version
   needs it.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

from config import Config

try:
    from aec_audio_processing import AudioProcessor as _WebRTCAudioProcessor

    _HAS_AEC_LIB = True
except (ImportError, OSError) as e:
    _WebRTCAudioProcessor = None
    _HAS_AEC_LIB = False
    print(
        "[AEC] 'aec-audio-processing' is unavailable — echo cancellation "
        "disabled, falling back to passthrough. Run: pip install aec-audio-processing. "
        f"({e})"
    )

# Candidate method names for pushing the far-end/reverse (reference)
# stream into the processor, tried in order. First match wins.
_REVERSE_METHOD_CANDIDATES = (
    "process_reverse_stream",
    "process_render_stream",
    "process_reverse",
    "reverse_stream",
    "analyze_reverse_stream",
)

_BYTES_PER_SAMPLE = 2  # int16 PCM

# How often (seconds) a given category of recurring error is allowed to
# print again, once it has already printed once. Keeps a persistent,
# ongoing problem visible in logs without spamming on every single frame.
_ERROR_LOG_RATE_LIMIT_S = 60.0

# Safety valve: if the near-end buffer somehow grows beyond this many
# frames' worth of bytes (e.g. downstream processing stalls for any
# reason), drop the backlog rather than growing memory unboundedly.
# Mirrors the existing _far_buf safety valve below.
_NEAR_BUF_MAX_FRAMES = 50


def _resample_linear(pcm_i16: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """
    Lightweight dependency-free resampler (linear interpolation) used only
    for feeding the AEC far-end reference — this does NOT touch anything
    that gets played back or transcribed, so perfect audio fidelity isn't
    required here, only correct timing/shape so the adaptive filter can
    correlate it against the echoed copy in the mic signal.
    """
    if src_rate == dst_rate or len(pcm_i16) == 0:
        return pcm_i16
    src_n = len(pcm_i16)
    dst_n = max(1, int(round(src_n * (dst_rate / float(src_rate)))))
    src_x = np.linspace(0.0, 1.0, num=src_n, endpoint=False)
    dst_x = np.linspace(0.0, 1.0, num=dst_n, endpoint=False)
    out = np.interp(dst_x, src_x, pcm_i16.astype(np.float32))
    return np.clip(out, -32768.0, 32767.0).astype(np.int16)


class AECProcessor:
    """
    Thread-safe wrapper around WebRTC APM providing:
      - feed_far_end(pcm_bytes, source_sample_rate): push whatever audio is
        ACTUALLY being sent to the speaker right now (called from TTS's
        real-time output callback).
      - process_near_end(pcm_bytes) -> bytes: run raw mic audio through
        AEC/NS and get back echo-cancelled audio (called from STT's
        real-time input callback).

    Both sides internally re-frame arbitrary-length chunks into the fixed
    10ms frames WebRTC APM requires, buffering any remainder across calls.
    If the underlying library is unavailable or fails to initialize, both
    methods degrade to safe passthrough (near-end unmodified, far-end
    silently ignored) rather than raising into the audio callback thread.
    """

    def __init__(
        self,
        sample_rate: Optional[int] = None,
        channels: int = 1,
        stream_delay_ms: Optional[int] = None,
        enable_ns: Optional[bool] = None,
        enable_agc: Optional[bool] = None,
        enable_vad: Optional[bool] = None,
    ) -> None:
        self.enabled = bool(getattr(Config, "AEC_ENABLED", True)) and _HAS_AEC_LIB

        self.sample_rate = int(sample_rate or getattr(Config, "AEC_SAMPLE_RATE", 16000))
        self.channels = channels
        self._frame_samples = max(
            1, int(self.sample_rate * 0.01)
        )  # 10ms, per WebRTC APM
        self._frame_bytes = self._frame_samples * _BYTES_PER_SAMPLE * self.channels

        self._ap = None
        self._reverse_method_name: Optional[str] = None

        self._near_buf = bytearray()
        self._near_lock = threading.Lock()
        self._far_buf = bytearray()
        self._far_lock = threading.Lock()

        self._closed = False
        self._close_lock = threading.Lock()

        # Rate-limited error logging state (see PRODUCTION-AUDIT FIXES
        # note #1 at the top of this file). Each category tracks the
        # monotonic timestamp it last printed at. Starts as None (not
        # 0.0) so the FIRST error in each category always logs — using
        # 0.0 as a sentinel would incorrectly suppress the first log if
        # the process had been running for under _ERROR_LOG_RATE_LIMIT_S
        # seconds when the error occurred (time.monotonic()'s epoch is
        # arbitrary and can itself be a small number shortly after
        # process start).
        self._log_state_lock = threading.Lock()
        self._last_far_end_error_log: Optional[float] = None
        self._last_process_error_log: Optional[float] = None

        if not self.enabled:
            print(
                "[AEC] Disabled (either AEC_ENABLED=False or library missing) — "
                "mic audio will pass through unmodified. Echo-hearing / "
                "self-triggering may reoccur without it."
            )
            return

        try:
            self._ap = _WebRTCAudioProcessor(
                enable_aec=True,
                enable_ns=bool(
                    getattr(Config, "AEC_ENABLE_NS", True)
                    if enable_ns is None
                    else enable_ns
                ),
                enable_agc=bool(
                    getattr(Config, "AEC_ENABLE_AGC", False)
                    if enable_agc is None
                    else enable_agc
                ),
                enable_vad=bool(
                    getattr(Config, "AEC_ENABLE_VAD", False)
                    if enable_vad is None
                    else enable_vad
                ),
            )
            self._ap.set_stream_format(
                sample_rate_in=self.sample_rate,
                channel_count_in=self.channels,
                sample_rate_out=self.sample_rate,
                channel_count_out=self.channels,
            )
            self._ap.set_reverse_stream_format(self.sample_rate, self.channels)

            delay_ms = int(
                stream_delay_ms
                if stream_delay_ms is not None
                else getattr(Config, "AEC_STREAM_DELAY_MS", 80)
            )
            self._ap.set_stream_delay(delay_ms)

            self._reverse_method_name = self._resolve_reverse_method()

            print(
                f"[AEC] Ready — rate={self.sample_rate}Hz | delay={delay_ms}ms | "
                f"aec={getattr(self._ap, 'aec_enabled', lambda: '?')()} | "
                f"ns={getattr(self._ap, 'ns_enabled', lambda: '?')()} | "
                f"agc={getattr(self._ap, 'agc_enabled', lambda: '?')()} | "
                f"far-end method='{self._reverse_method_name or 'NOT FOUND — see warning above'}'"
            )
        except Exception as e:
            print(f"[AEC Error] Failed to initialize WebRTC AudioProcessor: {e}")
            print("[AEC] Falling back to passthrough (no echo cancellation).")
            self._ap = None
            self.enabled = False

    # ── Method discovery (see API-NAME SAFETY NOTE at top of file) ────────

    def _resolve_reverse_method(self) -> Optional[str]:
        for name in _REVERSE_METHOD_CANDIDATES:
            if hasattr(self._ap, name):
                return name
        available = [m for m in dir(self._ap) if not m.startswith("_")]
        print(
            "[AEC WARNING] Could not find a far-end/reverse-stream method on "
            f"AudioProcessor among candidates {_REVERSE_METHOD_CANDIDATES}. "
            "Echo cancellation will NOT work correctly (near-end processing "
            "will still run, but with no reference signal to cancel against "
            "— equivalent to AEC being off). Actual available methods on "
            f"the installed AudioProcessor: {available}. "
            "Update _REVERSE_METHOD_CANDIDATES in sara/audio/aec.py with the "
            "correct name from that list."
        )
        return None

    # ── Rate-limited error logging helper ──────────────────────────────

    def _should_log_now(self, category: str) -> bool:
        """
        Returns True at most once per _ERROR_LOG_RATE_LIMIT_S seconds for
        a given category ("far_end" or "process"), so a recurring error
        stays visible in logs over time instead of either spamming every
        frame or (the previous behavior) going silent forever after the
        very first occurrence. The very first call for a category always
        returns True (last-log-time starts as None, not a timestamp).
        """
        now = time.monotonic()
        with self._log_state_lock:
            if category == "far_end":
                last = self._last_far_end_error_log
                if last is None or (now - last) >= _ERROR_LOG_RATE_LIMIT_S:
                    self._last_far_end_error_log = now
                    return True
                return False
            if category == "process":
                last = self._last_process_error_log
                if last is None or (now - last) >= _ERROR_LOG_RATE_LIMIT_S:
                    self._last_process_error_log = now
                    return True
                return False
        return False

    # ── Far-end (reference / reverse stream) ───────────────────────────────

    def feed_far_end(self, pcm_i16: np.ndarray, source_sample_rate: int) -> None:
        """
        Push whatever audio is actually being sent to the speaker RIGHT NOW.
        Called from TTS's real-time output callback — must stay fast and
        must never raise (audio callback thread).
        """
        if not self.enabled or self._ap is None or pcm_i16 is None or len(pcm_i16) == 0:
            return
        try:
            if source_sample_rate != self.sample_rate:
                pcm_i16 = _resample_linear(
                    pcm_i16, source_sample_rate, self.sample_rate
                )
            pcm_bytes = pcm_i16.astype(np.int16, copy=False).tobytes()

            with self._far_lock:
                self._far_buf.extend(pcm_bytes)
                if self._reverse_method_name is None:
                    # Nothing we can feed it to — drop buffered bytes so
                    # memory doesn't grow unboundedly while still silent.
                    if len(self._far_buf) > self._frame_bytes * 50:
                        self._far_buf.clear()
                    return
                method = getattr(self._ap, self._reverse_method_name)
                while len(self._far_buf) >= self._frame_bytes:
                    frame = bytes(self._far_buf[: self._frame_bytes])
                    del self._far_buf[: self._frame_bytes]
                    method(frame)
        except Exception as e:
            # FIX: this used to be a "print once, then silence forever"
            # flag mislabeled as being specifically about a missing
            # reverse method. It's now rate-limited (see
            # _should_log_now) and accurately describes that SOME error
            # occurred in feed_far_end, without claiming a specific cause.
            if self._should_log_now("far_end"):
                print(
                    f"[AEC] feed_far_end error (further identical errors "
                    f"suppressed for {_ERROR_LOG_RATE_LIMIT_S:.0f}s): {e}"
                )

    # ── Near-end (microphone / forward stream) ─────────────────────────────

    def process_near_end(self, pcm_bytes: bytes) -> bytes:
        """
        Run raw mic audio through AEC + NS. Returns echo-cancelled audio.
        Called from STT's real-time input callback — must stay fast and
        must never raise (audio callback thread). On any failure this
        returns the ORIGINAL audio unmodified (fail-safe passthrough) so a
        transient processing error never silences the mic entirely.
        """
        if not self.enabled or self._ap is None or not pcm_bytes:
            return pcm_bytes

        try:
            with self._near_lock:
                self._near_buf.extend(pcm_bytes)

                # FIX: safety valve mirroring the one _far_buf already had —
                # if something downstream stalls, this bounds memory growth
                # instead of letting _near_buf grow without limit.
                if len(self._near_buf) > self._frame_bytes * _NEAR_BUF_MAX_FRAMES:
                    if self._should_log_now("process"):
                        print(
                            f"[AEC WARNING] near-end buffer exceeded "
                            f"{_NEAR_BUF_MAX_FRAMES} frames worth of audio "
                            f"and was reset — processing may be falling "
                            f"behind real time."
                        )
                    self._near_buf.clear()
                    return pcm_bytes

                out = bytearray()
                while len(self._near_buf) >= self._frame_bytes:
                    frame = bytes(self._near_buf[: self._frame_bytes])
                    del self._near_buf[: self._frame_bytes]
                    try:
                        processed = self._ap.process_stream(frame)
                        out.extend(processed if processed else frame)
                    except Exception as e:
                        if self._should_log_now("process"):
                            print(
                                f"[AEC] process_stream error (further identical "
                                f"errors suppressed for {_ERROR_LOG_RATE_LIMIT_S:.0f}s, "
                                f"passing raw audio through): {e}"
                            )
                        out.extend(frame)
                return bytes(out)
        except Exception as e:
            print(f"[AEC] process_near_end fatal error, disabling AEC for safety: {e}")
            self.enabled = False
            return pcm_bytes

    def has_voice(self) -> bool:
        """Exposes WebRTC APM's own VAD result (only meaningful if
        AEC_ENABLE_VAD=True). Not currently consumed by stt.py, which uses
        its own webrtcvad-based endpointing — available for future use."""
        if not self.enabled or self._ap is None:
            return False
        try:
            return bool(self._ap.has_voice())
        except Exception:
            return False

    def reset_stream_delay(self, delay_ms: int) -> None:
        """Allows runtime re-tuning of AEC_STREAM_DELAY_MS without a
        restart, e.g. from a future debug/settings UI control."""
        if not self.enabled or self._ap is None:
            return
        try:
            self._ap.set_stream_delay(max(0, min(500, int(delay_ms))))
        except Exception as e:
            print(f"[AEC] reset_stream_delay failed: {e}")

    def close(self) -> None:
        """
        Releases any resources held by this processor. Safe to call
        multiple times (idempotent). The underlying native
        AudioProcessor object does not currently require explicit
        teardown, but this gives callers (TextToSpeech/SpeechToText
        already have their own close()/shutdown()) one consistent
        lifecycle pattern across all three audio-related classes, and a
        single place to add real cleanup if a future library version
        needs it.
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self.enabled = False
            self._ap = None
