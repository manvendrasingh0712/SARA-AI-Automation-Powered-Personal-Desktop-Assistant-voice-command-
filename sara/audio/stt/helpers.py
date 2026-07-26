"""
sara.audio.stt.helpers
Small stateless helpers: RMS energy, language detection, hallucination filter.
"""
from __future__ import annotations


import os
import atexit
import collections
import math
import re
import struct
import threading
import time
from enum import Enum, auto
from typing import Deque, List, Optional, Tuple

import numpy as np

if os.name == "nt":
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        bin_path = os.path.join(cuda_path, "bin")
        if os.path.isdir(bin_path):
            try:
                os.add_dll_directory(bin_path)
                print(f"[STT] CUDA DLL Path Added: {bin_path}")
            except (AttributeError, FileNotFoundError):
                pass

try:
    from faster_whisper import WhisperModel

    _HAS_WHISPER = True
except (ImportError, OSError) as e:
    _HAS_WHISPER = False
    print(f"[STT] faster-whisper import failed; offline STT features disabled. ({e})")

try:
    import webrtcvad

    _HAS_VAD = True
except (ImportError, OSError):
    webrtcvad = None
    _HAS_VAD = False

try:
    import sounddevice as sd

    _HAS_SD = True
except (ImportError, OSError):
    _HAS_SD = False

try:
    from openwakeword.model import Model as _OWWModel

    _HAS_WAKEWORD = True
except (ImportError, OSError):
    _OWWModel = None
    _HAS_WAKEWORD = False

try:
    import pyaudio as _pyaudio

    _HAS_PYAUDIO = True
except (ImportError, OSError):
    _pyaudio = None
    _HAS_PYAUDIO = False

try:
    import audioop as _audioop

    _HAS_AUDIOOP = True
except ImportError:
    _audioop = None
    _HAS_AUDIOOP = False

import queue

from config import Config

# ══════════════════════════════════════════════════════════════════════
# Audio Math & Utilities
# ══════════════════════════════════════════════════════════════════════


def _rms_numpy(buf: bytes) -> float:
    if not buf:
        return 0.0
    arr = np.frombuffer(buf, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(arr * arr)))


def _rms(buf: bytes, sample_width: int = 2) -> float:
    if not buf:
        return 0.0
    try:
        return _rms_numpy(buf)
    except Exception:
        pass
    if _HAS_AUDIOOP and _audioop is not None:
        try:
            return _audioop.rms(buf, sample_width)
        except Exception:
            pass
    count = len(buf) // 2
    samples = struct.unpack(f"<{count}h", buf[: count * 2])
    return math.sqrt(sum(s * s for s in samples) / count)


# ══════════════════════════════════════════════════════════════════════
# Language Detection
# ══════════════════════════════════════════════════════════════════════

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

