"""
sara.audio.tts.text_prep
Text normalization + adaptive chunk-splitting before synthesis.
"""
from __future__ import annotations



import os
import re


from config import Config

# Single source of truth for language detection (Devanagari script +
# romanized Hinglish marker words) — same detector STT already uses and
# the same import voice_params.py already relies on for voice routing.
# Number normalization below needs to know the language BEFORE picking
# English vs. Hindi number words, and clean_for_tts() runs earlier in
# the pipeline than tts/engine.py's own _detect_lang() call — so this
# detects independently here rather than requiring a language parameter
# be threaded through every clean_for_tts() call site.
from sara.audio.stt.helpers import _detect_language as _stt_detect_language

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


# ══════════════════════════════════════════════════════════════════════════════
#  LANGUAGE DETECTION
# ══════════════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════════════
#  TEXT CLEANER
# ══════════════════════════════════════════════════════════════════════════════

_EMOJI_RE = re.compile(r"[\U0001F300-\U0001F9FF\U00002702-\U000027B0]+", re.UNICODE)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MD_RE = re.compile(r"(\*{1,3}|#{1,6}|`{1,3}|_{1,2})(.*?)\1", re.DOTALL)
_MULTI_SP = re.compile(r"\s+")
_ABBR: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bAI\b"), "A I"),
    (re.compile(r"\bAPI\b"), "A P I"),
    (re.compile(r"\bLLM\b"), "L L M"),
    (re.compile(r"\bOK\b"), "okay"),
    (re.compile(r"\betc\.?\b"), "et cetera"),
    (re.compile(r"\bvs\.?\b"), "versus"),
]


# ══════════════════════════════════════════════════════════════════════════════
#  NUMBER / CURRENCY / TIME NORMALIZATION
# ══════════════════════════════════════════════════════════════════════════════
#
# Kokoro (both the English and Hindi voices) reads raw digit sequences
# poorly — the confirmed gap this addresses (see the SARA TTS/STT audit,
# Part 1/Part 4): clean_for_tts() previously had zero digit-to-word logic
# in either language.
#
# SCOPE, STATED EXPLICITLY:
#   - Covered: cardinal integers, decimals ("3.14" -> "three point one
#     four"), percentages ("90%" -> "ninety percent"), currency amounts
#     (₹ / Rs / Rs. / INR / $), and simple H:MM clock times ("3:45").
#   - NOT covered: calendar dates in any format ("15/08/2026", "15th
#     August", "next Tuesday"), ordinals ("3rd"), fractions ("3/4"),
#     phone/order/OTP numbers (deliberately left untouched — see the
#     digit-count heuristic below), or 12-hour vs. 24-hour time
#     conversion (a bare "14:30" is read as "fourteen thirty", not
#     "half past two"). Full calendar-date parsing is locale-fragile
#     enough that a half-working version would be worse than not
#     attempting it in this pass — flagged as a known follow-up, not
#     silently skipped.
#   - A digit run touching a letter with no space ("3rd", "42B",
#     "AI302") is left completely untouched, not partially converted —
#     an earlier version of this matched the digits anyway and produced
#     "threerd" / "forty-twoB", which is worse than doing nothing.
#     Caught by testing before this shipped, not theoretical.
#   - Bare (no comma-grouping) digit runs of 7+ digits are left
#     untouched on purpose — a phone number, OTP, or order ID read out
#     as one giant cardinal number ("nine billion eight hundred...")
#     would be a clear regression, not an improvement, and there's no
#     reliable way to tell those apart from a genuine large quantity at
#     this layer. Comma-grouped numbers of any length ("12,34,567") are
#     converted regardless, since the grouping itself signals a
#     deliberate quantity, not an ID.
#   - Magnitude is capped at _NUM_MAGNITUDE_CAP (999,999,999) for
#     sanity — realistic assistant use (reminders, prices, quantities)
#     never needs more than this; anything larger is left as digits
#     rather than producing an ungainly number-word run.

_NUM_MAGNITUDE_CAP = 999_999_999
_PHONE_LIKE_DIGIT_THRESHOLD = 7

_EN_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
_EN_TENS = (
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
)
_EN_SCALES = ("", " thousand", " million")

# Hindi 0-99 cardinals are irregular (not compositionally built from a
# ones+tens rule the way English's "twenty-one" is) — this is the
# standard, complete lookup table; there is no shorter correct way to
# build it.
_HI_0_99 = (
    "शून्य", "एक", "दो", "तीन", "चार", "पांच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह",
    "अठारह", "उन्नीस", "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस",
    "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस", "तीस", "इकतीस", "बत्तीस",
    "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
    "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस",
    "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास", "पचास", "इक्यावन", "बावन",
    "तिरपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ", "साठ",
    "इकसठ", "बासठ", "तिरसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ",
    "उनहत्तर", "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर",
    "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उन्यासी", "अस्सी",
    "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी",
    "अट्ठासी", "नवासी", "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे",
    "पंचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे",
)


