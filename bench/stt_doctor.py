"""
Stage-by-stage microphone-to-transcript diagnostic.

    python -m bench.stt_doctor                 # full run, 4s of capture
    python -m bench.stt_doctor --seconds 6
    python -m bench.stt_doctor --device 3      # pick an input device by index
    python -m bench.stt_doctor --no-model      # skip model load (fast)
    python -m bench.stt_doctor --no-listen     # skip the live listen() test
    python -m bench.stt_doctor --save-wav mic.wav   # OPT-IN local recording

WHY THIS EXISTS
    "SARA is not transcribing" can fail at eight different places, and the
    symptom looks identical from outside for at least three of them: the GUI
    level meter is fed by a callback that is INDEPENDENT of the speech gate,
    so levels can move perfectly while nothing is ever captured. This walks
    each stage in order and names the first one that fails.

    Stages 1-6 prove the microphone and the model work. They deliberately
    bypass _collect_speech(), so they do NOT prove the application works:
    stage 7 calls the real listen() the app calls, and stage 8 pushes the
    resulting transcript through the real intent router. A healthy 1-6 with
    a failing 7 is the signature of an endpointing/gate problem rather than
    a microphone or model problem.

PRIVACY
    Entirely local. No network calls of any kind. Audio is held in memory and
    discarded unless you pass --save-wav, which writes to a path you choose.
    Transcripts are printed to your console only. Nothing is uploaded and
    nothing is written to SARA's databases.

NO NEW DEPENDENCIES
    Uses only sounddevice / numpy / faster-whisper, which SARA already
    requires, plus stdlib wave.
"""
from __future__ import annotations

import argparse
import os
import time
import wave
from typing import Optional

# ── CUDA DLL registration -- MUST happen before ctranslate2 is imported ──
# main.py does this at its very top, with the comment "must run BEFORE
# anything imports onnxruntime / ctranslate2": the pip-installed nvidia-*-cu12
# wheels ship their DLLs in site-packages/nvidia/<pkg>/bin and Windows will
# not find them on its own.
#
# This module is run as `python -m bench.stt_doctor`, which never executes
# main.py -- so without this the GPU path fails with "Library
# cublas64_12.dll is not found", and the doctor reports a CUDA problem that
# SARA itself does not have. A diagnostic that does not reproduce the real
# runtime environment is worse than no diagnostic.
#
# Deliberately duplicated rather than factored out of main.py: this is a
# P0 diagnostic and main.py is the boot path, which should not be edited to
# support a tool. main.py remains the canonical copy.
_cuda_dll_dir_handles = []  # kept alive: if GC'd, the directories are removed
try:
    import nvidia  # namespace package

    for _nv_root in list(getattr(nvidia, "__path__", [])):
        if not os.path.isdir(_nv_root):
            continue
        for _pkg in sorted(os.listdir(_nv_root)):
            _dll_dir = os.path.join(_nv_root, _pkg, "bin")
            if os.path.isdir(_dll_dir):
                try:
                    _cuda_dll_dir_handles.append(os.add_dll_directory(_dll_dir))
                except (OSError, AttributeError):
                    pass
                os.environ["PATH"] = _dll_dir + os.pathsep + os.environ.get("PATH", "")
except ImportError:
    pass

_OK = "PASS"
_NO = "FAIL"
_SKIP = "SKIP"
_WARN = "WARN"

_failures: list[str] = []
_warnings: list[str] = []


def _stage(name: str, status: str, detail: str = "") -> None:
    print(f"  [{status:4}] {name}" + (f" -- {detail}" if detail else ""))
    if status == _NO:
        _failures.append(name)
    elif status == _WARN:
        _warnings.append(name)


