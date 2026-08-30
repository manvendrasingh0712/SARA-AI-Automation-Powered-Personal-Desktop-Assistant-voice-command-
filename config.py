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
8. NEW (multi-step planning engine): added PLANNING_* settings backing
   sara/core/planning/ (the bounded multi-step tool-chaining planner) and
   APP_LAUNCH_ALLOWLIST* settings backing the open_app/close_app/open_url
   security hardening in sara/core/planning/schema.py. Both blocks follow
   this file's existing typed-attribute + clamp + debug-print convention
   exactly, and both are fully backward compatible: every new setting has
   a safe default, so an existing .env file with none of these keys set
   continues to work identically to before this revision.
9. NEW (memory management / decision memory / memory consolidation):
   added MEMORY_CONSOLIDATION_* settings backing the periodic background
   long-term-fact extractor (sara/core/memory_consolidation.py) and
   MEMORY_FORGET_MATCH_THRESHOLD backing the "forget that I like X" voice
   intent's fuzzy-match confidence gate
   (sara/orchestrator/intent_handlers.py). Decision memory itself (the
   new decision_log table in sara/core/memory.py) has no config gate —
   it's an always-on, lightweight append-only log, the same as
   proactive_log. Same backward-compatible convention as #8: every new
   key has a safe default, so an existing .env file is unaffected.
10. NEW (file notifications / emergency-stop hotkey): added
    NOTIFICATIONS_* settings backing the background download-completion
    watcher (sara/orchestrator/notifications.py) and EMERGENCY_STOP_*
    settings backing the global panic-button hotkey
    (sara/orchestrator/emergency_stop.py). Same backward-compatible
    convention as #8/#9 above: every new key has a safe default, so an
    existing .env file with none of these keys set continues to work
    identically to before this revision.
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

# ── New bounds for low-confidence destructive-action confirmation (NEW) ──
_MIN_STT_CONFIDENCE_CONFIRM_THRESHOLD = 0.0
_MAX_STT_CONFIDENCE_CONFIRM_THRESHOLD = 1.0

_MIN_TTS_BLEED_MULTIPLIER = 1.0
_MAX_TTS_BLEED_MULTIPLIER = 5.0

_MIN_OLLAMA_TOP_K = 1
_MAX_OLLAMA_TOP_K = 200

_MIN_OLLAMA_TOP_P = 0.0
_MAX_OLLAMA_TOP_P = 1.0

_MIN_OLLAMA_REPEAT_PENALTY = 1.0
_MAX_OLLAMA_REPEAT_PENALTY = 2.0

_MIN_TTS_FIRST_CHUNK_CHARS = 1
_MAX_TTS_FIRST_CHUNK_CHARS = 100

_MIN_TTS_FIRST_CHUNK_SOFT_CHARS = 1
_MAX_TTS_FIRST_CHUNK_SOFT_CHARS = 300

_MIN_LLM_RETRIES = 0
_MAX_LLM_RETRIES = 10

_MIN_LLM_RETRY_DELAY_S = 0.1
_MAX_LLM_RETRY_DELAY_S = 30.0

_MIN_LLM_WARMUP_WAIT_S = 0.0
_MAX_LLM_WARMUP_WAIT_S = 120.0

_MIN_GEMINI_HISTORY_TOKENS = 1_000
_MAX_GEMINI_HISTORY_TOKENS = 200_000

# ── New bounds for the multi-step planning engine ───────────────────────
_MIN_PLANNING_MAX_STEPS = 2
_MAX_PLANNING_MAX_STEPS = 8

_MIN_PLANNING_STEP_TIMEOUT_S = 1.0
_MAX_PLANNING_STEP_TIMEOUT_S = 15.0

_MIN_PLANNING_TOTAL_TIMEOUT_S = 2.0
_MAX_PLANNING_TOTAL_TIMEOUT_S = 30.0

# ── New bounds for memory management (decision memory / consolidation) ───
_MIN_MEMORY_CONSOLIDATION_INTERVAL_S = 60
_MAX_MEMORY_CONSOLIDATION_INTERVAL_S = 86400

_MIN_MEMORY_CONSOLIDATION_BATCH_SIZE = 4
_MAX_MEMORY_CONSOLIDATION_BATCH_SIZE = 200

_MIN_MEMORY_CONSOLIDATION_MAX_FACTS = 1
_MAX_MEMORY_CONSOLIDATION_MAX_FACTS = 10