def _en_group_upto_999(n: int) -> str:
    if n == 0:
        return ""
    parts = []
    hundreds, rem = divmod(n, 100)
    if hundreds:
        parts.append(f"{_EN_ONES[hundreds]} hundred")
    if rem:
        if rem < 20:
            parts.append(_EN_ONES[rem])
        else:
            tens, ones = divmod(rem, 10)
            parts.append(_EN_TENS[tens] + (f"-{_EN_ONES[ones]}" if ones else ""))
    return " ".join(parts)


def _int_to_words_en(n: int) -> str:
    if n == 0:
        return "zero"
    negative = n < 0
    n = abs(n)
    groups = []
    scale_idx = 0
    while n > 0 and scale_idx < len(_EN_SCALES):
        group = n % 1000
        if group:
            groups.append(_en_group_upto_999(group) + _EN_SCALES[scale_idx])
        n //= 1000
        scale_idx += 1
    words = " ".join(reversed(groups))
    return f"minus {words}" if negative else words


def _hi_group_upto_999(n: int) -> str:
    if n == 0:
        return ""
    parts = []
    hundreds, rem = divmod(n, 100)
    if hundreds:
        parts.append(f"{_HI_0_99[hundreds]} सौ")
    if rem:
        parts.append(_HI_0_99[rem])
    return " ".join(parts)


def _int_to_words_hi(n: int) -> str:
    # Indian numbering system (crore/lakh, not thousand/million groups).
    # Under _NUM_MAGNITUDE_CAP (999,999,999), crore/lakh/thousand counts
    # are each guaranteed < 100, so the plain _HI_0_99 lookup is always
    # enough for them — no recursive grouping needed at this magnitude.
    if n == 0:
        return _HI_0_99[0]
    negative = n < 0
    n = abs(n)
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, rest = divmod(n, 1_000)
    parts = []
    if crore:
        parts.append(f"{_HI_0_99[crore]} करोड़")
    if lakh:
        parts.append(f"{_HI_0_99[lakh]} लाख")
    if thousand:
        parts.append(f"{_HI_0_99[thousand]} हज़ार")
    if rest:
        parts.append(_hi_group_upto_999(rest))
    words = " ".join(parts)
    return f"माइनस {words}" if negative else words


def _int_to_words(n: int, lang: str) -> str:
    return _int_to_words_hi(n) if lang in ("hi", "hinglish") else _int_to_words_en(n)


def _digit_word(d: str, lang: str) -> str:
    i = int(d)
    return _HI_0_99[i] if lang in ("hi", "hinglish") else _EN_ONES[i]


def _currency_word(symbol: str, value: int, lang: str) -> str:
    is_hindi = lang in ("hi", "hinglish")
    sym_norm = symbol.strip(".").upper()
    if symbol == "₹" or sym_norm in ("RS", "INR"):
        if is_hindi:
            return "रुपया" if value == 1 else "रुपये"
        return "rupee" if value == 1 else "rupees"
    if symbol == "$":
        if is_hindi:
            return "डॉलर"
        return "dollar" if value == 1 else "dollars"
    return ""


def _time_to_words(hour: int, minute: int, lang: str) -> str:
    is_hindi = lang in ("hi", "hinglish")
    if is_hindi:
        hour_word = _HI_0_99[hour] if hour < 100 else str(hour)
        if minute == 0:
            return f"{hour_word} बजे"
        return f"{hour_word} बजकर {_HI_0_99[minute]} मिनट"
    hour_word = _int_to_words_en(hour)
    if minute == 0:
        return f"{hour_word} o'clock"
    minute_word = f"oh {_EN_ONES[minute]}" if minute < 10 else _int_to_words_en(minute)
    return f"{hour_word} {minute_word}"


_DIGIT_RE = re.compile(r"\d")

# All four number-shape branches share the same guard: a digit run
# immediately touching a letter on either side (no space) is NOT treated
# as a standalone convertible number — "3rd", "42B", "AI302", "B2" all
# stay untouched rather than getting a word glued onto the adjacent
# letters (an earlier version of this matched the digits regardless and
# produced "threerd" / "forty-twoB" / "AIthree hundred two" — confirmed
# by testing before this shipped, not a theoretical concern).
_NUM_RE = re.compile(
    r"(?P<cur_sym>₹|Rs\.?|INR|\$)\s?(?P<cur_num>\d[\d,]*(?:\.\d+)?)(?![A-Za-z])"
    r"|(?<![A-Za-z0-9])(?P<time_h>[01]?\d|2[0-3]):(?P<time_m>[0-5]\d)(?!:?\d)(?![A-Za-z])"
    r"|(?<![A-Za-z0-9])(?P<pct_num>\d+(?:\.\d+)?)\s?%"
    r"|(?<![A-Za-z0-9])(?P<dec_int>\d+)\.(?P<dec_frac>\d+)(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9])(?P<int_num>\d{1,3}(?:,\d{2,3})+|\d+)(?![A-Za-z0-9])"
)