def stage_imports() -> dict:
    print("\n1. OPTIONAL DEPENDENCIES")
    found = {}
    for mod, why in (
        ("faster_whisper", "transcription -- without it nothing can transcribe"),
        ("sounddevice", "primary mic backend"),
        ("pyaudio", "fallback mic backend"),
        ("webrtcvad", "voice activity detection"),
        ("openwakeword", "wake word"),
        ("numpy", "audio maths"),
    ):
        try:
            __import__(mod)
            found[mod] = True
            _stage(mod, _OK)
        except Exception as exc:
            found[mod] = False
            critical = mod in ("faster_whisper", "numpy") or (
                mod == "sounddevice" and not found.get("pyaudio")
            )
            _stage(mod, _NO if critical else _WARN, f"{type(exc).__name__}: {exc} ({why})")

    # The documented VAD killer: webrtcvad imports pkg_resources at runtime,
    # which setuptools >= 82 removed. requirements.txt pins setuptools<82 for
    # exactly this reason and calls it "silently disables VAD".
    try:
        import setuptools

        ver = getattr(setuptools, "__version__", "?")
        major = int(str(ver).split(".")[0])
        if major >= 82 and not found.get("webrtcvad"):
            _stage(
                "setuptools<82 pin",
                _NO,
                f"setuptools {ver} removed pkg_resources, which is why "
                f"webrtcvad failed to import. Fix: pip install 'setuptools<82'",
            )
        elif major >= 82:
            _stage("setuptools<82 pin", _WARN, f"setuptools {ver} (>=82) but webrtcvad still imports")
        else:
            _stage("setuptools<82 pin", _OK, f"setuptools {ver}")
    except Exception as exc:
        _stage("setuptools check", _SKIP, str(exc))

    # D42: AEC decides two per-turn costs in the real application -- the mic
    # settle (0.3s with AEC, Config.STT_SETTLE_MIN_GAP_S 1.3s without) and
    # whether the wake ack blocks listen(). This diagnostic constructs
    # SpeechToText() with aec=None, so its own "[STT] Ready" line ALWAYS
    # prints AEC=✗ and says nothing about the real app. Checking the wheel
    # here is what distinguishes "AEC is off" from "the doctor doesn't wire
    # it", which is a distinction that cost real debugging time.
    try:
        import importlib

        importlib.import_module("aec_audio_processing")
        _stage("aec-audio-processing", _OK,
               "installed -- the real app gets echo cancellation, a 0.3s mic "
               "settle and a non-blocking wake ack (this diagnostic always "
               "shows AEC=X below; it passes aec=None on purpose)")
    except Exception as exc:
        _stage(
            "aec-audio-processing", _WARN,
            f"missing ({type(exc).__name__}), so every turn pays the full "
            f"mic settle and waits for the wake ack to finish playing. "
            f"Fix: pip install aec-audio-processing",
        )

    # D34/D35: offline mode is frozen into huggingface_hub when it is
    # imported, so if it is truthy at process start, no model that is not
    # already cached can ever be fetched -- and the only outward symptom is
    # empty transcripts.
    import os

    offline_vars = {
        k: os.environ.get(k)
        for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY")
        if os.environ.get(k) is not None
    }
    blocking = [
        k for k, v in offline_vars.items()
        if k != "HF_HUB_DISABLE_TELEMETRY" and str(v).strip().lower() not in ("", "0", "false")
    ]
    if blocking:
        _stage(
            "hub reachable", _NO,
            f"{', '.join(f'{k}={offline_vars[k]}' for k in blocking)} -- offline "
            f"mode is on, so a model that is not already cached CANNOT be "
            f"downloaded. Unset it (see the verdict below)",
        )
    else:
        _stage("hub reachable", _OK, "no offline env var set" if not offline_vars
               else f"only {', '.join(offline_vars)} set (harmless)")

    # Is the configured model actually in the cache?
    try:
        from config import Config
        from faster_whisper.utils import download_model

        size = getattr(Config, "WHISPER_MODEL_SIZE", "medium")
        try:
            path = download_model(size, local_files_only=True)
            _stage(f"model '{size}' cached", _OK, str(path))
            found["_model_cached"] = True
        except Exception:
            _stage(
                f"model '{size}' cached", _NO,
                "not in the local cache. SARA cannot transcribe without it; "
                "see the verdict below for the one-time download command",
            )
            found["_model_cached"] = False
    except Exception as exc:
        _stage("model cache check", _SKIP, f"{type(exc).__name__}: {exc}")
    return found