# ── Default application allowlist (used when APP_LAUNCH_ALLOWLIST is
# unset/empty in .env) — common everyday Windows desktop apps. Extend via
# .env, never edit this default list to add a one-off app for a single
# install.
_DEFAULT_APP_LAUNCH_ALLOWLIST = (
    "chrome,firefox,edge,brave,opera,"
    "notepad,notepad++,wordpad,"
    "calculator,calc,"
    "spotify,"
    "vscode,code,visual studio code,"
    "explorer,file explorer,"
    "word,excel,powerpoint,outlook,onenote,"
    "paint,paint3d,"
    "discord,slack,teams,zoom,skype,"
    "steam,epic games,"
    "vlc,windows media player,"
    "task manager,taskmgr,"
    "settings,control panel,"
    "terminal,command prompt,cmd,powershell,windows terminal,"
    "whatsapp,telegram"
)


WAKE_ACK_PHRASE = "Yes?"


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
    # Lower than Ollama's ~0.8 default on purpose: small (4B) models drift off
    # the requested output language / persona far more at high temperature.
    # 0.4 keeps replies noticeably more consistent (language, tone, format)
    # while still sounding natural — raise toward 0.7 only if replies start
    # feeling too flat/repetitive.
    OLLAMA_TEMPERATURE: float = _float(os.getenv("OLLAMA_TEMPERATURE"), default=0.4)
    OLLAMA_TOP_K: int = _int(os.getenv("OLLAMA_TOP_K"), default=40)
    OLLAMA_TOP_P: float = _float(os.getenv("OLLAMA_TOP_P"), default=0.9)
    OLLAMA_REPEAT_PENALTY: float = _float(
        os.getenv("OLLAMA_REPEAT_PENALTY"), default=1.15
    )

    # ── Gemini ────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    GEMINI_MAX_HISTORY_TOKENS: int = _int(
        os.getenv("GEMINI_MAX_HISTORY_TOKENS"), default=30_000
    )

    # ── LLM retry / warm-up behavior ─────────────────────────────────────
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

    TTS_BLEED_GUARD_MULTIPLIER: float = _float(
        os.getenv("TTS_BLEED_GUARD_MULTIPLIER"), default=1.6
    )
    TTS_FIRST_CHUNK_MIN_CHARS: int = _int(
        os.getenv("TTS_FIRST_CHUNK_MIN_CHARS"), default=12
    )
    TTS_FIRST_CHUNK_SOFT_BOUNDARY_MIN_CHARS: int = _int(
        os.getenv("TTS_FIRST_CHUNK_SOFT_BOUNDARY_MIN_CHARS"), default=35
    )
    # ── Core ──────────────────────────────────────────────────────────────
    DEBUG_MODE: bool = _bool(os.getenv("DEBUG_MODE", "False"), default=False)
    WAKE_WORD: str = os.getenv("WAKE_WORD", "sara , sarah").lower().strip()
    SARA_NAME: str = os.getenv("SARA_NAME", "Sara")
    SARA_TIMEZONE: str = os.getenv("SARA_TIMEZONE", "Asia/Kolkata")
    SARA_LANGUAGE: str = os.getenv("SARA_LANGUAGE", "hinglish").lower().strip()

    # ── Wake acknowledgement phrase ──────────────────────────────────────
    # Previously only defined as a MODULE-level variable (outside this
    # class), so core_wiring.py's
    # getattr(Config, "WAKE_ACK_PHRASE", "Yes?") always silently fell
    # through to the "Yes?" default regardless of .env. Now a real Config
    # class attribute, so it's actually configurable. The module-level
    # WAKE_ACK_PHRASE constant elsewhere in this file is left untouched
    # for backward compatibility with anything that might reference it
    # directly.
    WAKE_ACK_PHRASE: str = os.getenv("WAKE_ACK_PHRASE", "Yes?")

    # ── Wake word — fallback STT-based multi-variant matching (stt.py) ─────
    WAKE_WORDS: list = [
        w.strip().lower()
        for w in os.getenv("WAKE_WORDS", "sara,sarah,hey sara,hey sarah").split(",")
        if w.strip()
    ]

    WAKE_WORD_ALLOW_CUSTOM_ONLY: bool = _bool(
        os.getenv("WAKE_WORD_ALLOW_CUSTOM_ONLY", "False"), default=False
    )

    WAKE_WORD_MODEL_PATH: str | None = _optional_str(os.getenv("WAKE_WORD_MODEL_PATH"))

    WAKE_WORD_FAST_MODEL_SIZE: str = os.getenv("WAKE_WORD_FAST_MODEL_SIZE", "tiny")
    WAKE_LISTEN_TIMEOUT_S: float = _float(os.getenv("WAKE_LISTEN_TIMEOUT_S"), default=1.5)
    WAKE_LISTEN_MAX_DURATION_S: float = _float(
        os.getenv("WAKE_LISTEN_MAX_DURATION_S"), default=1.8
    )
    WAKE_FUZZY_MATCH_ENABLED: bool = _bool(
        os.getenv("WAKE_FUZZY_MATCH_ENABLED", "True"), default=True
    )
    WAKE_FUZZY_MATCH_THRESHOLD: float = _float(
        os.getenv("WAKE_FUZZY_MATCH_THRESHOLD"), default=0.75
    )

    WAKE_WORD_COOLDOWN_S: float = _float(os.getenv("WAKE_WORD_COOLDOWN_S"), default=2.0)
    WAKE_WORD_THRESHOLD: float = _float(os.getenv("WAKE_WORD_THRESHOLD"), default=0.5)
    WAKE_WORD_BEAM_SIZE: int = _int(os.getenv("WAKE_WORD_BEAM_SIZE"), default=1)

    # ── Mic settle time after TTS stops (echo / room-decay guard) ──────────
    STT_SETTLE_MIN_GAP_S: float = _float(os.getenv("STT_SETTLE_MIN_GAP_S"), default=1.3)

    # ── Acoustic Echo Cancellation (AEC) — WebRTC APM (aec-audio-processing) ─
    AEC_ENABLED: bool = _bool(os.getenv("AEC_ENABLED", "True"), default=True)

    AEC_SAMPLE_RATE: int = _int(os.getenv("AEC_SAMPLE_RATE"), default=16000)

    AEC_STREAM_DELAY_MS: int = _int(os.getenv("AEC_STREAM_DELAY_MS"), default=80)

    AEC_ENABLE_NS: bool = _bool(os.getenv("AEC_ENABLE_NS", "True"), default=True)
    AEC_ENABLE_AGC: bool = _bool(os.getenv("AEC_ENABLE_AGC", "False"), default=False)

    AEC_ENABLE_VAD: bool = _bool(os.getenv("AEC_ENABLE_VAD", "False"), default=False)

    # ── Memory ────────────────────────────────────────────────────────────
    MAX_MEMORY_EXCHANGES: int = _int(os.getenv("MAX_MEMORY_EXCHANGES"), default=6)

    # ── Language detection ────────────────────────────────────────────────
    LANG_DETECTION_MODE: str = os.getenv("LANG_DETECTION_MODE", "auto").lower().strip()
    STT_LANGUAGE: str | None = _optional_str(os.getenv("STT_LANGUAGE"))

    STT_FORCE_LANG_FOR_HINGLISH: bool = _bool(
        os.getenv("STT_FORCE_LANG_FOR_HINGLISH", "True"), default=True
    )

    # ── Whisper transcription tuning ─────────────────────────────────────
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

    # ── Low-confidence short-transcript reject gate + TTS hard-mute ──────
    # These were previously read via getattr(Config, "X", default)
    # without ever being defined on this class -- meaning they looked
    # configurable via .env but silently were not. Defaults below match
    # the getattr fallback values already in use elsewhere, so behavior
    # is unchanged for anyone not explicitly setting these in .env.
    STT_MIN_CONFIDENCE_REJECT: float = _float(
        os.getenv("STT_MIN_CONFIDENCE_REJECT"), default=0.35
    )
    STT_HARD_MUTE_DURING_TTS: bool = _bool(
        os.getenv("STT_HARD_MUTE_DURING_TTS", "True"), default=True
    )

    # ── Low-confidence destructive-action confirmation (NEW) ────────────
    # When the STT confidence for a turn (see sara/audio/stt/engine.py's
    # TranscriptionResult) falls below this, shutdown_system /
    # restart_system / log_off / empty_recycle_bin additionally require
    # a spoken "yes"/"cancel" confirmation even if the keyword-based
    # risky-action check wouldn't otherwise flag them. Does NOT affect
    # any other intent.
    STT_CONFIDENCE_CONFIRM_THRESHOLD: float = _float(
        os.getenv("STT_CONFIDENCE_CONFIRM_THRESHOLD"), default=0.55
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
    PROACTIVE_ENABLED: bool = _bool(os.getenv("PROACTIVE_ENABLED", "True"), default=True)
    PROACTIVE_CHECK_INTERVAL_S: int = _int(
        os.getenv("PROACTIVE_CHECK_INTERVAL_S"), default=60
    )
    PROACTIVE_BATTERY_LOW_PERCENT: int = _int(
        os.getenv("PROACTIVE_BATTERY_LOW_PERCENT"), default=15
    )
    PROACTIVE_REMINDER_LEAD_MINUTES: int = _int(
        os.getenv("PROACTIVE_REMINDER_LEAD_MINUTES"), default=15
    )
    PROACTIVE_IDLE_BREAK_MINUTES: int = _int(
        os.getenv("PROACTIVE_IDLE_BREAK_MINUTES"), default=90
    )
    PROACTIVE_COOLDOWN_MINUTES: int = _int(
        os.getenv("PROACTIVE_COOLDOWN_MINUTES"), default=30
    )
    PROACTIVE_LLM_PHRASING: bool = _bool(
        os.getenv("PROACTIVE_LLM_PHRASING", "True"), default=True
    )
    PROACTIVE_MEETING_LEAD_MINUTES: int = _int(
        os.getenv("PROACTIVE_MEETING_LEAD_MINUTES"), default=15
    )

    # ── File Notifications (sara/orchestrator/notifications.py) ─────────
    # Watches a single folder (default: the real Windows Downloads
    # folder, resolved via SHGetKnownFolderPath -- see
    # notifications.py's get_downloads_folder()) for a file completing,
    # and announces it once via the existing TTS worker. Only one watch
    # is active at a time -- see NotificationWatcher.watch_for_next_file()
    # for what happens if a second watch is requested while one is
    # already running (it silently replaces the first).
    NOTIFICATIONS_ENABLED: bool = _bool(
        os.getenv("NOTIFICATIONS_ENABLED", "True"), default=True
    )
    # How often (seconds) the background thread re-checks the
    # NOTIFICATIONS_ENABLED gate and the watched folder's state. Same
    # role as PROACTIVE_CHECK_INTERVAL_S, kept much shorter since a
    # download-finished notification should feel near-instant, not
    # delayed up to a full minute.
    NOTIFICATIONS_CHECK_INTERVAL_S: int = _int(
        os.getenv("NOTIFICATIONS_CHECK_INTERVAL_S"), default=2
    )

    # ── Emergency Stop Hotkey (sara/orchestrator/emergency_stop.py) ──────
    # Global "panic button": stops TTS immediately and cancels any
    # pending file-notification watch, WITHOUT closing the app. Reuses
    # the `keyboard` library already in requirements.txt.
    EMERGENCY_STOP_ENABLED: bool = _bool(
        os.getenv("EMERGENCY_STOP_ENABLED", "True"), default=True
    )
    EMERGENCY_STOP_HOTKEY: str = os.getenv("EMERGENCY_STOP_HOTKEY", "ctrl+alt+s")

    # ── Skills (sara/skills/) ─────────────────────────────────────────────
    DAILY_BRIEFING_LOCATION: str = os.getenv("DAILY_BRIEFING_LOCATION", "Ajmer,IN")

    NOTES_FOLDER: str = os.getenv("NOTES_FOLDER") or str(_PROJECT_ROOT / "sara_class_notes")
    NOTES_CHUNK_CHARS: int = _int(os.getenv("NOTES_CHUNK_CHARS"), default=800)
    NOTES_QA_TOP_K: int = _int(os.getenv("NOTES_QA_TOP_K"), default=4)
    NOTES_MAX_CHUNKS_PER_FILE: int = _int(
        os.getenv("NOTES_MAX_CHUNKS_PER_FILE"), default=200
    )

    # ── Personality: festival-aware greeting ─────────────────────────────
    DIWALI_DATE: str = os.getenv("DIWALI_DATE", "")
    HOLI_DATE: str = os.getenv("HOLI_DATE", "")

    # ── RAG / long-term semantic memory (sara/core/rag.py) ──────────────────
    RAG_ENABLED: bool = _bool(os.getenv("RAG_ENABLED", "True"), default=True)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    EMBEDDING_TIMEOUT_S: float = _float(os.getenv("EMBEDDING_TIMEOUT_S"), default=4.0)
    RAG_TOP_K: int = _int(os.getenv("RAG_TOP_K"), default=6)
    RAG_MIN_SIMILARITY: float = _float(os.getenv("RAG_MIN_SIMILARITY"), default=0.40)
    # NEW: durable, explicitly-stated facts ("my girlfriend's name is
    # Parul") get their own lower threshold in rag.py's search() -- they
    # shouldn't depend on the same lucky semantic overlap a full
    # conversational exchange needs, since there's usually only ONE
    # short sentence to match against a query like "who is Parul".
    RAG_FACT_MIN_SIMILARITY: float = _float(os.getenv("RAG_FACT_MIN_SIMILARITY"), default=0.30)
    RAG_MAX_IN_MEMORY: int = _int(os.getenv("RAG_MAX_IN_MEMORY"), default=5000)

    # ── Memory Management: decision memory & consolidation ──────────────
    MEMORY_CONSOLIDATION_ENABLED: bool = _bool(
        os.getenv("MEMORY_CONSOLIDATION_ENABLED", "True"), default=True
    )
    MEMORY_CONSOLIDATION_INTERVAL_S: int = _int(
        os.getenv("MEMORY_CONSOLIDATION_INTERVAL_S"), default=60
    )
    MEMORY_CONSOLIDATION_BATCH_SIZE: int = _int(
        os.getenv("MEMORY_CONSOLIDATION_BATCH_SIZE"), default=20
    )
    MEMORY_CONSOLIDATION_MAX_FACTS: int = _int(
        os.getenv("MEMORY_CONSOLIDATION_MAX_FACTS"), default=3
    )
    MEMORY_FORGET_MATCH_THRESHOLD: float = _float(
        os.getenv("MEMORY_FORGET_MATCH_THRESHOLD"), default=0.45
    )

    # ── LLM tool-calling (sara/core/tool_router.py) ──────────────────────────
    TOOL_CALLING_ENABLED: bool = _bool(
        os.getenv("TOOL_CALLING_ENABLED", "True"), default=True
    )
    TOOL_CALLING_TIMEOUT_S: float = _float(
        os.getenv("TOOL_CALLING_TIMEOUT_S"), default=3.0
    )
    TOOL_CALLING_MODE: str = os.getenv("TOOL_CALLING_MODE", "llm").lower()

    # ── Multi-step planning engine (sara/core/planning/) ────────────────────
    PLANNING_ENABLED: bool = _bool(os.getenv("PLANNING_ENABLED", "True"), default=True)
    PLANNING_MAX_STEPS: int = _int(os.getenv("PLANNING_MAX_STEPS"), default=4)
    PLANNING_STEP_TIMEOUT_S: float = _float(
        os.getenv("PLANNING_STEP_TIMEOUT_S"), default=3.0
    )
    PLANNING_TOTAL_TIMEOUT_S: float = _float(
        os.getenv("PLANNING_TOTAL_TIMEOUT_S"), default=6.0
    )
    PLANNING_STEP_RETRY_ENABLED: bool = _bool(
        os.getenv("PLANNING_STEP_RETRY_ENABLED", "True"), default=True
    )

    # ── Application launch allowlist (sara/core/planning/schema.py) ─────────
    APP_LAUNCH_ALLOWLIST_ENABLED: bool = _bool(
        os.getenv("APP_LAUNCH_ALLOWLIST_ENABLED", "True"), default=True
    )
    APP_LAUNCH_ALLOWLIST: list = [
        w.strip().lower()
        for w in os.getenv(
            "APP_LAUNCH_ALLOWLIST", _DEFAULT_APP_LAUNCH_ALLOWLIST
        ).split(",")
        if w.strip()
    ]

    # ── Shared file paths (CWD-independent) ─────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH") or str(_PROJECT_ROOT / "sara_data.db")
    NOTES_FILE_PATH: str = os.getenv("NOTES_FILE_PATH") or str(
        _PROJECT_ROOT / "sara_notes.txt"
    )
    UNMATCHED_LOG_PATH: str = os.getenv("UNMATCHED_LOG_PATH") or str(
        _PROJECT_ROOT / "sara_unmatched_queries.jsonl"
    )

    # ── Google Calendar (sara/tools/calendar.py) ─────────────────────────
    GOOGLE_CALENDAR_CREDENTIALS_PATH: str = os.getenv(
        "GOOGLE_CALENDAR_CREDENTIALS_PATH"
    ) or str(_PROJECT_ROOT / "credentials.json")
    GOOGLE_CALENDAR_TOKEN_PATH: str = os.getenv("GOOGLE_CALENDAR_TOKEN_PATH") or str(
        _PROJECT_ROOT / "token.json"
    )

    @classmethod
    def validate(cls, force: bool = False) -> None:
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
        cls.TTS_FIRST_CHUNK_MIN_CHARS = max(
            _MIN_TTS_FIRST_CHUNK_CHARS,
            min(_MAX_TTS_FIRST_CHUNK_CHARS, cls.TTS_FIRST_CHUNK_MIN_CHARS),
        )
        cls.TTS_FIRST_CHUNK_SOFT_BOUNDARY_MIN_CHARS = max(
            cls.TTS_FIRST_CHUNK_MIN_CHARS,
            min(
                _MAX_TTS_FIRST_CHUNK_SOFT_CHARS,
                cls.TTS_FIRST_CHUNK_SOFT_BOUNDARY_MIN_CHARS,
            ),
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
        cls.STT_MIN_CONFIDENCE_REJECT = max(
            0.0, min(1.0, cls.STT_MIN_CONFIDENCE_REJECT)
        )
        cls.WAKE_WORD_BEAM_SIZE = max(
            _MIN_WHISPER_BEAM_SIZE, min(_MAX_WHISPER_BEAM_SIZE, cls.WAKE_WORD_BEAM_SIZE)
        )

        # ── Low-confidence confirmation clamp (NEW) ─────────────────────
        cls.STT_CONFIDENCE_CONFIRM_THRESHOLD = max(
            _MIN_STT_CONFIDENCE_CONFIRM_THRESHOLD,
            min(
                _MAX_STT_CONFIDENCE_CONFIRM_THRESHOLD,
                cls.STT_CONFIDENCE_CONFIRM_THRESHOLD,
            ),
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
        cls.WAKE_LISTEN_TIMEOUT_S = max(0.5, min(5.0, cls.WAKE_LISTEN_TIMEOUT_S))
        cls.WAKE_LISTEN_MAX_DURATION_S = max(0.8, min(5.0, cls.WAKE_LISTEN_MAX_DURATION_S))
        cls.WAKE_FUZZY_MATCH_THRESHOLD = max(0.5, min(1.0, cls.WAKE_FUZZY_MATCH_THRESHOLD))

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
        cls.OLLAMA_TEMPERATURE = max(0.0, min(2.0, cls.OLLAMA_TEMPERATURE))
        cls.OLLAMA_NUM_CTX = max(256, cls.OLLAMA_NUM_CTX)
        if cls.OLLAMA_SUMMARY_NUM_CTX < cls.OLLAMA_NUM_CTX:
            cls.OLLAMA_SUMMARY_NUM_CTX = cls.OLLAMA_NUM_CTX
        cls.OLLAMA_TOP_K = max(_MIN_OLLAMA_TOP_K, min(_MAX_OLLAMA_TOP_K, cls.OLLAMA_TOP_K))
        cls.OLLAMA_TOP_P = max(_MIN_OLLAMA_TOP_P, min(_MAX_OLLAMA_TOP_P, cls.OLLAMA_TOP_P))
        cls.OLLAMA_REPEAT_PENALTY = max(
            _MIN_OLLAMA_REPEAT_PENALTY,
            min(_MAX_OLLAMA_REPEAT_PENALTY, cls.OLLAMA_REPEAT_PENALTY),
        )

        # ── RAG / long-term memory clamps ──────────────────────────────────
        cls.EMBEDDING_TIMEOUT_S = max(1.0, min(15.0, cls.EMBEDDING_TIMEOUT_S))
        cls.RAG_TOP_K = max(1, min(20, cls.RAG_TOP_K))
        cls.RAG_MIN_SIMILARITY = max(0.0, min(1.0, cls.RAG_MIN_SIMILARITY))
        cls.RAG_FACT_MIN_SIMILARITY = max(0.0, min(1.0, cls.RAG_FACT_MIN_SIMILARITY))
        cls.RAG_MAX_IN_MEMORY = max(100, min(50_000, cls.RAG_MAX_IN_MEMORY))

        # ── Memory consolidation / decision-memory clamps ────────────────
        cls.MEMORY_CONSOLIDATION_INTERVAL_S = max(
            _MIN_MEMORY_CONSOLIDATION_INTERVAL_S,
            min(_MAX_MEMORY_CONSOLIDATION_INTERVAL_S, cls.MEMORY_CONSOLIDATION_INTERVAL_S),
        )
        cls.MEMORY_CONSOLIDATION_BATCH_SIZE = max(
            _MIN_MEMORY_CONSOLIDATION_BATCH_SIZE,
            min(_MAX_MEMORY_CONSOLIDATION_BATCH_SIZE, cls.MEMORY_CONSOLIDATION_BATCH_SIZE),
        )
        cls.MEMORY_CONSOLIDATION_MAX_FACTS = max(
            _MIN_MEMORY_CONSOLIDATION_MAX_FACTS,
            min(_MAX_MEMORY_CONSOLIDATION_MAX_FACTS, cls.MEMORY_CONSOLIDATION_MAX_FACTS),
        )
        cls.MEMORY_FORGET_MATCH_THRESHOLD = max(
            0.0, min(1.0, cls.MEMORY_FORGET_MATCH_THRESHOLD)
        )

        # ── Tool-calling clamps ─────────────────────────────────────────────
        cls.TOOL_CALLING_TIMEOUT_S = max(1.0, min(15.0, cls.TOOL_CALLING_TIMEOUT_S))

        if cls.TOOL_CALLING_MODE not in ("llm", "heuristic"):
            print(
                f"[Warning] Unknown TOOL_CALLING_MODE '{cls.TOOL_CALLING_MODE}', defaulting to 'llm'."
            )
            cls.TOOL_CALLING_MODE = "llm"

        # ── Planning engine clamps ───────────────────────────────────────────
        cls.PLANNING_MAX_STEPS = max(
            _MIN_PLANNING_MAX_STEPS, min(_MAX_PLANNING_MAX_STEPS, cls.PLANNING_MAX_STEPS)
        )
        cls.PLANNING_STEP_TIMEOUT_S = max(
            _MIN_PLANNING_STEP_TIMEOUT_S,
            min(_MAX_PLANNING_STEP_TIMEOUT_S, cls.PLANNING_STEP_TIMEOUT_S),
        )
        cls.PLANNING_TOTAL_TIMEOUT_S = max(
            cls.PLANNING_STEP_TIMEOUT_S,
            min(_MAX_PLANNING_TOTAL_TIMEOUT_S, cls.PLANNING_TOTAL_TIMEOUT_S),
        )
        cls.PLANNING_TOTAL_TIMEOUT_S = max(
            _MIN_PLANNING_TOTAL_TIMEOUT_S, cls.PLANNING_TOTAL_TIMEOUT_S
        )

        # ── Application launch allowlist normalization ───────────────────────
        if cls.APP_LAUNCH_ALLOWLIST:
            seen: set[str] = set()
            deduped: list[str] = []
            for entry in cls.APP_LAUNCH_ALLOWLIST:
                normalized_entry = entry.strip().lower()
                if normalized_entry and normalized_entry not in seen:
                    seen.add(normalized_entry)
                    deduped.append(normalized_entry)
            cls.APP_LAUNCH_ALLOWLIST = deduped

        if cls.APP_LAUNCH_ALLOWLIST_ENABLED and not cls.APP_LAUNCH_ALLOWLIST:
            print(
                "[Warning] APP_LAUNCH_ALLOWLIST_ENABLED is True but "
                "APP_LAUNCH_ALLOWLIST is empty -- falling back to the built-in "
                "default app list so open_app/close_app remain usable."
            )
            cls.APP_LAUNCH_ALLOWLIST = [
                w.strip().lower()
                for w in _DEFAULT_APP_LAUNCH_ALLOWLIST.split(",")
                if w.strip()
            ]

        # ── Proactive Engine clamps ─────────────────────────────────────────
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
        cls.PROACTIVE_MEETING_LEAD_MINUTES = max(
            1, min(180, cls.PROACTIVE_MEETING_LEAD_MINUTES)
        )

        # ── File Notifications clamps ───────────────────────────────────
        cls.NOTIFICATIONS_CHECK_INTERVAL_S = max(
            1, min(30, cls.NOTIFICATIONS_CHECK_INTERVAL_S)
        )

        # ── Emergency Stop Hotkey clamp ─────────────────────────────────
        if not cls.EMERGENCY_STOP_HOTKEY or not cls.EMERGENCY_STOP_HOTKEY.strip():
            print(
                "[Warning] EMERGENCY_STOP_HOTKEY is empty, defaulting to 'ctrl+alt+s'."
            )
            cls.EMERGENCY_STOP_HOTKEY = "ctrl+alt+s"
        else:
            cls.EMERGENCY_STOP_HOTKEY = cls.EMERGENCY_STOP_HOTKEY.strip().lower()

        # ── Notes Q&A clamps ─────────────────────────────────────────────────
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
                f"[Debug] STT confirm  : low_confidence_threshold={cls.STT_CONFIDENCE_CONFIRM_THRESHOLD}"
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
                f"[Debug] TTS 1st chunk: min={cls.TTS_FIRST_CHUNK_MIN_CHARS}chars "
                f"soft_min={cls.TTS_FIRST_CHUNK_SOFT_BOUNDARY_MIN_CHARS}chars"
            )
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
                f"fact_min_sim={cls.RAG_FACT_MIN_SIMILARITY} | "
                f"timeout={cls.EMBEDDING_TIMEOUT_S}s | max_in_ram={cls.RAG_MAX_IN_MEMORY}"
            )
            print(
                f"[Debug] Memory consol.: enabled={cls.MEMORY_CONSOLIDATION_ENABLED} | "
                f"every={cls.MEMORY_CONSOLIDATION_INTERVAL_S}s | "
                f"batch={cls.MEMORY_CONSOLIDATION_BATCH_SIZE} | "
                f"max_facts={cls.MEMORY_CONSOLIDATION_MAX_FACTS}"
            )
            print(f"[Debug] Forget match : threshold={cls.MEMORY_FORGET_MATCH_THRESHOLD}")
            print(
                f"[Debug] Tool calling : enabled={cls.TOOL_CALLING_ENABLED} | "
                f"mode={cls.TOOL_CALLING_MODE} | timeout={cls.TOOL_CALLING_TIMEOUT_S}s"
            )
            print(
                f"[Debug] Planning     : enabled={cls.PLANNING_ENABLED} | "
                f"max_steps={cls.PLANNING_MAX_STEPS} | "
                f"step_timeout={cls.PLANNING_STEP_TIMEOUT_S}s | "
                f"total_timeout={cls.PLANNING_TOTAL_TIMEOUT_S}s | "
                f"retry_enabled={cls.PLANNING_STEP_RETRY_ENABLED}"
            )
            print(
                f"[Debug] App allowlist: enabled={cls.APP_LAUNCH_ALLOWLIST_ENABLED} | "
                f"{len(cls.APP_LAUNCH_ALLOWLIST)} entries"
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
                f"[Debug] Notifications : enabled={cls.NOTIFICATIONS_ENABLED} | "
                f"check_every={cls.NOTIFICATIONS_CHECK_INTERVAL_S}s"
            )
            print(
                f"[Debug] Emergency stop: enabled={cls.EMERGENCY_STOP_ENABLED} | "
                f"hotkey='{cls.EMERGENCY_STOP_HOTKEY}'"
            )
            print(
                f"[Debug] Notes Q&A    : folder={cls.NOTES_FOLDER} | "
                f"chunk_chars={cls.NOTES_CHUNK_CHARS} | top_k={cls.NOTES_QA_TOP_K}"
            )
            if cls.LLM_BACKEND == "ollama":
                print(
                    f"[Debug] Ollama ctx   : {cls.OLLAMA_NUM_CTX} tokens (summary: {cls.OLLAMA_SUMMARY_NUM_CTX})"
                )
                print(f"[Debug] Ollama keep  : {cls.OLLAMA_KEEP_ALIVE}")
                print(
                    f"[Debug] Ollama sample: top_k={cls.OLLAMA_TOP_K} "
                    f"top_p={cls.OLLAMA_TOP_P} repeat_penalty={cls.OLLAMA_REPEAT_PENALTY} "
                    f"temperature={cls.OLLAMA_TEMPERATURE}"
                )

Config.validate()