_HINGLISH_MARKERS = re.compile(
    r"\b(hai|hain|ho|tha|thi|the|kya|nahi|nahin|nhi|aur|par|lekin|toh|bas|"
    r"accha|achha|theek|thik|agar|matlab|yaar|yar|bhai|bro|arrey|arre|"
    r"karo|karna|karte|karein|mujhe|tumhe|aapko|mera|tera|apna|unka|"
    r"bahut|bohot|thoda|jaise|waise|isliye|kyunki|phir|fir|abhi|sab|"
    r"kaisa|kaisi|kaise|kal|aaj|kab|kahan|kyun|kaun|kitna)\b",
    re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    if not text or not text.strip():
        return "en"
    non_space = len(text.replace(" ", ""))
    if non_space == 0:
        return "en"

    deva_count = len(_DEVANAGARI_RE.findall(text))
    if (deva_count / non_space) >= 0.60:
        return "hi"
    if (deva_count / non_space) >= 0.01:
        return "hinglish"

    words = text.split()
    word_count = max(1, len(words))
    markers = _HINGLISH_MARKERS.findall(text)

    if len(markers) >= 2 or (len(markers) / word_count) >= 0.25:
        return "hinglish"
    return "en"


def _lang_from_stt_language(stt_language: str) -> str:
    if not stt_language:
        return "en"
    prefix = stt_language.lower().split("-")[0]
    return "hi" if prefix == "hi" else "en"


# ══════════════════════════════════════════════════════════════════════
# Hallucination guards
# ══════════════════════════════════════════════════════════════════════

# BUGFIX (root cause of "galat voice detect ho raha hai" / phantom
# transcripts like "Subtitles by the Amara.org community"): Whisper is
# well known to hallucinate a small, fixed set of boilerplate phrases —
# left over from its training data (YouTube captions/outros) — when it's
# fed near-silent or non-speech audio (room tone, fan noise, faint music
# bleeding in) instead of correctly emitting nothing. Our VAD/energy gate
# in buffers.py/_collect_speech() is intentionally permissive (so it
# doesn't clip the start of quiet speech), which means these silence/
# noise windows regularly get sent to Whisper at all — so this
# phrase-level guard is the last line of defense that keeps them from
# ever reaching the user as if they were a real command. Unlike
# _is_hallucinated_repetition() below (which only catches repeated
# loops), these phrases are almost always hallucinated on the FIRST and
# only occurrence, so they need an exact/near-exact match check instead
# of a repetition count.
_HALLUCINATION_PHRASES = (
    "subtitles by the amara.org community",
    "amara.org",
    "www.amara.org",
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "subscribe to my channel",
    "like and subscribe",
    "don't forget to subscribe",
    "see you in the next video",
    "see you next time",
    "translated by",
    "transcribed by",
    "captions by",
    "subtitles by",
    "www.youtube.com",
)


def _is_known_hallucination(text: str) -> bool:
    if not text:
        return False
    normalized = re.sub(r"[^\w\s]", "", text.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    for phrase in _HALLUCINATION_PHRASES:
        phrase_norm = re.sub(r"[^\w\s]", "", phrase).strip()
        # Exact match, or the phrase makes up effectively the whole
        # utterance (Whisper sometimes pads it with a stray word or two) —
        # deliberately NOT a loose substring-anywhere check, so a genuine
        # command that happens to mention e.g. "subscribe" isn't dropped.
        if normalized == phrase_norm:
            return True
        if phrase_norm in normalized and len(normalized) <= len(phrase_norm) + 12:
            return True
    return False


def _is_hallucinated_repetition(text: str, min_repeats: int = 3) -> bool:
    """
    Two distinct shapes of Whisper repetition-loop hallucination, both
    checked:
      1. Sentence-level: "I'm sorry. I'm sorry. I'm sorry..." — separated
         by sentence-ending punctuation.
      2. Word/phrase-level: repeated short phrase with NO punctuation
         separating instances (e.g. "के लिए के लिए के लिए...", or
         "How are you Sara? How are you Sara?..." when punctuation
         parsing doesn't cleanly separate every repeat).
    """
    if not text:
        return False

    sentence_parts = [p.strip().lower() for p in re.split(r"[.!?]+", text) if p.strip()]
    if len(sentence_parts) >= min_repeats:
        counts = collections.Counter(sentence_parts)
        _, freq = counts.most_common(1)[0]
        if freq >= min_repeats and (freq / len(sentence_parts)) >= 0.5:
            return True

    words = text.strip().lower().split()
    if len(words) < min_repeats * 2:
        return False
    for n in (1, 2, 3):
        if len(words) < n * min_repeats:
            continue
        grams = [" ".join(words[i : i + n]) for i in range(0, len(words) - n + 1, n)]
        if len(grams) < min_repeats:
            continue
        counts = collections.Counter(grams)
        _, freq = counts.most_common(1)[0]
        if freq >= min_repeats and (freq / len(grams)) >= 0.6:
            return True

    return False
