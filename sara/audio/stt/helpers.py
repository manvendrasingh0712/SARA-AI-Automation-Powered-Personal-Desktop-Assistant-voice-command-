"""
sara.audio.stt.helpers
Small stateless helpers: RMS energy, language detection, hallucination filter.
"""
from __future__ import annotations


import collections
import math
import re
import struct
from typing import Optional

import numpy as np
from sara.core.diag import swallowed


def _optional_import_failed(dep_name: str, exc: Exception) -> None:
    """Log a soft-disabled optional dependency without crashing this module.

    Deliberately catches Exception (not just ImportError/OSError): a
    version-incompatible transitive dependency (e.g. an sklearn/scipy build
    that predates the installed numpy/Python ABI) can raise TypeError,
    RuntimeError, AttributeError, etc. *during* import, not just the
    ImportError/OSError this used to be narrowed to. Any of those must
    still degrade this feature to "disabled", not take down STT entirely.
    """
    print(f"[STT] {dep_name} import failed; related features disabled. ({type(exc).__name__}: {exc})")


# NOTE: these two blocks (_HAS_WHISPER / _HAS_SD) have no actual import in
# their try body -- faster-whisper and sounddevice are imported for real in
# engine.py, which owns the authoritative _HAS_WHISPER/_HAS_SD flags. These
# duplicate names are dead weight left over from an earlier refactor; kept
# as no-op True flags here only so nothing that reads
# `sara.audio.stt.helpers._HAS_WHISPER` breaks. Prefer engine.py's flags.
try:
    _HAS_WHISPER = True
except Exception as e:
    _HAS_WHISPER = False
    _optional_import_failed("faster-whisper", e)

try:
    import webrtcvad

    _HAS_VAD = True
except Exception as e:
    webrtcvad = None
    _HAS_VAD = False
    _optional_import_failed("webrtcvad", e)

try:
    _HAS_SD = True
except Exception as e:
    _HAS_SD = False
    _optional_import_failed("sounddevice", e)

try:
    from openwakeword.model import Model as _OWWModel

    _HAS_WAKEWORD = True
except Exception as e:
    _OWWModel = None
    _HAS_WAKEWORD = False
    _optional_import_failed("openwakeword", e)

try:
    import pyaudio as _pyaudio

    _HAS_PYAUDIO = True
except Exception as e:
    _pyaudio = None
    _HAS_PYAUDIO = False
    _optional_import_failed("pyaudio", e)

try:
    import audioop as _audioop

    _HAS_AUDIOOP = True
except ImportError:
    _audioop = None
    _HAS_AUDIOOP = False



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
    except Exception as _exc:
        swallowed(None, "helpers._rms:102", _exc)
    if _HAS_AUDIOOP and _audioop is not None:
        try:
            return _audioop.rms(buf, sample_width)
        except Exception as _exc:
            swallowed(None, "helpers._rms:107", _exc)
    count = len(buf) // 2
    samples = struct.unpack(f"<{count}h", buf[: count * 2])
    return math.sqrt(sum(s * s for s in samples) / count)


