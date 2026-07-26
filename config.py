"""
config.py
Centralized configuration management for Sara AI.
TTS backend: Kokoro ONNX (in-process, GPU-accelerated with CPU fallback).

PRODUCTION-AUDIT CHANGES (this revision)
-----------------------------------------
1. Added previously-undefined settings that other modules (stt.py, llm.py)
   were silently reading via getattr(Config, "X", default) without them
   ever existing here — meaning they were NOT actually configurable via
   .env before this fix. Now they are first-class Config attributes with
   proper defaults, bounds, and validation, matching the rest of the file.
2. Config.validate() no longer calls sys.exit(1) on a fatal misconfiguration.
   It now raises ConfigError instead. Behavior for an unhandled case is
   identical (the process still stops), but the failure is now a normal,
   catchable Python exception instead of an abrupt process kill — this
   makes the module importable/testable in isolation (e.g. from a test
   suite) without silently terminating the test runner.
3. validate() is now idempotent (guarded by a class-level _validated flag)
   so calling it more than once (e.g. from a test, or a future explicit
   re-validation call) does not repeat print-output or redo work.
4. DEBUG_MODE now defaults to False (was True) — a production build should
   not be verbose by default; set DEBUG_MODE=true in .env for development.
5. KOKORO_SPEED was previously defined but never actually consumed by
   tts.py (only KOKORO_SPEED_EN / KOKORO_SPEED_HI were read), making it
   dead configuration. It is now a genuine base/fallback value: if a user
   sets only KOKORO_SPEED in .env, both KOKORO_SPEED_EN and
   KOKORO_SPEED_HI inherit it automatically unless explicitly overridden.
6. WAKE_WORDS force-inclusion of the four built-in wake-word variants
   (sara/sarah/hey sara/hey sarah) is now optional, controlled by
   WAKE_WORD_ALLOW_CUSTOM_ONLY. Default behavior (False) is unchanged
   from before, so existing .env files keep working exactly as-is.
7. Added a single, canonical, CWD-independent DB_PATH and NOTES_FILE_PATH,
   resolved relative to this file's own location (the project root) —
   not the process's current working directory. Previously, database.py,
   reminders.py, and system.py each computed their own path independently
   via os.getcwd(), which meant launching the app from a different
   working directory could silently point different modules at different
   files. All modules that touch the shared SQLite DB or the notes file
   should now import DB_PATH / NOTES_FILE_PATH from here instead of
   computing their own path.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Project root (this file's own directory) — used for CWD-independent
# paths below. Do NOT use os.getcwd() for anything that must be stable
# regardless of where the process happens to be launched from. ──────────
_PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(Exception):
    """
    Raised when configuration validation finds a fatal, unrecoverable
    problem (e.g. LLM_BACKEND=gemini but no GEMINI_API_KEY set).

    This replaces the previous sys.exit(1) behavior. An uncaught
    ConfigError still stops the process (same end result as before for
    normal app startup), but it is now a regular exception — catchable,
    testable, and inspectable — instead of an abrupt, untestable process
    kill.
    """


# ── Optional ONNX Runtime introspection (debug output only) ────────────────
try:
    import onnxruntime as _ort

    _ORT_AVAILABLE_PROVIDERS: list[str] = _ort.get_available_providers()
except ImportError:
    _ORT_AVAILABLE_PROVIDERS = []


def _bool(val: str | None, default: bool = False) -> bool:
    if not val:
        return default
    return val.strip().lower() in ("true", "1", "yes")


def _int(val: str | None, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _float(val: str | None, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _optional_str(val: str | None) -> str | None:
    if not val or not val.strip():
        return None
    return val.strip()


# ── Numeric bounds (single source of truth — no magic numbers in validate) ─
_CPU_COUNT = os.cpu_count() or 4

_MIN_THREADS = 1
_MAX_THREADS = max(_CPU_COUNT * 2, 8)
_DEFAULT_ORT_INTRA_THREADS_GPU = min(
    4, _CPU_COUNT
)  # GPU does the heavy lifting; keep CPU threads light
_DEFAULT_ORT_INTRA_THREADS_CPU = _CPU_COUNT

_BYTES_PER_GB = 1024**3
_DEFAULT_CUDA_MEM_LIMIT_GB = 3
_MIN_CUDA_MEM_LIMIT_GB = 0.25
_MAX_CUDA_MEM_LIMIT_GB = 24

_MIN_PLAYBACK_BUFFER_MS = 10
_MAX_PLAYBACK_BUFFER_MS = 500

_MIN_WARMUP_WAIT_S = 0.0
_MAX_WARMUP_WAIT_S = 30.0

_MIN_QUEUE_SIZE = 1
_MAX_QUEUE_SIZE = 64

_MIN_PHRASE_CACHE_SIZE = 1
_MAX_PHRASE_CACHE_SIZE = 512
_MIN_PHRASE_CACHE_MAXLEN = 1
_MAX_PHRASE_CACHE_MAXLEN = 200

_MIN_KOKORO_SPEED = 0.5
_MAX_KOKORO_SPEED = 2.0

_MIN_TTS_VOLUME = 0.0
_MAX_TTS_VOLUME = 2.0

_MIN_STT_SETTLE_GAP_S = 0.3
_MAX_STT_SETTLE_GAP_S = 5.0

# WebRTC Audio Processing Module (APM) only accepts these native rates.
_AEC_VALID_SAMPLE_RATES = (8000, 16000, 32000, 48000)
_MIN_AEC_STREAM_DELAY_MS = 0
_MAX_AEC_STREAM_DELAY_MS = 500

# ── New bounds for previously-undefined-but-consumed settings ──────────────
_MIN_WHISPER_BEAM_SIZE = 1
_MAX_WHISPER_BEAM_SIZE = 10

_MIN_NO_SPEECH_THRESHOLD = 0.0
_MAX_NO_SPEECH_THRESHOLD = 1.0

_MIN_LOG_PROB_THRESHOLD = -10.0
_MAX_LOG_PROB_THRESHOLD = 0.0

_MIN_COMPRESSION_RATIO_THRESHOLD = 1.0
_MAX_COMPRESSION_RATIO_THRESHOLD = 10.0

_MIN_HALLUCINATION_MIN_REPEATS = 2
_MAX_HALLUCINATION_MIN_REPEATS = 10

_MIN_TTS_BLEED_MULTIPLIER = 1.0
_MAX_TTS_BLEED_MULTIPLIER = 5.0

_MIN_LLM_RETRIES = 0
_MAX_LLM_RETRIES = 10

_MIN_LLM_RETRY_DELAY_S = 0.1
_MAX_LLM_RETRY_DELAY_S = 30.0

_MIN_LLM_WARMUP_WAIT_S = 0.0
_MAX_LLM_WARMUP_WAIT_S = 120.0

_MIN_GEMINI_HISTORY_TOKENS = 1_000
_MAX_GEMINI_HISTORY_TOKENS = 200_000


class Config:
    # ── Idempotency guard for validate() — see ConfigError docstring above ─
    _validated: bool = False

    # ── LLM backend ───────────────────────────────────────────────────────
    LLM_BACKEND: str = os.getenv("LLM_BACKEND", "ollama").lower()

    # ── Ollama ────────────────────────────────────────────────────────────
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5")
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_TIMEOUT: int = _int(os.getenv("OLLAMA_TIMEOUT"), default=30)
    OLLAMA_NUM_CTX: int = _int(os.getenv("OLLAMA_NUM_CTX"), default=2048)
    OLLAMA_SUMMARY_NUM_CTX: int = _int(
        os.getenv("OLLAMA_SUMMARY_NUM_CTX"), default=4096
    )
    OLLAMA_NUM_PREDICT: int = _int(os.getenv("OLLAMA_NUM_PREDICT"), default=300)
    OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")

    # ── Gemini ────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    # NEW: was previously read via getattr(cfg, "GEMINI_MODEL", "gemini-2.5-flash")
    # in llm.py with no matching definition here — now first-class and
    # actually configurable via .env.
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    # NEW: was read via getattr(cfg, "GEMINI_MAX_HISTORY_TOKENS", 30_000) in
    # llm.py's _trim_history_to_budget() with no matching definition here.
    GEMINI_MAX_HISTORY_TOKENS: int = _int(
        os.getenv("GEMINI_MAX_HISTORY_TOKENS"), default=30_000
    )

    # ── LLM retry / warm-up behavior ─────────────────────────────────────
    # NEW: these four were read via getattr(...) in llm.py's
    # _stream_generic()/generate_response_stream() with no definitions
    # here — meaning retry/backoff/warm-up tuning was silently
    # unconfigurable via .env before this fix.
    LLM_MAX_RETRIES: int = _int(os.getenv("LLM_MAX_RETRIES"), default=2)
    LLM_RETRY_BASE_DELAY_S: float = _float(
        os.getenv("LLM_RETRY_BASE_DELAY_S"), default=1.5
    )
    LLM_RETRY_MAX_DELAY_S: float = _float(
        os.getenv("LLM_RETRY_MAX_DELAY_S"), default=8.0
    )
    LLM_WARMUP_WAIT_S: float = _float(os.getenv("LLM_WARMUP_WAIT_S"), default=20.0)

    # ── Kokoro ONNX TTS — model / runtime ───────────────────────────────────
    KOKORO_MODEL_PATH: str = os.getenv("KOKORO_MODEL_PATH", "models/kokoro-v1.0.onnx")
    KOKORO_VOICES_PATH: str = os.getenv("KOKORO_VOICES_PATH", "models/voices-v1.0.bin")
    KOKORO_USE_GPU: bool = _bool(os.getenv("KOKORO_USE_GPU", "True"), default=True)

    CUDA_GPU_MEM_LIMIT_BYTES: int = _int(
        os.getenv("CUDA_GPU_MEM_LIMIT_BYTES"),
        default=int(_DEFAULT_CUDA_MEM_LIMIT_GB * _BYTES_PER_GB),
    )

    # Intra-op threads default lower when GPU is active — the CUDA EP does the
    # heavy compute, so oversubscribing CPU threads only adds contention with
    # STT/wake-word workers running alongside TTS.
    ORT_INTRA_THREADS: int = _int(
        os.getenv("ORT_INTRA_THREADS"),
        default=(
            _DEFAULT_ORT_INTRA_THREADS_GPU
            if KOKORO_USE_GPU
            else _DEFAULT_ORT_INTRA_THREADS_CPU
        ),
    )
    ORT_INTER_THREADS: int = _int(os.getenv("ORT_INTER_THREADS"), default=1)

    # ── Kokoro ONNX TTS — per-language voice routing ────────────────────────
    KOKORO_VOICE_EN: str = os.getenv("KOKORO_VOICE_EN", "af_heart")
    KOKORO_LANG_EN: str = os.getenv("KOKORO_LANG_EN", "en-us")

    # FIX: KOKORO_SPEED used to be defined but never read anywhere in
    # tts.py (only the _EN/_HI variants were), making it dead config. It
    # is now a genuine base/fallback speed: if a user sets ONLY
    # KOKORO_SPEED in .env, both per-language speeds inherit it
    # automatically. Setting KOKORO_SPEED_EN/KOKORO_SPEED_HI explicitly
    # still overrides this base value, exactly as you'd expect.
    KOKORO_SPEED: float = _float(os.getenv("KOKORO_SPEED"), default=1.0)

    KOKORO_VOICE_HI: str = os.getenv("KOKORO_VOICE_HI", "hf_alpha")
    KOKORO_LANG_HI: str = os.getenv("KOKORO_LANG_HI", "hi")

    KOKORO_SPEED_EN: float = _float(os.getenv("KOKORO_SPEED_EN"), default=KOKORO_SPEED)
    KOKORO_SPEED_HI: float = _float(os.getenv("KOKORO_SPEED_HI"), default=KOKORO_SPEED)

    # ── TTS playback / streaming pipeline tuning ────────────────────────────
    TTS_VOLUME: float = _float(os.getenv("TTS_VOLUME"), default=1.0)
    TTS_PLAYBACK_BUFFER_MS: int = _int(os.getenv("TTS_PLAYBACK_BUFFER_MS"), default=40)
    TTS_SD_LATENCY: str = os.getenv("TTS_SD_LATENCY", "low")
    TTS_WARMUP_WAIT_S: float = _float(os.getenv("TTS_WARMUP_WAIT_S"), default=2.0)
    TTS_SYNTH_QUEUE_SIZE: int = _int(os.getenv("TTS_SYNTH_QUEUE_SIZE"), default=12)
    TTS_PLAY_QUEUE_SIZE: int = _int(os.getenv("TTS_PLAY_QUEUE_SIZE"), default=6)
    TTS_PHRASE_CACHE_SIZE: int = _int(os.getenv("TTS_PHRASE_CACHE_SIZE"), default=64)
    TTS_PHRASE_CACHE_MAXLEN: int = _int(
        os.getenv("TTS_PHRASE_CACHE_MAXLEN"), default=40
    )

    # NEW: was read via getattr(self, "TTS_BLEED_GUARD_MULTIPLIER", 1.6) in
    # stt.py's is_user_speaking() with no definition here.
    TTS_BLEED_GUARD_MULTIPLIER: float = _float(
        os.getenv("TTS_BLEED_GUARD_MULTIPLIER"), default=1.6
    )

    # ── Core ──────────────────────────────────────────────────────────────
    # FIX: default changed True -> False. A production build should be
    # quiet by default; set DEBUG_MODE=true in .env during development.
    DEBUG_MODE: bool = _bool(os.getenv("DEBUG_MODE", "False"), default=False)
    WAKE_WORD: str = os.getenv("WAKE_WORD", "sara , sarah").lower().strip()
    SARA_NAME: str = os.getenv("SARA_NAME", "Sara")
    SARA_TIMEZONE: str = os.getenv("SARA_TIMEZONE", "Asia/Kolkata")
    SARA_LANGUAGE: str = os.getenv("SARA_LANGUAGE", "hinglish").lower().strip()

    # ── Wake word — fallback STT-based multi-variant matching (stt.py) ─────
    WAKE_WORDS: list = [
        w.strip().lower()
        for w in os.getenv("WAKE_WORDS", "sara,sarah,hey sara,hey sarah").split(",")
        if w.strip()
    ]

    # NEW: controls whether validate() force-adds the four built-in wake
    # word variants to a user-supplied WAKE_WORDS list. Default (False)
    # preserves the exact previous behavior (defaults always added) so
    # existing .env files keep working unchanged. Set to True in .env if
    # you want ONLY your custom wake words, with no forced additions.
    WAKE_WORD_ALLOW_CUSTOM_ONLY: bool = _bool(
        os.getenv("WAKE_WORD_ALLOW_CUSTOM_ONLY", "False"), default=False
    )

    # Path to a CUSTOM-TRAINED openwakeword model file/dir for "sara"/
    # "sarah". Leave unset (default) until one is actually trained — a
    # pretrained model such as "hey_jarvis" does NOT recognize "sara" and
    # loading it would make stt.py listen for the wrong word entirely.
    # When this is None, stt.py skips openwakeword and uses the
    # STT-based fallback (WAKE_WORDS above) instead.
    WAKE_WORD_MODEL_PATH: str | None = _optional_str(os.getenv("WAKE_WORD_MODEL_PATH"))

    WAKE_WORD_COOLDOWN_S: float = _float(os.getenv("WAKE_WORD_COOLDOWN_S"), default=2.0)
    WAKE_WORD_THRESHOLD: float = _float(os.getenv("WAKE_WORD_THRESHOLD"), default=0.5)
    # NEW: was read via getattr(cfg, "WAKE_WORD_BEAM_SIZE", 1) in both
    # stt.py (listen(mode="wake")) and gui_main.py's debug log, with no
    # definition here.
    WAKE_WORD_BEAM_SIZE: int = _int(os.getenv("WAKE_WORD_BEAM_SIZE"), default=1)

    # ── Mic settle time after TTS stops (echo / room-decay guard) ──────────
    # Kept as a secondary safety margin even with AEC active below — AEC
    # cancels the correlated echo component in real time, but this still
    # guards against any residual tail immediately after TTS stops.
    STT_SETTLE_MIN_GAP_S: float = _float(os.getenv("STT_SETTLE_MIN_GAP_S"), default=1.3)

    # ── Acoustic Echo Cancellation (AEC) — WebRTC APM (aec-audio-processing) ─
    # Real echo cancellation: the exact audio Sara is playing through the
    # speakers is fed to the processor as a "reverse"/far-end reference
    # stream in real time, and it's subtracted (via WebRTC's adaptive
    # filter) from the microphone's "forward"/near-end stream before STT
    # ever sees it. This replaces trying to guess echo vs. real speech
    # from energy/VAD heuristics alone.
    AEC_ENABLED: bool = _bool(os.getenv("AEC_ENABLED", "True"), default=True)

    # WebRTC APM only accepts 8000 / 16000 / 32000 / 48000 Hz. This MUST
    # match SpeechToText.SAMPLE_RATE (16000) since the near-end/forward
    # stream is the raw mic audio — changing one without the other will
    # silently break AEC's alignment.
    AEC_SAMPLE_RATE: int = _int(os.getenv("AEC_SAMPLE_RATE"), default=16000)

    # How many milliseconds of latency exist between a sample leaving
    # Kokoro's output buffer (speaker) and that same sound arriving back
    # at the microphone. This is a starting estimate (typical Windows
    # sounddevice + speaker/mic round-trip) — if echo still leaks through
    # after AEC is wired in, tune this value up/down in small steps
    # (e.g. 20ms at a time) while testing; too small a delay and AEC's
    # adaptive filter never converges, too large and it converges to the
    # wrong echo path.
    AEC_STREAM_DELAY_MS: int = _int(os.getenv("AEC_STREAM_DELAY_MS"), default=80)

    # Bundled extras from the same WebRTC APM engine. NS (noise
    # suppression) is safe to leave on — it helps Hindi/Hinglish capture
    # in noisy rooms. AGC (automatic gain control) is left OFF by default
    # because it continuously re-scales mic volume, which would fight
    # with SpeechToText's own energy-threshold-based endpointing/wake
    # detection unless that logic is also revisited — enable only if you
    # explicitly want APM to manage mic gain instead.
    AEC_ENABLE_NS: bool = _bool(os.getenv("AEC_ENABLE_NS", "True"), default=True)
    AEC_ENABLE_AGC: bool = _bool(os.getenv("AEC_ENABLE_AGC", "False"), default=False)

    # APM has its own built-in VAD (has_voice()). Sara already has
    # webrtcvad wired directly into stt.py's endpointing logic, so this
    # stays off by default to avoid two independent VADs disagreeing;
    # exposed here in case stt.py is later extended to cross-check both.
    AEC_ENABLE_VAD: bool = _bool(os.getenv("AEC_ENABLE_VAD", "False"), default=False)

    # ── Memory ────────────────────────────────────────────────────────────
    MAX_MEMORY_EXCHANGES: int = _int(os.getenv("MAX_MEMORY_EXCHANGES"), default=6)

    # ── Language detection ────────────────────────────────────────────────
    LANG_DETECTION_MODE: str = os.getenv("LANG_DETECTION_MODE", "auto").lower().strip()
    STT_LANGUAGE: str | None = _optional_str(os.getenv("STT_LANGUAGE"))

    # NEW: was read via getattr(cfg, "STT_FORCE_LANG_FOR_HINGLISH", True)
    # in stt.py's _resolve_forced_language() with no definition here.
    # When SARA_LANGUAGE is "hindi" or "hinglish", this forces Whisper's
    # language parameter to "hi" for transcription (see stt.py for the
    # full reasoning). Kept True by default to preserve existing behavior.
    STT_FORCE_LANG_FOR_HINGLISH: bool = _bool(
        os.getenv("STT_FORCE_LANG_FOR_HINGLISH", "True"), default=True
    )

    # ── Whisper transcription tuning ─────────────────────────────────────
    # NEW: all of the settings in this block were previously read via
    # getattr(Config, "X", default) in stt.py's _load_faster_whisper() /
    # _transcribe() with NO matching definition here — meaning none of
    # them were actually configurable via .env before this fix, despite
    # looking like they should be.
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "large-v3")
    WHISPER_BEAM_SIZE: int = _int(os.getenv("WHISPER_BEAM_SIZE"), default=3)
    STT_NO_SPEECH_THRESHOLD: float = _float(
        os.getenv("STT_NO_SPEECH_THRESHOLD"), default=0.6
    )
    STT_LOG_PROB_THRESHOLD: float = _float(
        os.getenv("STT_LOG_PROB_THRESHOLD"), default=-1.0
    )
    STT_COMPRESSION_RATIO_THRESHOLD: float = _float(
        os.getenv("STT_COMPRESSION_RATIO_THRESHOLD"), default=2.4
    )
    STT_HALLUCINATION_MIN_REPEATS: int = _int(
        os.getenv("STT_HALLUCINATION_MIN_REPEATS"), default=3
    )

    # ── Barge-in ──────────────────────────────────────────────────────────
    BARGE_IN_ENABLED: bool = _bool(os.getenv("BARGE_IN_ENABLED", "True"), default=True)
    BARGE_IN_ENERGY_THRESHOLD: int = _int(
        os.getenv("BARGE_IN_ENERGY_THRESHOLD"), default=600
    )

    # ── Continuous mode ───────────────────────────────────────────────────
    CONTINUOUS_MODE_TIMEOUT: int = _int(
        os.getenv("CONTINUOUS_MODE_TIMEOUT"), default=180
    )

    # ── Vision ────────────────────────────────────────────────────────────
    VISION_MODEL: str = os.getenv("VISION_MODEL", "gemini-2.5-flash")

    # ── Reminders ─────────────────────────────────────────────────────────
    REMINDER_CHECK_INTERVAL: int = _int(os.getenv("REMINDER_CHECK_INTERVAL"), default=5)

    # ── Proactive Engine (sara/orchestrator/proactive.py) ────────────────────
    # Sara periodically checks a few passive signals (battery, upcoming
    # reminders, how long since the last conversation turn) and speaks up
    # on her own — without a wake word — when one of them crosses a
    # threshold. Each trigger has its own cooldown so the same nudge can't
    # repeat before PROACTIVE_COOLDOWN_MINUTES have passed. Always silenced
    # while focus_mode is on or the assistant is paused (Home page
    # Pause/Resume Listening), and can be turned off entirely from the
    # Settings page (defaults ON — see get_ui_settings()/update_setting()
    # in sara/gui/app/settings.py, "setting:proactive_mode").
    PROACTIVE_ENABLED: bool = _bool(os.getenv("PROACTIVE_ENABLED", "True"), default=True)
    # How often (seconds) the background thread re-checks all signals.
    PROACTIVE_CHECK_INTERVAL_S: int = _int(
        os.getenv("PROACTIVE_CHECK_INTERVAL_S"), default=60
    )
    # Battery-low trigger: fires once per cooldown window while on battery
    # power (not charging) and at/under this percentage.
    PROACTIVE_BATTERY_LOW_PERCENT: int = _int(
        os.getenv("PROACTIVE_BATTERY_LOW_PERCENT"), default=15
    )
    # Reminder heads-up trigger: gives a spoken heads-up this many minutes
    # before a reminder is actually due (in addition to the existing
    # on-time alarm in sara/tools/reminders.py — this is additive, not a
    # replacement).
    PROACTIVE_REMINDER_LEAD_MINUTES: int = _int(
        os.getenv("PROACTIVE_REMINDER_LEAD_MINUTES"), default=15
    )
    # Idle/break trigger: fires if this many minutes have passed since the
    # last conversation turn (wake + at least one exchange), suggesting a
    # short break.
    PROACTIVE_IDLE_BREAK_MINUTES: int = _int(
        os.getenv("PROACTIVE_IDLE_BREAK_MINUTES"), default=90
    )
    # Shared cooldown: minimum minutes between two firings of the SAME
    # trigger (different triggers don't share this cooldown with each other).
    PROACTIVE_COOLDOWN_MINUTES: int = _int(
        os.getenv("PROACTIVE_COOLDOWN_MINUTES"), default=30
    )
    # If True, asks the LLM (brain.generate_response) to phrase each nudge
    # naturally/bilingually instead of using the fixed template string.
    # Falls back to the template automatically if the LLM isn't ready yet,
    # is disabled, or the call fails/times out for any reason — this must
    # never block or crash the proactive thread.
    PROACTIVE_LLM_PHRASING: bool = _bool(
        os.getenv("PROACTIVE_LLM_PHRASING", "True"), default=True
    )

    # ── Skills (sara/skills/) ─────────────────────────────────────────────
    # Daily Briefing (sara/skills/daily_briefing.py): combines weather +
    # today's reminders + a news headline into one spoken summary. No
    # per-user city setting exists yet for voice queries (only the GUI
    # weather card's WEATHER_CITY in sara/gui/app/helpers.py, which this
    # intentionally does NOT touch/reuse, to avoid coupling a skill module
    # to the GUI layer) — same default city, kept independently so each
    # can be changed without affecting the other.
    DAILY_BRIEFING_LOCATION: str = os.getenv("DAILY_BRIEFING_LOCATION", "Ajmer,IN")

    # Notes Q&A (sara/skills/notes_qa.py): lets Sara answer questions from
    # .txt/.md class-notes files dropped into NOTES_FOLDER, via the
    # existing (previously unused) sara/core/rag.py LongTermMemory vector
    # store. Files are chunked to roughly NOTES_CHUNK_CHARS characters
    # each before embedding.
    NOTES_FOLDER: str = os.getenv("NOTES_FOLDER") or str(_PROJECT_ROOT / "sara_class_notes")
    NOTES_CHUNK_CHARS: int = _int(os.getenv("NOTES_CHUNK_CHARS"), default=800)
    NOTES_QA_TOP_K: int = _int(os.getenv("NOTES_QA_TOP_K"), default=4)
    # Safety cap: protects startup from a pathologically large single file
    # (e.g. an entire textbook accidentally dropped in) turning into
    # thousands of blocking embedding calls. Extra content beyond this
    # many chunks per file is simply skipped, not an error.
    NOTES_MAX_CHUNKS_PER_FILE: int = _int(
        os.getenv("NOTES_MAX_CHUNKS_PER_FILE"), default=200
    )

    # ── Personality: festival-aware greeting ─────────────────────────────
    # Fixed-date national days (Republic Day, Independence Day, New Year,
    # Teachers' Day, Gandhi Jayanti) are the same date every year, so
    # they're safe to hardcode in sara/orchestrator/core_wiring.py's
    # greeting logic directly. Diwali and Holi follow the lunar calendar
    # and shift every year — hardcoding a fixed month/day for them would
    # eventually greet "Happy Diwali" on a random unrelated day, which is
    # worse than not having the greeting at all. Instead they're set here,
    # once a year, as plain "YYYY-MM-DD" strings (leave blank to disable
    # — blank is the default here since no fixed date was provided):
    #   DIWALI_DATE=2026-11-08
    #   HOLI_DATE=2026-03-03
    DIWALI_DATE: str = os.getenv("DIWALI_DATE", "")
    HOLI_DATE: str = os.getenv("HOLI_DATE", "")

    # ── RAG / long-term semantic memory (sara/core/rag.py) ──────────────────
    # Uses Ollama's own /api/embeddings endpoint — no extra heavy ML
    # dependency, reuses the Ollama server Sara already depends on for chat.
    # "nomic-embed-text" is a small (~270MB), fast, well-regarded local
    # embedding model — pull it once with `ollama pull nomic-embed-text`.
    RAG_ENABLED: bool = _bool(os.getenv("RAG_ENABLED", "True"), default=True)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    EMBEDDING_TIMEOUT_S: float = _float(os.getenv("EMBEDDING_TIMEOUT_S"), default=4.0)
    # How many past memories to retrieve and inject into the LLM's context
    # per turn, and the minimum cosine-similarity score (0-1) a memory must
    # clear to be considered relevant enough to include at all.
    RAG_TOP_K: int = _int(os.getenv("RAG_TOP_K"), default=4)
    RAG_MIN_SIMILARITY: float = _float(os.getenv("RAG_MIN_SIMILARITY"), default=0.55)
    # Caps how many memory rows are loaded into the in-memory similarity
    # matrix at startup (most recent N) — bounds RAM use on a very
    # long-running install without needing a real vector DB dependency.
    RAG_MAX_IN_MEMORY: int = _int(os.getenv("RAG_MAX_IN_MEMORY"), default=5000)

    # ── LLM tool-calling (sara/core/tool_router.py) ──────────────────────────
    # When the fast regex-based intent.py finds no match at all, this makes
    # ONE extra bounded-time LLM call (structured function-calling, not a
    # free-form chat reply) to see if a known tool genuinely applies before
    # falling back to a purely conversational reply — catches natural
    # phrasing that intent.py's ~100 hand-written patterns don't cover
    # ("could you check what the weather's doing in Jaipur" vs the rigid
    # "weather in <x>" pattern), without replacing or slowing down the
    # existing fast path for anything intent.py already matches directly.
    TOOL_CALLING_ENABLED: bool = _bool(
        os.getenv("TOOL_CALLING_ENABLED", "True"), default=True
    )
    TOOL_CALLING_TIMEOUT_S: float = _float(
        os.getenv("TOOL_CALLING_TIMEOUT_S"), default=5.0
    )

    # ── Shared file paths (CWD-independent — resolved from this file's own
    # location, i.e. the project root, NOT os.getcwd()) ─────────────────────
    # PRODUCTION-AUDIT FIX: database.py, reminders.py, and system.py each
    # used to compute their own path to the shared SQLite DB / notes file
    # independently via os.getcwd(), which meant launching the app from a
    # different working directory could point different modules at
    # different physical files. All modules should now import DB_PATH /
    # NOTES_FILE_PATH from here instead of computing their own.
    # BUGFIX: .env.example ships DB_PATH= and NOTES_FILE_PATH= with empty
    # values (as placeholders to fill in) — os.getenv("X", default) only
    # falls back to `default` when the variable is completely UNSET, not
    # when it's set-but-empty. So anyone who copied .env.example -> .env
    # without filling these in got DB_PATH="" silently, which pointed
    # PreferencesDB at a bogus empty path (created with no schema) instead
    # of the real sara_data.db — surfacing later as "no such table:
    # preferences" / "no such table: conversation_log" errors. `or` here
    # treats both "unset" and "set to empty string" as "use the default".
    DB_PATH: str = os.getenv("DB_PATH") or str(_PROJECT_ROOT / "sara_data.db")
    NOTES_FILE_PATH: str = os.getenv("NOTES_FILE_PATH") or str(
        _PROJECT_ROOT / "sara_notes.txt"
    )

    @classmethod
    def validate(cls, force: bool = False) -> None:
        # Idempotency guard — see ConfigError docstring at the top of this
        # file for why. Running validate() more than once (e.g. from a
        # test) is now a safe no-op unless force=True is passed explicitly.
        if cls._validated and not force:
            return

        # ── LLM backend ───────────────────────────────────────────────────
        if cls.LLM_BACKEND == "gemini":
            if not cls.GEMINI_API_KEY or cls.GEMINI_API_KEY in (
                "",
                "your_api_key_here",
            ):
                raise ConfigError(
                    "LLM_BACKEND is 'gemini' but GEMINI_API_KEY is missing. "
                    "Set GEMINI_API_KEY in your .env file, or switch "
                    "LLM_BACKEND to 'ollama' to use a local model instead."
                )
        if cls.LLM_BACKEND not in ("ollama", "gemini"):
            print(
                f"[Warning] Unknown LLM_BACKEND '{cls.LLM_BACKEND}', defaulting to 'ollama'."
            )
            cls.LLM_BACKEND = "ollama"

        # ── LLM retry / warm-up clamps ─────────────────────────────────────
        cls.LLM_MAX_RETRIES = max(
            _MIN_LLM_RETRIES, min(_MAX_LLM_RETRIES, cls.LLM_MAX_RETRIES)
        )
        cls.LLM_RETRY_BASE_DELAY_S = max(
            _MIN_LLM_RETRY_DELAY_S,
            min(_MAX_LLM_RETRY_DELAY_S, cls.LLM_RETRY_BASE_DELAY_S),
        )
        cls.LLM_RETRY_MAX_DELAY_S = max(
            cls.LLM_RETRY_BASE_DELAY_S,
            min(_MAX_LLM_RETRY_DELAY_S, cls.LLM_RETRY_MAX_DELAY_S),
        )
        cls.LLM_WARMUP_WAIT_S = max(
            _MIN_LLM_WARMUP_WAIT_S, min(_MAX_LLM_WARMUP_WAIT_S, cls.LLM_WARMUP_WAIT_S)
        )
        cls.GEMINI_MAX_HISTORY_TOKENS = max(
            _MIN_GEMINI_HISTORY_TOKENS,
            min(_MAX_GEMINI_HISTORY_TOKENS, cls.GEMINI_MAX_HISTORY_TOKENS),
        )

        # ── Kokoro model files ────────────────────────────────────────────
        if not Path(cls.KOKORO_MODEL_PATH).exists():
            print(f"[Warning] Kokoro model not found at '{cls.KOKORO_MODEL_PATH}'.")
        if not Path(cls.KOKORO_VOICES_PATH).exists():
            print(
                f"[Warning] Kokoro voices file not found at '{cls.KOKORO_VOICES_PATH}'."
            )

        # ── GPU / ONNX Runtime clamps ───────────────────────────────────────
        cls.CUDA_GPU_MEM_LIMIT_BYTES = max(
            int(_MIN_CUDA_MEM_LIMIT_GB * _BYTES_PER_GB),
            min(
                int(_MAX_CUDA_MEM_LIMIT_GB * _BYTES_PER_GB),
                cls.CUDA_GPU_MEM_LIMIT_BYTES,
            ),
        )
        cls.ORT_INTRA_THREADS = max(
            _MIN_THREADS, min(_MAX_THREADS, cls.ORT_INTRA_THREADS)
        )
        cls.ORT_INTER_THREADS = max(
            _MIN_THREADS, min(_MAX_THREADS, cls.ORT_INTER_THREADS)
        )

        # ── Kokoro / TTS pipeline clamps ──────────────────────────────────
        cls.TTS_VOLUME = max(_MIN_TTS_VOLUME, min(_MAX_TTS_VOLUME, cls.TTS_VOLUME))

        cls.KOKORO_SPEED = max(
            _MIN_KOKORO_SPEED,
            min(_MAX_KOKORO_SPEED, cls.KOKORO_SPEED),
        )
        cls.KOKORO_SPEED_EN = max(
            _MIN_KOKORO_SPEED, min(_MAX_KOKORO_SPEED, cls.KOKORO_SPEED_EN)
        )
        cls.KOKORO_SPEED_HI = max(
            _MIN_KOKORO_SPEED, min(_MAX_KOKORO_SPEED, cls.KOKORO_SPEED_HI)
        )
        cls.TTS_PLAYBACK_BUFFER_MS = max(
            _MIN_PLAYBACK_BUFFER_MS,
            min(_MAX_PLAYBACK_BUFFER_MS, cls.TTS_PLAYBACK_BUFFER_MS),
        )
        cls.TTS_WARMUP_WAIT_S = max(
            _MIN_WARMUP_WAIT_S, min(_MAX_WARMUP_WAIT_S, cls.TTS_WARMUP_WAIT_S)
        )
        cls.TTS_SYNTH_QUEUE_SIZE = max(
            _MIN_QUEUE_SIZE, min(_MAX_QUEUE_SIZE, cls.TTS_SYNTH_QUEUE_SIZE)
        )
        cls.TTS_PLAY_QUEUE_SIZE = max(
            _MIN_QUEUE_SIZE, min(_MAX_QUEUE_SIZE, cls.TTS_PLAY_QUEUE_SIZE)
        )
        cls.TTS_PHRASE_CACHE_SIZE = max(
            _MIN_PHRASE_CACHE_SIZE,
            min(_MAX_PHRASE_CACHE_SIZE, cls.TTS_PHRASE_CACHE_SIZE),
        )
        cls.TTS_PHRASE_CACHE_MAXLEN = max(
            _MIN_PHRASE_CACHE_MAXLEN,
            min(_MAX_PHRASE_CACHE_MAXLEN, cls.TTS_PHRASE_CACHE_MAXLEN),
        )
        cls.TTS_BLEED_GUARD_MULTIPLIER = max(
            _MIN_TTS_BLEED_MULTIPLIER,
            min(_MAX_TTS_BLEED_MULTIPLIER, cls.TTS_BLEED_GUARD_MULTIPLIER),
        )

        # ── Whisper transcription clamps ──────────────────────────────────
        cls.WHISPER_BEAM_SIZE = max(
            _MIN_WHISPER_BEAM_SIZE, min(_MAX_WHISPER_BEAM_SIZE, cls.WHISPER_BEAM_SIZE)
        )
        cls.STT_NO_SPEECH_THRESHOLD = max(
            _MIN_NO_SPEECH_THRESHOLD,
            min(_MAX_NO_SPEECH_THRESHOLD, cls.STT_NO_SPEECH_THRESHOLD),
        )
        cls.STT_LOG_PROB_THRESHOLD = max(
            _MIN_LOG_PROB_THRESHOLD,
            min(_MAX_LOG_PROB_THRESHOLD, cls.STT_LOG_PROB_THRESHOLD),
        )
        cls.STT_COMPRESSION_RATIO_THRESHOLD = max(
            _MIN_COMPRESSION_RATIO_THRESHOLD,
            min(_MAX_COMPRESSION_RATIO_THRESHOLD, cls.STT_COMPRESSION_RATIO_THRESHOLD),
        )
        cls.STT_HALLUCINATION_MIN_REPEATS = max(
            _MIN_HALLUCINATION_MIN_REPEATS,
            min(_MAX_HALLUCINATION_MIN_REPEATS, cls.STT_HALLUCINATION_MIN_REPEATS),
        )
        cls.WAKE_WORD_BEAM_SIZE = max(
            _MIN_WHISPER_BEAM_SIZE, min(_MAX_WHISPER_BEAM_SIZE, cls.WAKE_WORD_BEAM_SIZE)
        )

        # ── Language / SARA ───────────────────────────────────────────────
        if cls.LANG_DETECTION_MODE not in ("auto", "manual"):
            print(
                f"[Warning] Unknown LANG_DETECTION_MODE '{cls.LANG_DETECTION_MODE}', defaulting to 'auto'."
            )
            cls.LANG_DETECTION_MODE = "auto"

        if cls.LANG_DETECTION_MODE == "manual" and not cls.STT_LANGUAGE:
            print(
                "[Warning] LANG_DETECTION_MODE='manual' but STT_LANGUAGE unset; falling back to 'auto'."
            )
            cls.LANG_DETECTION_MODE = "auto"

        valid_langs = ("english", "hindi", "hinglish")
        if cls.SARA_LANGUAGE not in valid_langs:
            print(
                f"[Warning] Unknown SARA_LANGUAGE '{cls.SARA_LANGUAGE}', defaulting to 'hinglish'."
            )
            cls.SARA_LANGUAGE = "hinglish"

        # ── Wake word ─────────────────────────────────────────────────────
        if not cls.WAKE_WORD:
            print("[Warning] WAKE_WORD is empty, defaulting to 'sara'.")
            cls.WAKE_WORD = "sara"

        if not cls.WAKE_WORDS:
            print("[Warning] WAKE_WORDS is empty, defaulting to sara/sarah variants.")
            cls.WAKE_WORDS = ["sara", "sarah", "hey sara", "hey sarah"]
        elif not cls.WAKE_WORD_ALLOW_CUSTOM_ONLY:
            # Preserves the original behavior exactly: the four built-in
            # variants are always present unless the user opted out via
            # WAKE_WORD_ALLOW_CUSTOM_ONLY=true.
            for must in ("sara", "sarah", "hey sara", "hey sarah"):
                if must not in cls.WAKE_WORDS:
                    cls.WAKE_WORDS.append(must)

        if cls.WAKE_WORD_MODEL_PATH and not Path(cls.WAKE_WORD_MODEL_PATH).exists():
            print(
                f"[Warning] WAKE_WORD_MODEL_PATH '{cls.WAKE_WORD_MODEL_PATH}' does not exist; "
                f"falling back to STT-based wake detection."
            )
            cls.WAKE_WORD_MODEL_PATH = None

        cls.WAKE_WORD_COOLDOWN_S = max(0.5, min(10.0, cls.WAKE_WORD_COOLDOWN_S))
        cls.WAKE_WORD_THRESHOLD = max(0.1, min(0.99, cls.WAKE_WORD_THRESHOLD))

        # ── STT settle-gap clamp ─────────────────────────────────────────
        cls.STT_SETTLE_MIN_GAP_S = max(
            _MIN_STT_SETTLE_GAP_S, min(_MAX_STT_SETTLE_GAP_S, cls.STT_SETTLE_MIN_GAP_S)
        )

        # ── AEC clamps ──────────────────────────────────────────────────
        if cls.AEC_SAMPLE_RATE not in _AEC_VALID_SAMPLE_RATES:
            print(
                f"[Warning] AEC_SAMPLE_RATE {cls.AEC_SAMPLE_RATE} is not one of "
                f"{_AEC_VALID_SAMPLE_RATES} (WebRTC APM requirement); defaulting to 16000."
            )
            cls.AEC_SAMPLE_RATE = 16000
        cls.AEC_STREAM_DELAY_MS = max(
            _MIN_AEC_STREAM_DELAY_MS,
            min(_MAX_AEC_STREAM_DELAY_MS, cls.AEC_STREAM_DELAY_MS),
        )

        # ── Memory / context clamps ────────────────────────────────────────
        cls.MAX_MEMORY_EXCHANGES = max(1, min(20, cls.MAX_MEMORY_EXCHANGES))
        cls.OLLAMA_NUM_CTX = max(256, cls.OLLAMA_NUM_CTX)
        if cls.OLLAMA_SUMMARY_NUM_CTX < cls.OLLAMA_NUM_CTX:
            cls.OLLAMA_SUMMARY_NUM_CTX = cls.OLLAMA_NUM_CTX

        # ── RAG / long-term memory clamps ──────────────────────────────────
        cls.EMBEDDING_TIMEOUT_S = max(1.0, min(15.0, cls.EMBEDDING_TIMEOUT_S))
        cls.RAG_TOP_K = max(1, min(20, cls.RAG_TOP_K))
        cls.RAG_MIN_SIMILARITY = max(0.0, min(1.0, cls.RAG_MIN_SIMILARITY))
        cls.RAG_MAX_IN_MEMORY = max(100, min(50_000, cls.RAG_MAX_IN_MEMORY))

        # ── Tool-calling clamps ─────────────────────────────────────────────
        cls.TOOL_CALLING_TIMEOUT_S = max(1.0, min(15.0, cls.TOOL_CALLING_TIMEOUT_S))

        # ── Proactive Engine clamps ─────────────────────────────────────────
        # A too-small check interval would turn the background thread into
        # a near-busy-loop; a too-small cooldown would make nudges spammy.
        cls.PROACTIVE_CHECK_INTERVAL_S = max(5, min(3600, cls.PROACTIVE_CHECK_INTERVAL_S))
        cls.PROACTIVE_BATTERY_LOW_PERCENT = max(
            1, min(90, cls.PROACTIVE_BATTERY_LOW_PERCENT)
        )
        cls.PROACTIVE_REMINDER_LEAD_MINUTES = max(
            1, min(180, cls.PROACTIVE_REMINDER_LEAD_MINUTES)
        )
        cls.PROACTIVE_IDLE_BREAK_MINUTES = max(
            5, min(600, cls.PROACTIVE_IDLE_BREAK_MINUTES)
        )
        cls.PROACTIVE_COOLDOWN_MINUTES = max(1, min(240, cls.PROACTIVE_COOLDOWN_MINUTES))

        # ── Notes Q&A clamps ─────────────────────────────────────────────────
        # A too-small chunk size would explode a modest notes folder into
        # thousands of embedding calls at startup.
        cls.NOTES_CHUNK_CHARS = max(200, min(4000, cls.NOTES_CHUNK_CHARS))
        cls.NOTES_QA_TOP_K = max(1, min(20, cls.NOTES_QA_TOP_K))
        cls.NOTES_MAX_CHUNKS_PER_FILE = max(1, min(5000, cls.NOTES_MAX_CHUNKS_PER_FILE))

        cls._validated = True

        # ── Debug print ───────────────────────────────────────────────────
        if cls.DEBUG_MODE:
            backend_detail = (
                f"model='{cls.OLLAMA_MODEL}'"
                if cls.LLM_BACKEND == "ollama"
                else f"model='{cls.GEMINI_MODEL}'"
            )
            active_provider = "CPUExecutionProvider"
            if (
                cls.KOKORO_USE_GPU
                and "CUDAExecutionProvider" in _ORT_AVAILABLE_PROVIDERS
            ):
                active_provider = "CUDAExecutionProvider"
            providers_display = (
                _ORT_AVAILABLE_PROVIDERS
                if _ORT_AVAILABLE_PROVIDERS
                else "onnxruntime not installed"
            )

            print(
                f"[Debug] LLM backend  : {cls.LLM_BACKEND.upper()} ({backend_detail})"
            )
            print(
                f"[Debug] LLM retries  : max={cls.LLM_MAX_RETRIES} base_delay={cls.LLM_RETRY_BASE_DELAY_S}s "
                f"max_delay={cls.LLM_RETRY_MAX_DELAY_S}s warmup_wait={cls.LLM_WARMUP_WAIT_S}s"
            )
            print(
                f"[Debug] Wake words   : {cls.WAKE_WORDS} | model={'custom (' + cls.WAKE_WORD_MODEL_PATH + ')' if cls.WAKE_WORD_MODEL_PATH else 'none — STT fallback'} "
                f"| custom_only={cls.WAKE_WORD_ALLOW_CUSTOM_ONLY} | beam={cls.WAKE_WORD_BEAM_SIZE}"
            )
            print(
                f"[Debug] Wake cooldown: {cls.WAKE_WORD_COOLDOWN_S}s | threshold={cls.WAKE_WORD_THRESHOLD}"
            )
            print(f"[Debug] STT settle   : {cls.STT_SETTLE_MIN_GAP_S}s")
            print(
                f"[Debug] Whisper      : model={cls.WHISPER_MODEL_SIZE} beam={cls.WHISPER_BEAM_SIZE} "
                f"no_speech_thr={cls.STT_NO_SPEECH_THRESHOLD} log_prob_thr={cls.STT_LOG_PROB_THRESHOLD} "
                f"compression_thr={cls.STT_COMPRESSION_RATIO_THRESHOLD} halluc_repeats={cls.STT_HALLUCINATION_MIN_REPEATS}"
            )
            print(
                f"[Debug] AEC          : enabled={cls.AEC_ENABLED} | rate={cls.AEC_SAMPLE_RATE}Hz | "
                f"delay={cls.AEC_STREAM_DELAY_MS}ms | ns={cls.AEC_ENABLE_NS} | agc={cls.AEC_ENABLE_AGC} | vad={cls.AEC_ENABLE_VAD}"
            )
            print(
                f"[Debug] TTS engine   : Kokoro ONNX (GPU requested={cls.KOKORO_USE_GPU})"
            )
            print(
                f"[Debug] ORT provider : active={active_provider} | available={providers_display}"
            )
            print(f"[Debug] Kokoro model : {cls.KOKORO_MODEL_PATH}")
            print(f"[Debug] Kokoro voices: {cls.KOKORO_VOICES_PATH}")
            print(f"[Debug] Kokoro base speed: {cls.KOKORO_SPEED}")
            print(
                f"[Debug] Kokoro EN    : voice={cls.KOKORO_VOICE_EN} lang={cls.KOKORO_LANG_EN} speed={cls.KOKORO_SPEED_EN}"
            )
            print(
                f"[Debug] Kokoro HI    : voice={cls.KOKORO_VOICE_HI} lang={cls.KOKORO_LANG_HI} speed={cls.KOKORO_SPEED_HI}"
            )
            print(
                f"[Debug] ORT threads  : intra={cls.ORT_INTRA_THREADS} inter={cls.ORT_INTER_THREADS}"
            )
            print(
                f"[Debug] CUDA mem cap : {cls.CUDA_GPU_MEM_LIMIT_BYTES / _BYTES_PER_GB:.2f} GB"
            )
            print(
                f"[Debug] TTS queues   : synth={cls.TTS_SYNTH_QUEUE_SIZE} play={cls.TTS_PLAY_QUEUE_SIZE}"
            )
            print(
                f"[Debug] TTS buffer   : {cls.TTS_PLAYBACK_BUFFER_MS}ms | warmup wait={cls.TTS_WARMUP_WAIT_S}s"
            )
            print(f"[Debug] TTS bleed x  : {cls.TTS_BLEED_GUARD_MULTIPLIER}")
            print(
                f"[Debug] Phrase cache : size={cls.TTS_PHRASE_CACHE_SIZE} maxlen={cls.TTS_PHRASE_CACHE_MAXLEN}"
            )
            print(f"[Debug] Memory       : {cls.MAX_MEMORY_EXCHANGES} exchanges")
            print(
                f"[Debug] Lang detect  : {cls.LANG_DETECTION_MODE}"
                + (f" (locked to '{cls.STT_LANGUAGE}')" if cls.STT_LANGUAGE else "")
            )
            print(
                f"[Debug] Sara lang    : {cls.SARA_LANGUAGE} | force_hi_for_hinglish={cls.STT_FORCE_LANG_FOR_HINGLISH}"
            )
            print(f"[Debug] DB path      : {cls.DB_PATH}")
            print(f"[Debug] Notes path   : {cls.NOTES_FILE_PATH}")
            print(
                f"[Debug] RAG memory   : enabled={cls.RAG_ENABLED} | model={cls.EMBEDDING_MODEL} | "
                f"top_k={cls.RAG_TOP_K} | min_sim={cls.RAG_MIN_SIMILARITY} | "
                f"timeout={cls.EMBEDDING_TIMEOUT_S}s | max_in_ram={cls.RAG_MAX_IN_MEMORY}"
            )
            print(
                f"[Debug] Tool calling : enabled={cls.TOOL_CALLING_ENABLED} | "
                f"timeout={cls.TOOL_CALLING_TIMEOUT_S}s"
            )
            print(
                f"[Debug] Proactive    : enabled={cls.PROACTIVE_ENABLED} | "
                f"every={cls.PROACTIVE_CHECK_INTERVAL_S}s | "
                f"battery<={cls.PROACTIVE_BATTERY_LOW_PERCENT}% | "
                f"reminder_lead={cls.PROACTIVE_REMINDER_LEAD_MINUTES}m | "
                f"idle_break={cls.PROACTIVE_IDLE_BREAK_MINUTES}m | "
                f"cooldown={cls.PROACTIVE_COOLDOWN_MINUTES}m"
            )
            print(
                f"[Debug] Notes Q&A    : folder={cls.NOTES_FOLDER} | "
                f"chunk_chars={cls.NOTES_CHUNK_CHARS} | top_k={cls.NOTES_QA_TOP_K}"
            )
            # FIX: these two lines used to sit OUTSIDE the `if cls.DEBUG_MODE:`
            # block above (a separate `if cls.LLM_BACKEND == "ollama":` check),
            # meaning they printed even when DEBUG_MODE was False. Now that
            # DEBUG_MODE defaults to False for production quietness, these
            # are moved inside the same debug-gated block for consistency —
            # a production run should print nothing at all by default.
            if cls.LLM_BACKEND == "ollama":
                print(
                    f"[Debug] Ollama ctx   : {cls.OLLAMA_NUM_CTX} tokens (summary: {cls.OLLAMA_SUMMARY_NUM_CTX})"
                )
                print(f"[Debug] Ollama keep  : {cls.OLLAMA_KEEP_ALIVE}")


Config.validate()