def stage_config() -> None:
    """
    Stage 1b: the switches that decide capture, endpointing and preview
    speed, in one place.

    Several of them have been found lying at some point in this audit --
    declared nowhere and silently fixed at a default (D31, D40), or
    complete but switched off (D32), or overridden by a saved GUI
    preference the log never mentioned (D58). Printing the EFFECTIVE values
    is cheaper than discovering each one from behaviour.
    """
    print("\n1b. VOICE CONFIGURATION (effective values)")
    try:
        from config import Config
    except Exception as exc:
        _stage("read config", _SKIP, str(exc))
        return

    def g(name, default=None):
        return getattr(Config, name, default)

    thr = float(g("BARGE_IN_ENERGY_THRESHOLD", 500))
    print(f"         model                      : {g('WHISPER_MODEL_SIZE')}")
    print(f"         BARGE_IN_ENERGY_THRESHOLD  : {thr:.0f}"
          f"   (start gate {thr * 0.35:.0f}, silence gate {thr * 0.30:.0f})")
    print(f"         preview model on GPU       : {g('WAKE_MODEL_USE_GPU')}")
    print(f"         adaptive endpoint          : {g('VOICE_ENDPOINT_ADAPTIVE')}"
          f"   (min {g('ENDPOINT_MIN_MS')}ms, max {g('ENDPOINT_MAX_MS')}ms)")
    print(f"         streaming first chunk (v2) : {g('VOICE_CHUNKER_V2')}")
    print(f"         noise-relative gate        : {g('VOICE_GATE_NOISE_RELATIVE')}"
          f"   (x{g('VOICE_GATE_NOISE_MULTIPLIER')}, "
          f"clamp {g('VOICE_GATE_MIN_RMS'):.0f}-{g('VOICE_GATE_MAX_RMS'):.0f})")
    print(f"         startup calibration after  : {g('STT_RECALIB_FIRST_FAST_S')}s")
    print(f"         telemetry                  : {g('TELEMETRY_ENABLED')}")

    if g("VOICE_ENDPOINT_ADAPTIVE") is not True:
        _stage(
            "adaptive endpointing", _WARN,
            "off -- every command waits a fixed silence_limit instead of "
            "ending early on a recognised one (audit D32/D41)",
        )
    else:
        _stage("adaptive endpointing", _OK, "on")

    if g("VOICE_GATE_NOISE_RELATIVE") is True:
        _stage(
            "noise-relative gate", _OK,
            "ON -- the gate follows your measured room noise, so "
            "BARGE_IN_ENERGY_THRESHOLD above is only the starting value",
        )
    else:
        _stage(
            "noise-relative gate", _WARN,
            "off, so the gate is fixed at the value above. Turn it on with "
            "VOICE_GATE_NOISE_RELATIVE=True in .env if capture is "
            "unreliable in a quiet or noisy room (audit D47/D49)",
        )

    # D58: a saved GUI slider overrides the .env threshold, and only the
    # real app applies it -- this diagnostic never does. Say so, because
    # the number printed above is NOT necessarily what the app will use.
    try:
        import sqlite3
        from sara.core.telemetry.store import db_path

        pref = db_path().parent / "sara_data.db"
        if pref.exists():
            con = sqlite3.connect(f"file:{pref}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT value FROM preferences WHERE key = ?",
                    ("mic_sensitivity",),
                ).fetchone()
            finally:
                con.close()
            if row is not None and str(row[0]).strip() != "":
                sens = max(0, min(100, int(row[0])))
                effective = max(100, 1000 - (sens * 9))
                _stage(
                    "mic sensitivity override", _WARN,
                    f"a saved GUI slider at {sens}% will OVERRIDE the "
                    f"threshold above to {effective:.0f} (start gate "
                    f"{effective * 0.35:.0f}) when you run main.py. Change "
                    f"it with the GUI slider, not .env (audit D58)",
                )
            else:
                _stage("mic sensitivity override", _OK,
                       "none saved -- .env decides")
    except Exception as exc:
        _stage("mic sensitivity override", _SKIP, f"{type(exc).__name__}: {exc}")


def stage_devices(device: Optional[int]) -> Optional[int]:
    print("\n2. INPUT DEVICES")
    try:
        import sounddevice as sd
    except Exception as exc:
        _stage("enumerate", _SKIP, f"sounddevice unavailable: {exc}")
        return device
    try:
        devices = sd.query_devices()
        default_in = sd.default.device[0]
        any_input = False
        for idx, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) > 0:
                any_input = True
                mark = " <- default" if idx == default_in else ""
                print(
                    f"         [{idx}] {dev['name']} "
                    f"(ch={dev['max_input_channels']}, "
                    f"sr={int(dev.get('default_samplerate') or 0)}){mark}"
                )
        if not any_input:
            _stage("input device present", _NO, "no device reports input channels")
            return device
        _stage("input device present", _OK, f"default index {default_in}")
        return device if device is not None else default_in
    except Exception as exc:
        _stage("enumerate", _NO, f"{type(exc).__name__}: {exc}")
        return device