# ══════════════════════════════════════════════════════════════════════
# Language Detection
# ══════════════════════════════════════════════════════════════════════

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# "the" and "bro" are deliberately NOT markers. "the" (Hindi "were") is the
# commonest English word: with it, 7 of 9 plain English sentences ("what is
# the capital of the country") were labelled Hinglish, so in auto mode SARA
# answered in the Hindi voice (E2E log 2026-10-30, "Do you know the
# character of the character..."). Real Hinglish keeps its many other
# markers; "bro" is ordinary English slang.
_HINGLISH_MARKERS = re.compile(
    r"\b(hai|hain|ho|tha|thi|kya|nahi|nahin|nhi|aur|par|lekin|toh|bas|"
    r"accha|achha|theek|thik|agar|matlab|yaar|yar|bhai|arrey|arre|"
    r"karo|karna|karte|karein|mujhe|tumhe|aapko|mera|tera|apna|unka|"
    r"bahut|bohot|thoda|jaise|waise|isliye|kyunki|phir|fir|abhi|sab|"
    r"kaisa|kaisi|kaise|kal|aaj|kab|kahan|kyun|kaun|kitna|"
    r"wo|woh|yeh|ye|ghar|tum|hum|kuch|koi|bhi|mein|raha|rahi|rahe|"
    r"chahiye|batao|bolo)\b",
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
    # "Thank you very much for watching." / "Thanks so much for watching": extra words defeat the exact
    # phrase list above, but nobody gives a voice command that ends in "for watching".
    if re.search(r"\bfor watching\b", normalized) and len(normalized.split()) <= 8:
        return True
    # Broadcaster credits Whisper learned from subtitles: "Copyright WDR 2021".
    if re.match(r"^(?:copyright|\u00a9)\b.*\b(?:19|20)\d\d$", normalized) and len(normalized.split()) <= 6:
        return True
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


# Real log (sara_unmatched_queries.jsonl, 2026-10-30): on silence / music Whisper printed its OWN
# initial_prompt back ("Transcribe exactly what is spoken, in whichever language or mix it was
# spoken in...", "Keep proper names and technical terms exactly as spoken."), long loops of one
# character ("ndndnd...", "ṭṭṭṭ...", "vay,vay,vay,...") and caption boilerplate ("Thank you very much
# for watching.", "Copyright WDR 2021"). Those reached the router and were answered by the LLM as if
# the user had said them.
_PROMPT_ECHO_MARKERS = (
    "code-switched", "code switched", "transcribe exactly", "switching script",
    "inventing words", "technical terms exactly", "proper names and technical",
    "natural code", "in whichever language", "the word is written", "the words are written",
)


def _is_prompt_echo(text: str) -> bool:
    """True when the transcript is (part of) the style prompt we gave Whisper."""
    t = re.sub(r"\s+", " ", (text or "").lower().replace("\u2011", "-"))
    return any(m in t for m in _PROMPT_ECHO_MARKERS)


_RUN_RE = re.compile(r"(.{1,8}?)\1{5,}", re.DOTALL)


def _is_degenerate_text(text: str) -> bool:
    """
    Text with no real content: only punctuation (". ."), a short unit repeated many times with or
    without separators ("ndndnd...", "vay,vay,vay,..."), or the same word four times in a row.
    """
    t = (text or "").strip()
    if not t:
        return False
    if not re.search(r"\w", t):
        return True
    compact = re.sub(r"[\s,.;:!?\-]+", "", t.lower())
    if len(compact) >= 12:
        m = _RUN_RE.search(compact)
        if m and len(m.group(0)) >= 0.6 * len(compact):
            return True
        if len(set(compact)) <= 4 and len(compact) >= 20:
            return True
    words = re.findall(r"[\w']+", t.lower())
    run = 1
    for a, b2 in zip(words, words[1:]):
        run = run + 1 if a == b2 else 1
        if run >= 4:
            return True
    return False


def _is_latin_text(text: str) -> bool:
    """True when every character is Latin/ASCII-range (no Devanagari, Arabic, CJK ...)."""
    return all(ord(c) < 0x250 for c in text or "")


def choose_transcript(text_a, lang_a, prob_a, logprob_a, run_b, routes, min_en_prob=0.85, min_logprob=-0.6):
    """
    Two recognisers, one answer. A = the English-capable model (fast, on the GPU); B = the Hinglish model
    (a Whisper-base fine-tune for Hindi / Hinglish, run only when it is needed). Measured on 51 spoken
    clips (Windows voices, three noise levels): A alone understood 54 % of commands, B alone 91 %,
    this rule 94 % -- and English sentences keep A's accuracy (WER 2.4 % vs 6.5 % for B).

      1. A is a command the router understands and is plain English  -> A   (B is never run)
      2. else B's text is a command the router understands           -> B
      3. else A, only if it is confidently English (language probability and log-probability high)
      4. else B   (Hindi / Hinglish conversation: B writes it as readable Roman Hinglish)

    run_b() -> text (called at most once); routes(text) -> True when the app's router maps it to a tool.
    Returns ("A" | "B", text).
    """
    a = (text_a or "").strip()
    if a and routes(a) and lang_a == "en" and _is_latin_text(a):
        return "A", a
    b = (run_b() or "").strip()
    if b and routes(b):
        return "B", b
    if a and lang_a == "en" and prob_a >= min_en_prob and logprob_a >= min_logprob and _is_latin_text(a):
        return "A", a
    return ("B", b) if b else ("A", a)


def need_main_model(b_text, b_logprob, routes, confident_logprob=-0.3) -> bool:
    """
    "Hinglish model first" ordering. True when the Hinglish model's text still needs a second opinion from the main
    model: it is empty, or it is not a command the router understands AND the model is not sure about it.
    A command, or a confident plain-Latin sentence of three words or more, is final (the main model is not run).
    """
    b = (b_text or "").strip()
    if not b:
        return True
    if routes(b):
        return False
    return not (b_logprob >= confident_logprob and _is_latin_text(b) and len(b.split()) >= 3)


def pick_after_hinglish_first(b_text, a_text, a_lang, a_prob, a_logprob, routes, min_en_prob=0.9, min_logprob=-0.5):
    """
    Second stage of "Hinglish model first": the main model (A) has also listened. Returns ("A" | "B", text).
    A wins only when it is a command in plain English, or it is a confident English sentence; otherwise B.
    Measured on 38 sentences of the user's own voice (command / conversation, English / Hindi): Hinglish model alone
    72 %, main model alone 41-51 %, this ordering 80 % with the main model needed for 21 % of sentences.
    """
    a = (a_text or "").strip()
    b = (b_text or "").strip()
    if a and routes(a) and a_lang == "en" and _is_latin_text(a):
        return "A", a
    if a and a_lang == "en" and a_prob >= min_en_prob and a_logprob >= min_logprob and _is_latin_text(a):
        return "A", a
    return ("B", b) if b else ("A", a)


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

# ══════════════════════════════════════════════════════════════════════
# D43: foreign-script hallucination filter
# ══════════════════════════════════════════════════════════════════════
#
# OBSERVED ON THE TARGET MACHINE
#   [STTDiag] listen completed | mode=command | audio=12.26s | text='こんにちは!'
#
#   The user said "open chrome" and stopped. Neither existing filter
#   caught it: _is_hallucinated_repetition() only matches repeated loops,
#   and _HALLUCINATION_PHRASES is a list of English YouTube boilerplate.
#   So a phantom transcript reached the router, matched no command pattern,
#   and was dispatched to the LLM as conversation -- the command was lost
#   with no error anywhere.
#
# WHY A SCRIPT CHECK IS SOUND HERE RATHER THAN A GUESS
#   Config.validate() clamps SARA_LANGUAGE to exactly
#   ("english", "hindi", "hinglish"), so everything SARA is built to hear
#   is written in Latin or Devanagari. A transcript made up of CJK, Hangul,
#   Arabic, Hebrew, Thai, Cyrillic or Greek letters is therefore not a
#   mis-heard command, it is Whisper inventing text on near-silent audio --
#   the same failure mode _HALLUCINATION_PHRASES exists for, in a script
#   that list could never enumerate.
#
# THE ESCAPE HATCH, WHICH IS WHY THIS IS SAFE
#   Config.STT_LANGUAGE is deliberately free-form (any Whisper code), so a
#   user CAN pin Japanese. This filter is therefore inert whenever
#   STT_LANGUAGE is set: an explicitly pinned language means the user
#   intends that script and Whisper should be trusted. It only guards the
#   default auto-detect path.
#
# The test is on LETTERS only -- digits, punctuation, spaces and emoji are
# ignored, so "99" or "!!" never decides the outcome -- and it needs a
# clear majority, not a single stray character, so one borrowed glyph
# inside a real command cannot discard it.

# Unicode ranges SARA's three supported languages are written in.
_ALLOWED_SCRIPT_RANGES = (
    (0x0000, 0x024F),  # Basic Latin, Latin-1, Latin Extended-A/B
    (0x0900, 0x097F),  # Devanagari (Hindi)
    (0xA8E0, 0xA8FF),  # Devanagari Extended
    (0x1E00, 0x1EFF),  # Latin Extended Additional
)

# A transcript needs this share of its letters outside those ranges before
# it is treated as invented. Well above a stray glyph, well below "half
# the sentence", which no genuine command would reach.
_FOREIGN_SCRIPT_RATIO = 0.6

# Below this many letters the ratio is not meaningful -- a 1-letter
# transcript would trivially be "100% foreign".
_FOREIGN_SCRIPT_MIN_LETTERS = 2


def _in_allowed_script(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _ALLOWED_SCRIPT_RANGES)


def _is_foreign_script_hallucination(
    text: str, stt_language: Optional[str] = None
) -> bool:
    """
    True when `text` is predominantly written in a script none of SARA's
    supported languages use, and no explicit STT language was pinned.

    Inert when `stt_language` is set -- see the module note above.
    """
    if stt_language:
        return False
    if not text:
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < _FOREIGN_SCRIPT_MIN_LETTERS:
        return False
    foreign = sum(1 for ch in letters if not _in_allowed_script(ch))
    return (foreign / len(letters)) >= _FOREIGN_SCRIPT_RATIO