def _replace_number_match(m: "re.Match", lang: str) -> str:
    if m.group("cur_sym"):
        symbol = m.group("cur_sym")
        num_str = m.group("cur_num").replace(",", "")
        int_part_str, _, frac_part = num_str.partition(".")
        try:
            value = int(int_part_str) if int_part_str else 0
        except ValueError:
            return m.group(0)
        if value > _NUM_MAGNITUDE_CAP:
            return m.group(0)
        words = _int_to_words(value, lang)
        if frac_part:
            connector = " दशमलव " if lang in ("hi", "hinglish") else " point "
            words += connector + " ".join(_digit_word(d, lang) for d in frac_part)
        currency = _currency_word(symbol, value, lang)
        return f"{words} {currency}".strip()

    if m.group("time_h") is not None:
        hour = int(m.group("time_h"))
        minute = int(m.group("time_m"))
        return _time_to_words(hour, minute, lang)

    if m.group("pct_num") is not None:
        num_str = m.group("pct_num")
        int_part_str, _, frac_part = num_str.partition(".")
        try:
            value = int(int_part_str) if int_part_str else 0
        except ValueError:
            return m.group(0)
        if value > _NUM_MAGNITUDE_CAP:
            return m.group(0)
        words = _int_to_words(value, lang)
        if frac_part:
            connector = " दशमलव " if lang in ("hi", "hinglish") else " point "
            words += connector + " ".join(_digit_word(d, lang) for d in frac_part)
        pct_word = "प्रतिशत" if lang in ("hi", "hinglish") else "percent"
        return f"{words} {pct_word}"

    if m.group("dec_int") is not None:
        try:
            value = int(m.group("dec_int"))
        except ValueError:
            return m.group(0)
        if value > _NUM_MAGNITUDE_CAP:
            return m.group(0)
        connector = " दशमलव " if lang in ("hi", "hinglish") else " point "
        frac_words = " ".join(_digit_word(d, lang) for d in m.group("dec_frac"))
        return f"{_int_to_words(value, lang)}{connector}{frac_words}"

    if m.group("int_num") is not None:
        raw = m.group("int_num")
        has_commas = "," in raw
        digits_only = raw.replace(",", "")
        if not has_commas and len(digits_only) >= _PHONE_LIKE_DIGIT_THRESHOLD:
            # Left untouched on purpose — see module-level SCOPE note
            # above (phone/OTP/order-ID heuristic).
            return m.group(0)
        try:
            value = int(digits_only)
        except ValueError:
            return m.group(0)
        if value > _NUM_MAGNITUDE_CAP:
            return m.group(0)
        return _int_to_words(value, lang)

    return m.group(0)


def _normalize_numbers_for_tts(text: str, lang: str) -> str:
    if not _DIGIT_RE.search(text):
        return text  # fast path — most TTS text has no digits at all
    return _NUM_RE.sub(lambda m: _replace_number_match(m, lang), text)


def clean_for_tts(text: str) -> str:
    # Detected once, up front, on the raw input — before markdown/URL/
    # emoji stripping, none of which can affect Devanagari-script or
    # Hinglish-marker-word detection anyway, so detecting early vs. late
    # makes no difference to the result, only to how many places in this
    # function would otherwise need to re-run detection.
    lang = _stt_detect_language(text)
    text = _MD_RE.sub(r"\2", text)
    text = _URL_RE.sub("link", text)
    text = _EMOJI_RE.sub("", text)
    for pat, repl in _ABBR:
        text = pat.sub(repl, text)
    text = _normalize_numbers_for_tts(text, lang)
    return _MULTI_SP.sub(" ", text).strip()


# ══════════════════════════════════════════════════════════════════════════════
#  CHUNKER
# ══════════════════════════════════════════════════════════════════════════════

_SENT_END = re.compile(r"(?<![A-Z])(?<!\d)[.!?।\u0964]\s+|;\s+")
_CLAUSE_SPLT = re.compile(r"[,:\u2014\u2013]\s+")


def _split_adaptive(text: str) -> list[str]:
    parts = _SENT_END.split(text.strip())
    sentences: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > _MAX_CHUNK:
            clauses = _CLAUSE_SPLT.split(p)
            buf = ""
            for c in clauses:
                c = c.strip()
                candidate = (buf + ", " + c).strip() if buf else c
                if len(candidate) >= _MIN_CHUNK:
                    sentences.append(candidate)
                    buf = ""
                else:
                    buf = candidate
            if buf:
                sentences.append(buf)
        else:
            sentences.append(p)
    merged: list[str] = []
    for s in sentences:
        if merged and len(s) < _MIN_CHUNK:
            merged[-1] += " " + s
        else:
            merged.append(s)
    return merged if merged else [text.strip()]