def stage_capture(seconds: float, device: Optional[int]):
    """Raw capture at SARA's own format. Returns int16 bytes or None."""
    print(f"\n3. CAPTURE ({seconds:.0f}s at 16000 Hz, int16, mono)")
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:
        _stage("open stream", _SKIP, str(exc))
        return None

    frames: list[bytes] = []
    callbacks = {"n": 0}

    def _cb(indata, nframes, time_info, status):  # noqa: ARG001
        callbacks["n"] += 1
        frames.append(bytes(indata))

    try:
        stream = sd.RawInputStream(
            samplerate=16000, blocksize=512, dtype="int16",
            channels=1, callback=_cb, device=device,
        )
    except Exception as exc:
        _stage("open stream", _NO, f"{type(exc).__name__}: {exc}")
        return None

    print("         SPEAK NOW -- say: \"open chrome\"")
    try:
        stream.start()
        time.sleep(seconds)
        stream.stop()
        stream.close()
    except Exception as exc:
        _stage("run stream", _NO, f"{type(exc).__name__}: {exc}")
        return None

    if callbacks["n"] == 0:
        _stage("callback fired", _NO, "the audio callback never ran -- no data from the device")
        return None
    _stage("callback fired", _OK, f"{callbacks['n']} callbacks")

    audio = b"".join(frames)
    arr = np.frombuffer(audio, dtype=np.int16)
    if arr.size == 0:
        _stage("samples received", _NO, "0 samples")
        return None

    peak = int(np.max(np.abs(arr)))
    # Per-block RMS, matching how SARA gates (it tests one chunk at a time).
    blocks = [arr[i:i + 512] for i in range(0, arr.size - 512, 512)]
    rms = [float(np.sqrt(np.mean(b.astype(np.float64) ** 2))) for b in blocks if b.size]
    loudest = max(rms) if rms else 0.0
    median = sorted(rms)[len(rms) // 2] if rms else 0.0

    _stage("samples received", _OK, f"{arr.size} samples ({arr.size / 16000:.2f}s)")
    print(f"         peak amplitude : {peak} / 32767")
    print(f"         loudest block  : RMS {loudest:.0f}")
    print(f"         median block   : RMS {median:.0f}  (room noise)")

    if peak == 0:
        _stage("audio is non-silent", _NO, "every sample is zero -- muted device or wrong input")
        return None
    if peak < 300:
        _stage("audio level usable", _WARN,
               f"peak {peak} is very quiet; raise the mic level in Windows Sound settings")
    elif peak > 32000:
        _stage("audio level usable", _WARN, f"peak {peak} is clipping; lower the mic level")
    else:
        _stage("audio level usable", _OK, f"peak {peak}")
    return audio, loudest, median


def stage_gate(loudest: float, median: float) -> None:
    """
    The condition SARA actually uses to START collecting speech, from
    engine._collect_speech:

        self._vad.is_speech(chunk) or _rms(chunk) > thr * 0.35

    With webrtcvad missing, is_speech() returns False unconditionally, so the
    energy term is the ONLY thing that can start a capture.
    """
    print("\n4. SPEECH GATE (the stage that looks like a working mic but isn't)")
    try:
        from config import Config

        thr = float(getattr(Config, "BARGE_IN_ENERGY_THRESHOLD", 500))
    except Exception as exc:
        _stage("read threshold", _SKIP, str(exc))
        return
    needed = thr * 0.35
    print(f"         energy_threshold (start value) : {thr:.0f}")
    print(f"         RMS needed to start capture    : {needed:.0f}  (threshold x 0.35)")
    print(f"         loudest block you produced     : {loudest:.0f}")
    if loudest > needed:
        headroom = loudest / needed if needed else 0
        _stage("speech would be detected", _OK, f"{headroom:.1f}x above the gate")
    else:
        _stage(
            "speech would be detected", _NO,
            f"your loudest block ({loudest:.0f}) is BELOW the gate ({needed:.0f}). "
            f"SARA will never start capturing. Lower it with "
            f"BARGE_IN_ENERGY_THRESHOLD in .env, or raise the Windows mic level",
        )
    # The END of an utterance is decided by a DIFFERENT test, and this is
    # the one that bites: _collect_speech uses
    #     is_voice = vad.is_speech(chunk) or (avg_energy > thr * 0.3)
    # so if room noise sits above thr*0.3, every silent chunk still counts as
    # voice, silence never accumulates, the endpoint never fires, and the
    # capture runs to max_duration (12s) on every single command.
    cont = thr * 0.3
    print(f"         RMS below which speech ENDS      : {cont:.0f}  (threshold x 0.30)")
    print(f"         your room noise                  : {median:.0f}")
    if median >= cont:
        _stage(
            "end of speech can be detected", _NO,
            f"room noise (RMS {median:.0f}) is at or above the silence gate "
            f"({cont:.0f}), so SARA will never notice you stopped talking and "
            f"every command will run the full 12s capture. Raise "
            f"BARGE_IN_ENERGY_THRESHOLD, or reduce room/mic noise",
        )
    elif median > cont * 0.7:
        _stage(
            "end of speech can be detected", _WARN,
            f"room noise (RMS {median:.0f}) is close to the silence gate "
            f"({cont:.0f}); end-of-speech detection will be unreliable",
        )
    else:
        _stage("end of speech can be detected", _OK,
               f"noise {median:.0f} is {cont / max(median, 1):.1f}x below the gate")

    if median > thr * 0.5:
        _stage(
            "room noise vs auto-recalibration", _WARN,
            f"room noise (RMS {median:.0f}) is above half the threshold, which "
            f"also blocks SARA's auto-recalibration from lowering it",
        )


def stage_vad(audio: bytes) -> None:
    print("\n5. VOICE ACTIVITY DETECTION")
    try:
        import numpy as np

        from sara.audio.stt.buffers import _VADFilter
        from sara.audio.stt.engine import _HAS_VAD
    except Exception as exc:
        _stage("load VAD", _SKIP, str(exc))
        return
    if not _HAS_VAD:
        _stage(
            "webrtcvad active", _WARN,
            "VAD unavailable, so is_speech() always returns False. Capture "
            "still works via the energy fallback, but barge-in "
            "(interrupting SARA) does NOT -- it has no energy fallback",
        )
        return
    vad = _VADFilter(16000)
    chunks = [audio[i:i + 1024] for i in range(0, len(audio) - 1024, 1024)]
    voiced = sum(1 for c in chunks if vad.is_speech(c))
    if voiced:
        _stage("VAD found speech", _OK, f"{voiced}/{len(chunks)} frames voiced")
    else:
        _stage("VAD found speech", _WARN,
               f"0/{len(chunks)} frames voiced -- energy fallback must carry capture")

    # D45: cross-check VAD against the measured energy of the SAME chunks.
    # This is the check that would have identified D45 immediately: on the
    # target machine webrtcvad voiced 112 of 123 chunks of a clip whose
    # median was RMS 29, i.e. it reported near-silence as speech. Counting
    # voiced frames alone looks like a healthy PASS; only comparing it with
    # the energy of those same chunks shows the detector is unreliable.
    #
    # Reported, not acted on: SARA no longer lets VAD open or hold a capture
    # window (D39, D45), so an unreliable VAD here is informational for
    # barge-in rather than a capture failure.
    def _rms_of(buf: bytes) -> float:
        a = np.frombuffer(buf, dtype=np.int16)
        return float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) if a.size else 0.0

    quiet = [c for c in chunks if _rms_of(c) < 100]
    if quiet:
        quiet_voiced = sum(1 for c in quiet if vad.is_speech(c))
        pct = 100.0 * quiet_voiced / len(quiet)
        print(f"         of {len(quiet)} near-silent chunks (RMS < 100), "
              f"VAD called {quiet_voiced} speech ({pct:.0f}%)")
        if pct >= 50.0:
            _stage(
                "VAD agrees with measured energy", _WARN,
                f"VAD labels {pct:.0f}% of your near-silent audio as speech, "
                f"so it is not a usable speech detector on this microphone. "
                f"Capture does not rely on it (energy decides both gates), "
                f"but barge-in -- interrupting SARA mid-reply -- does, so "
                f"expect it to trigger on room noise",
            )
        else:
            _stage("VAD agrees with measured energy", _OK,
                   f"only {pct:.0f}% of near-silent chunks called speech")


def stage_transcribe(audio: bytes) -> None:
    print("\n6. TRANSCRIPTION (SARA's own pipeline, on the audio just captured)")
    try:
        from sara.audio.stt.engine import SpeechToText
    except Exception as exc:
        _stage("import engine", _NO, f"{type(exc).__name__}: {exc}")
        return None
    try:
        started = time.monotonic()
        ears = SpeechToText()
        load_ms = (time.monotonic() - started) * 1000.0
    except Exception as exc:
        _stage("construct SpeechToText", _NO, f"{type(exc).__name__}: {exc}")
        return None
    _stage("construct SpeechToText", _OK, f"{load_ms:.0f}ms (includes model load + warm-up)")

    if ears._whisper_model is None:
        _stage("model loaded", _NO, "faster-whisper model is None -- check the model files/size")
        ears.close()
        return None
    _stage("model loaded", _OK, str(getattr(ears, "_model_size", "")) or "ok")

    try:
        started = time.monotonic()
        result = ears._transcribe(audio)
        infer_ms = (time.monotonic() - started) * 1000.0
    except Exception as exc:
        _stage("inference", _NO, f"{type(exc).__name__}: {exc}")
        ears.close()
        return None

    text = str(result).strip()
    conf = getattr(result, "confidence", 0.0)
    if text:
        _stage("transcript produced", _OK, f"{infer_ms:.0f}ms, confidence {conf:.2f}")
        print(f"         TRANSCRIPT: {text!r}")
        return ears
    else:
        _stage(
            "transcript produced", _NO,
            f"empty transcript after {infer_ms:.0f}ms. The audio reached the "
            f"model but produced nothing -- check mic level, and try "
            f"STT_NO_SPEECH_THRESHOLD (currently filters segments)",
        )
    try:
        ears.close()
    except Exception:
        pass
    return None


def stage_engine_mic(ears, seconds: float = 3.0) -> None:
    """
    Stage 7a: what the ENGINE's own pipeline sees.

    Stage 3 opens its own sounddevice stream and measures that. Stage 7
    calls listen(), whose gate reads chunks off the engine's ring buffer --
    a different stream, filled through _ingest_processed_chunk. When those
    two disagree (stage 4 says "speech would be detected", stage 7 times
    out), nothing in the diagnostic could say which of three things was
    true: the engine's stream is not feeding, the chunks arrive quieter
    than stage 3 measured, or the user simply spoke more softly the second
    time.

    This reads the engine's ring directly and reports the gate's own input,
    so a stage-7 timeout is never ambiguous again.
    """
    print("\n7a. ENGINE MIC PROBE (what the gate itself receives)")
    try:
        import numpy as np
    except Exception as exc:
        _stage("probe engine ring", _SKIP, str(exc))
        return

    def _rms_of(buf: bytes) -> float:
        a = np.frombuffer(buf, dtype=np.int16)
        return float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) if a.size else 0.0

    try:
        thr = float(ears.energy_threshold)
    except Exception:
        thr = 600.0
    gate = thr * 0.35

    # listen() clears the ring at the start of every session, so draining it
    # here costs that session nothing.
    try:
        ears._ring.get_all(clear=True)
    except Exception as exc:
        _stage("probe engine ring", _SKIP, f"{type(exc).__name__}: {exc}")
        return

    print('         SPEAK NOW -- say "open chrome" at your normal volume')
    collected: list[bytes] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            ears._ring.wait(timeout=0.05)
            collected.extend(ears._ring.get_all(clear=True))
        except Exception:
            break

    if not collected:
        _stage(
            "engine stream is feeding", _NO,
            "the engine's ring buffer received NOTHING in "
            f"{seconds:.0f}s. Stage 3 captured audio on its own stream, so "
            "the device works -- it is the engine's stream that is not "
            "delivering. listen() can only ever time out in this state",
        )
        return
    _stage("engine stream is feeding", _OK, f"{len(collected)} chunks")

    rms = [_rms_of(c) for c in collected]
    loudest = max(rms)
    median = sorted(rms)[len(rms) // 2]
    over = sum(1 for r in rms if r > gate)
    print(f"         chunks seen by the gate : {len(rms)}")
    print(f"         loudest chunk           : RMS {loudest:.0f}")
    print(f"         median chunk            : RMS {median:.0f}")
    print(f"         gate to START a capture : {gate:.0f}  "
          f"(energy_threshold {thr:.0f} x 0.35)")
    print(f"         chunks above that gate  : {over}")

    if over == 0:
        _stage(
            "your speech clears the start gate", _NO,
            f"not one chunk reached {gate:.0f} -- loudest was {loudest:.0f}. "
            f"listen() WILL time out. Either speak louder/closer, raise the "
            f"Windows mic level, or lower BARGE_IN_ENERGY_THRESHOLD "
            f"(currently {thr:.0f}) in .env",
        )
    elif over < 3:
        _stage(
            "your speech clears the start gate", _WARN,
            f"only {over} chunk(s) cleared {gate:.0f} (loudest {loudest:.0f}) "
            f"-- that is a thin margin, so capture will start unreliably",
        )
    else:
        _stage("your speech clears the start gate", _OK,
               f"{over} chunks over {gate:.0f}, loudest {loudest:.0f} "
               f"({loudest / gate:.1f}x)")


def stage_listen(ears) -> Optional[str]:
    """
    Stage 7: the REAL listen() path -- the one the application calls.

    Stage 6 fed a pre-captured buffer straight to _transcribe(), which skips
    _collect_speech() entirely: the energy gate, the rolling-average
    continuation test, silence counting and the endpoint decision. Those are
    the stages between a working microphone and a working assistant, and
    they are exactly where a healthy mic can still fail to produce a usable
    command. A passing stage 6 therefore does NOT prove the app works.

    The number that matters here is the elapsed DURATION. A short command
    should end roughly 0.8s after you stop talking. If it comes back at the
    12s ceiling, the endpoint never fired -- see the silence gate in stage 4.
    """
    # From engine.py's listen() mode map (not Config -- these are literals
    # there): command mode waits `timeout` for speech to START, then captures
    # for at most `max_duration`.
    TIMEOUT = 8.0
    MAX_DURATION = 12.0

    print("\n7. LISTEN (the path the application actually uses)")
    print('         SPEAK NOW -- say "open chrome", then STOP and stay quiet.')
    try:
        started = time.monotonic()
        result = ears.listen(mode="command")
        elapsed = time.monotonic() - started
    except Exception as exc:
        _stage("listen() completed", _NO, f"{type(exc).__name__}: {exc}")
        return None

    text = str(result).strip()
    print(f"         listen() returned after {elapsed:.2f}s")

    if not text:
        if elapsed >= TIMEOUT - 0.5:
            _stage(
                "listen() produced a transcript", _NO,
                f"nothing captured in {elapsed:.1f}s -- speech never passed "
                f"the START gate, so _collect_speech timed out. Compare with "
                f"stage 4: your speech RMS must exceed the start threshold",
            )
        else:
            # D45: name the shape explicitly. A capture that opened, ran its
            # silence budget and returned a sliver of audio is a FALSE START
            # -- the window opened before you spoke and closed before you
            # did. That is a different fault from "never started", and the
            # generic "check stage 4" wording sent this investigation to the
            # wrong gate once already.
            _stage(
                "listen() produced a transcript", _NO,
                f"empty after only {elapsed:.2f}s, though stage 6 transcribed "
                f"fine. That is a FALSE START: the capture window opened on "
                f"something that was not speech, spent its silence budget "
                f"and closed before you finished. Compare the start gate in "
                f"stage 4 against your room noise, and see whether stage 5 "
                f"flagged VAD as unreliable",
            )
        return None

    _stage("listen() produced a transcript", _OK,
           f"confidence {getattr(result, 'confidence', 0.0):.2f}, "
           f"{getattr(result, 'speech_rate', 0.0):.1f} words/s")
    print(f"         TRANSCRIPT: {text!r}")

    # D50: this used to WARN whenever `elapsed` exceeded 6s and blamed the
    # silence gate for it. That was wrong, and it fired a false alarm on a
    # healthy run: elapsed is measured from the moment listen() is CALLED,
    # so it includes however long you took to start speaking (listen() waits
    # up to TIMEOUT for that). On the target machine it read
    #
    #   listen() returned after 6.70s      <- WARN, "silence gate" blamed
    #   [STTDiag] ... | audio=1.15s        <- the actual capture
    #
    # i.e. 5.55s of waiting for the user plus a 1.15s capture that ended on
    # a 640ms endpoint, nowhere near the 12s ceiling -- while stage 4 had
    # just measured room noise 3.6x BELOW the silence gate. A diagnostic
    # must not assert a cause it cannot observe, so the gate claim is gone:
    # stage 4 measures that directly, and the engine's own
    # "[STTDiag] ... audio=" line reports the capture length.
    #
    # Only the ceiling is a real defect signal from out here, because that
    # is the one outcome the user's reaction time cannot produce.
    if elapsed >= MAX_DURATION - 0.5:
        _stage(
            "end of speech detected", _NO,
            f"listen() ran {elapsed:.1f}s, i.e. all the way to the "
            f"{MAX_DURATION:.0f}s ceiling -- the endpoint never fired, so "
            f"EVERY command will be this slow. Check the silence gate "
            f"against your room noise in stage 4",
        )
    else:
        _stage("end of speech detected", _OK,
               f"ended before the {MAX_DURATION:.0f}s ceiling")
        print(f"         {elapsed:.2f}s elapsed, but that COUNTS YOUR OWN "
              f"REACTION TIME -- listen() waits up to {TIMEOUT:.0f}s for you")
        print("         to start. The capture length is the engine's own "
              "'audio=' value in the [STTDiag] line above.")
    return text


def stage_route(text: str) -> None:
    """Stage 8: does the transcript reach SARA's router as the right intent?"""
    print("\n8. INTENT ROUTING (does the transcript become a command?)")
    try:
        from sara.core.intent import detect_intent
    except Exception as exc:
        _stage("import router", _SKIP, str(exc))
        return
    try:
        intent, match = detect_intent(text)
    except Exception as exc:
        _stage("route transcript", _NO, f"{type(exc).__name__}: {exc}")
        return
    captured = ""
    if match is not None and match.groups():
        captured = (match.group(1) or "").strip()
    if intent and intent != "chat":
        _stage("transcript reached the router", _OK,
               f"intent={intent}" + (f", target={captured!r}" if captured else ""))
        print("         SARA would execute this as a tool call.")
    else:
        _stage(
            "transcript reached the router", _WARN,
            "the router returned 'chat', so this goes to the LLM rather than "
            "a tool. Correct if you spoke conversationally. If you said "
            "\"open chrome\" and still see this, the transcript wording did "
            "not match any pattern -- commands are matched tightly on purpose, "
            "so say only the command, with nothing before or after it",
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--device", type=int, default=None)
    ap.add_argument("--no-model", action="store_true", help="skip stages 5-8")
    ap.add_argument("--no-listen", action="store_true",
                    help="skip stages 7-8 (the live listen() test)")
    ap.add_argument("--save-wav", type=str, default=None,
                    help="OPT-IN: write the captured audio to this local path")
    args = ap.parse_args(argv)

    print("SARA STT doctor -- entirely local, no network, nothing uploaded")
    print("=" * 66)

    deps = stage_imports()
    stage_config()
    device = stage_devices(args.device)
    captured = stage_capture(args.seconds, device)

    if captured:
        audio, loudest, median = captured
        stage_gate(loudest, median)
        if args.save_wav:
            try:
                with wave.open(args.save_wav, "wb") as fh:
                    fh.setnchannels(1)
                    fh.setsampwidth(2)
                    fh.setframerate(16000)
                    fh.writeframes(audio)
                print(f"\n         recording saved locally to {args.save_wav}")
            except Exception as exc:
                print(f"\n         could not save wav: {exc}")
        if not args.no_model:
            stage_vad(audio)
            if deps.get("_model_cached") is False:
                # A diagnostic must not start a ~1.5GB download as a side
                # effect. Constructing SpeechToText() here would do exactly
                # that, and an interrupted run leaves the cache incomplete
                # and the diagnostic looking stuck -- so this stage is
                # skipped with the explicit command instead.
                print("\n6. TRANSCRIPTION")
                _stage("run transcription", _SKIP,
                       "model not cached -- download it first (command below), "
                       "then re-run. Not downloading from inside a diagnostic")
            else:
                ears = stage_transcribe(audio)
                if ears is not None:
                    try:
                        if not args.no_listen:
                            stage_engine_mic(ears)
                            heard = stage_listen(ears)
                            if heard:
                                stage_route(heard)
                    finally:
                        try:
                            ears.close()
                        except Exception:
                            pass

    print("\n" + "=" * 66)
    if _failures:
        print("FIRST FAILING STAGE: %s" % _failures[0])
        if len(_failures) > 1:
            print("also failed: %s" % ", ".join(_failures[1:]))
        # The one failure worth printing a remedy for inline, because it is
        # invisible from SARA's own behaviour: everything else works and
        # transcripts are simply always empty.
        if any("cached" in f or "hub reachable" in f or "model loaded" in f
               for f in _failures):
            try:
                from config import Config

                size = getattr(Config, "WHISPER_MODEL_SIZE", "medium")
            except Exception:
                size = "medium"
            print("")
            print("  SARA cannot transcribe without the speech model. One-time fix:")
            print("      $env:HF_HUB_OFFLINE=0")
            print(f"      python -c \"from faster_whisper import WhisperModel;"
                  f" WhisperModel('{size}', device='cpu', compute_type='int8')\"")
            print("  Then confirm offline mode is not set permanently:")
            print("      [Environment]::GetEnvironmentVariable('HF_HUB_OFFLINE','User')")
            print("      [Environment]::GetEnvironmentVariable('HF_HUB_OFFLINE','Machine')")
    else:
        print("No stage failed.")
        if captured and not args.no_model and not args.no_listen:
            print("Capture, endpointing, transcription and routing are all healthy,")
            print("so if SARA still does not act on commands the remaining suspect")
            print("is upstream of all of it: wake-word detection or listening-state")
            print("management. Run SARA and check the '[STT] Ready' line and the")
            print("'[Wake]' / '[STTDiag] listen completed' console lines.")
        elif captured and not args.no_model:
            print("Capture and transcription are healthy, but listen() was skipped,")
            print("so endpointing and routing are UNVERIFIED. Re-run without")
            print("--no-listen to test the path the application actually uses.")
    if _warnings:
        print("warnings: %s" % ", ".join(_warnings))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
