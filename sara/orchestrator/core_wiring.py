"""
sara.orchestrator.core_wiring
build_core_objects() constructs every subsystem (LLM, TTS, STT, DB,
reminders, vision) at startup; _WakeWatcher + run_sara_logic() are the
main always-on conversation loop.
"""
from .lazy import _debug_log, _Lazy
from .state import LanguageState, AssistantState
from .ollama_manager import _ensure_ollama_running, _stop_ollama_background
from .ui_bridge import _UICoalescer
from .tts_worker import TTSWorker
from .db_writer import AsyncDBWriter
from .text_utils import _extract_name, _matches_phrase_set
from .history import _apply_saved_preferences, _finish_brain_setup
from .intent_handlers import _handle_command
from .network_utils import _shutdown_network_executor
from .proactive import ActivityTracker, ProactiveEngine

import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Optional

from health_check import run_startup_diagnostics

from config import Config
from sara.core.llm import SaraLLM

from sara.audio.tts import TextToSpeech
from sara.audio.stt import SpeechToText
from sara.core.memory import PreferencesDB
from sara.tools.reminders import ReminderManager, play_alarm_beep
from sara.tools.vision import VisionAssistant

# PRODUCTION-AUDIT ADDITION (Phase 2): long-term memory (RAG) and the
# LLM tool-calling fallback are both optional, additive features — if
# either module fails to import for any reason (e.g. numpy missing),
# the whole app must still start exactly as before, just without that
# one feature. Both are re-checked as None/False below wherever used.
try:
    from sara.core.rag import LongTermMemory

    _HAS_RAG = True
except Exception as _rag_import_err:  # noqa: BLE001
    LongTermMemory = None
    _HAS_RAG = False
    print(
        f"[Core] sara.core.rag unavailable, long-term memory disabled: {_rag_import_err}"
    )

try:
    from sara.core.tool_router import (
        resolve_tool_call,
        build_fake_match,
        TOOL_NAME_TO_INTENT,
    )

    _HAS_TOOL_ROUTER = True
except Exception as _tool_router_import_err:  # noqa: BLE001
    resolve_tool_call = None
    build_fake_match = None
    TOOL_NAME_TO_INTENT = {}
    _HAS_TOOL_ROUTER = False
    print(
        f"[Core] sara.core.tool_router unavailable, LLM tool-calling fallback "
        f"disabled: {_tool_router_import_err}"
    )

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_EXIT_WORDS = {
    "exit",
    "quit",
    "stop",
    "goodbye",
    "bye",
    "shutdown",
    "band karo",
    "band kar",
    "alvida",
    "phir milenge",
    "bye bye",
    "बंद करो",
    "अलविदा",
}
_SLEEP_WORDS = {
    "sleep",
    "go to sleep",
    "that's all",
    "nothing else",
    "nevermind",
    "so jao",
    "so ja",
    "bas karo",
    "bas kar",
    "theek hai bas",
    "ठीक है बस",
    "सो जाओ",
}
_FORGET_WORDS = {
    "forget our conversation",
    "clear memory",
    "forget everything",
    "clear our conversation",
    "reset memory",
    "sab bhool jao",
    "memory clear karo",
    "history delete karo",
    "conversation bhool jao",
    "सब भूल जाओ",
}

_STRONG_NAME_PHRASES = (
    "my name is ",
    "call me ",
    "mera naam hai ",
    "mera naam ",
    "mujhe bulao ",
    "main hoon ",
)
_WEAK_NAME_PHRASES = ("i am ", "i'm ")

_WEAK_NAME_BLOCKLIST = {
    "sorry",
    "sure",
    "fine",
    "okay",
    "ok",
    "going",
    "not",
    "just",
    "here",
    "still",
    "really",
    "so",
    "very",
    "trying",
    "about",
    "done",
    "ready",
    "afraid",
    "glad",
    "happy",
    "sad",
    "tired",
    "busy",
    "confused",
    "lost",
    "good",
    "great",
    "alright",
    "kidding",
    "joking",
    "serious",
    "curious",
    "worried",
    "excited",
    "bored",
    "annoyed",
    "stressed",
    "hungry",
}

_MAX_EMPTY_RETRIES = 3
_EMPTY_RETRY_GRACE_S = 8.0
_IDLE_SLEEP_TIMEOUT_S = 180

_WAKE_POLL_INTERVAL_S = 0.05
_WAKE_WAIT_TIMEOUT_S = 0.3

_BARGE_IN_POLL_S = 0.05
_BARGE_IN_GRACE_S = 0.2
_TTS_IDLE_POLL_S = 0.5
_WATCH_IDLE_POLL_S = 0.5
_DB_WRITER_IDLE_POLL_S = 1.0

_NETWORK_TOOL_TIMEOUT_S = 6.0

_CALC_EXPR_RE = re.compile(r"^[\d\s\+\-\*\/\(\)\.\%]+$")
_CALC_MAX_LEN = 200
_CALC_MAX_NUMBER_DIGITS = 12
_CALC_MAX_POW_OPS = 1
_CALC_MAX_EXPONENT_VALUE = 1000
_CALC_EXPONENT_RE = re.compile(r"\*\*\s*([+-]?\d+)")

_OLLAMA_HOST = getattr(Config, "OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = getattr(Config, "OLLAMA_MODEL", "qwen2.5")
_OLLAMA_READY_TIMEOUT_S = 60
_OLLAMA_POLL_INTERVAL_S = 0.25

_DEBUG = getattr(Config, "DEBUG_MODE", False)

# Kokoro speed range. Kokoro's `speed` parameter is DIRECTLY
# proportional to playback rate (1.0 = normal, >1.0 = faster).
_KOKORO_SPEED_MIN = 0.6
_KOKORO_SPEED_MAX = 1.4

_POST_TTS_SETTLE_WITH_AEC_S = 0.3

_THREAD_ERROR_BACKOFF_S = 0.5




def build_core_objects(ui_update):
    """
    Returns: (brain, tts, ears, db, vision, reminders, db_writer,
              lang_state, assistant_state)

    v8 (NEW UI WIRING): also constructs the shared AssistantState used
    by the Home page's Pause/Resume Listening control, restoring its
    initial value from the "assistant_active" preference (defaults to
    active/True if never saved before).
    """
    ui_update("boot_progress", "Running startup diagnostics...", 3)
    run_startup_diagnostics(ui_update)

    ui_update("boot_progress", "Initializing audio engine...", 10)

    from sara.audio.aec import AECProcessor

    aec = None
    if getattr(Config, "AEC_ENABLED", True):
        try:
            aec = AECProcessor()
        except Exception as e:
            print(
                f"[Core] AECProcessor construction failed, continuing without AEC: {e}"
            )
            aec = None

    ui_update("boot_progress", "Starting voice engine...", 20)

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="sara-init") as pool:
        tts_future = pool.submit(TextToSpeech, aec=aec)
        ears_future = pool.submit(SpeechToText, aec=aec)
        db_future = pool.submit(PreferencesDB)

        voice = tts_future.result()
        ears = ears_future.result()
        db = db_future.result()

    ui_update("boot_progress", "Voice engine ready...", 45)

    restored_mode, restored_lang = _apply_saved_preferences(db, ears)
    lang_state = LanguageState(initial_lang=restored_lang)
    if restored_mode == "manual":
        lang_state.set_manual(restored_lang)

    # v8 NEW UI WIRING: restore the assistant pause/resume state saved
    # by a previous session (default: active/listening).
    try:
        _saved_active = db.get_preference("assistant_active")
        initial_active = True if _saved_active is None else (_saved_active == "1")
    except Exception as e:
        print(f"[Warning] Could not restore assistant_active preference: {e}")
        initial_active = True
    assistant_state = AssistantState(initial_active=initial_active)

    ui_update("boot_progress", "Restoring preferences...", 58)

    tts = TTSWorker(voice, ears)
    db_writer = AsyncDBWriter(db)

    ui_update("boot_progress", "Starting core services...", 68)

    def _make_brain():
        _ensure_ollama_running(ui_update)
        b = SaraLLM()
        _finish_brain_setup(db, b)
        ui_update("boot_progress", "AI brain ready...", 88)
        return b

    brain = _Lazy(_make_brain)
    vision = _Lazy(VisionAssistant)

    def _on_reminder(msg: str) -> None:
        ui_update("status", "speaking")
        try:
            play_alarm_beep(repetitions=3)
        except Exception as e:
            print(f"[Warning] alarm beep failed: {e}")
        reply = f"Reminder: {msg}"
        tts.speak(reply, fast=True)
        ui_update("transcript", "sara", f"\U0001f514 {reply}")
        ui_update("notification", "ti-bell-ringing", "#fbbf24", reply)

    def _make_reminders():
        r = ReminderManager(on_trigger=_on_reminder)
        r.start()
        ui_update("boot_progress", "Reminders ready...", 95)
        return r

    reminders = _Lazy(_make_reminders)

    ui_update("boot_progress", "Finalizing startup...", 97)

    return (
        brain,
        tts,
        ears,
        db,
        vision,
        reminders,
        db_writer,
        lang_state,
        assistant_state,
    )


# ----------------------------------------------------------------------------
# Wake watcher
# ----------------------------------------------------------------------------


class _WakeWatcher:
    __slots__ = (
        "_ears",
        "_manual",
        "_stop",
        "_assistant_state",
        "wake_event",
        "_thread",
    )

    def __init__(
        self,
        ears,
        manual_wake_event: threading.Event,
        stop_event: threading.Event,
        assistant_state=None,
    ):
        self._ears = ears
        self._manual = manual_wake_event
        self._stop = stop_event
        # v8 NEW UI WIRING: optional shared AssistantState — when
        # inactive ("paused" from the GUI), wake-WORD detection is
        # skipped every poll cycle below, but an explicit manual wake
        # (self._manual, set by Api.wake_now()) always still works,
        # since that's a deliberate user action rather than passive
        # background listening.
        self._assistant_state = assistant_state
        self.wake_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.wake_event.is_set():
                    time.sleep(_WAKE_POLL_INTERVAL_S)
                    continue

                manual_triggered = self._manual.is_set()

                if manual_triggered:
                    # Deliberate user action — never let the (potentially
                    # multi-second, STT-fallback) wake-word probe delay this.
                    wake_word_hit = False
                else:
                    assistant_active = True
                    if self._assistant_state is not None:
                        assistant_active = self._assistant_state.is_active()
                    wake_word_hit = (
                        assistant_active and self._ears.is_wake_word_detected()
                    )

                if wake_word_hit or manual_triggered:
                    self._manual.clear()
                    self.wake_event.set()
                    continue
                time.sleep(_WAKE_POLL_INTERVAL_S)
            except Exception as e:
                logger.exception(f"[WakeWatcher] unexpected error (continuing): {e}")
                time.sleep(_THREAD_ERROR_BACKOFF_S)

    def wait_for_wake(self) -> bool:
        while not self._stop.is_set():
            if self.wake_event.wait(timeout=_WAKE_WAIT_TIMEOUT_S):
                self.wake_event.clear()
                return True
        return False


# ----------------------------------------------------------------------------
# Main conversation loop
# ----------------------------------------------------------------------------


def _festival_greeting() -> Optional[str]:
    """
    Returns a special greeting if today matches a known festival, else
    None (caller falls back to the normal greeting). Fixed-date national
    days are hardcoded here since they're the same date every year;
    Diwali/Holi come from Config.DIWALI_DATE/HOLI_DATE instead since
    those shift yearly on the lunar calendar — see config.py's comment
    on why they're deliberately NOT hardcoded.
    """
    today = date.today()
    fixed = {
        (1, 1): "Happy New Year! I am Sara. Systems are online.",
        (1, 26): "Happy Republic Day! I am Sara. Systems are online.",
        (8, 15): "Happy Independence Day! I am Sara. Systems are online.",
        (9, 5): "Happy Teachers' Day! I am Sara. Systems are online.",
        (10, 2): "Happy Gandhi Jayanti! I am Sara. Systems are online.",
        (12, 25): "Merry Christmas! I am Sara. Systems are online.",
    }
    if (today.month, today.day) in fixed:
        return fixed[(today.month, today.day)]

    for env_date, name in (
        (getattr(Config, "DIWALI_DATE", ""), "Diwali"),
        (getattr(Config, "HOLI_DATE", ""), "Holi"),
    ):
        if not env_date:
            continue
        try:
            configured = datetime.strptime(env_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if configured == today:
            return f"Happy {name}! I am Sara. Systems are online."
    return None


def run_sara_logic(
    ui_update,
    stop_event,
    brain,
    tts,
    ears,
    db,
    vision,
    reminders,
    db_writer,
    manual_wake_event=None,
    lang_state=None,
    assistant_state=None,
) -> None:
    if manual_wake_event is None:
        manual_wake_event = threading.Event()
    if lang_state is None:
        lang_state = LanguageState()

    ui_update = _UICoalescer(ui_update)

    volume_state: dict = {}
    # NEW: same pattern as volume_state -- created once for the whole run
    # so state genuinely persists turn-to-turn. playback_state backs the
    # "play next video" YouTube follow-up; confirm_state backs the
    # close_app/stop_service risky-action yes/cancel flow.
    playback_state: dict = {}
    confirm_state: dict = {}
    # v8 NEW UI WIRING: assistant_state is passed straight through to
    # _WakeWatcher, which is the only place it's actually consulted.
    wake_watcher = _WakeWatcher(ears, manual_wake_event, stop_event, assistant_state)

    # PROACTIVE ENGINE: Sara's agentic background loop (battery / upcoming
    # reminders / idle-break nudges). Independent daemon thread, started
    # here and stopped in the `finally:` block below alongside every other
    # background worker — see sara/orchestrator/proactive.py for the full
    # design. `activity_tracker.touch()` is called at wake and on every
    # real user turn further down in this same function so the idle/break
    # trigger has an accurate "time since last real conversation" clock.
    activity_tracker = ActivityTracker()
    proactive_engine = ProactiveEngine(
        db=db,
        reminders=reminders,
        tts=tts,
        ui_update=ui_update,
        activity_tracker=activity_tracker,
        assistant_state=assistant_state,
        lang_state=lang_state,
    )
    proactive_engine.start()

    # NOTES Q&A (sara/skills/notes_qa.py): one shared LongTermMemory
    # instance for the whole run, passed into _handle_command() below via
    # notes_memory so the skill can search() it, and synced once here in
    # a background thread (embedding a folder of notes can take a few
    # seconds and must never delay the assistant becoming responsive).
    notes_memory = LongTermMemory() if _HAS_RAG else None
    if notes_memory is not None:
        def _sync_notes_once():
            try:
                from sara.skills.notes_qa import sync_notes_folder

                sync_notes_folder(notes_memory, db)
            except Exception as e:
                print(f"[NotesQA] startup sync failed: {e}")

        threading.Thread(
            target=_sync_notes_once, daemon=True, name="sara-notes-sync"
        ).start()

    aec_active = (
        bool(getattr(Config, "AEC_ENABLED", True))
        and getattr(ears, "_aec", None) is not None
    )
    post_tts_settle_s = (
        _POST_TTS_SETTLE_WITH_AEC_S
        if aec_active
        else float(getattr(Config, "STT_SETTLE_MIN_GAP_S", 1.3))
    )
    if _DEBUG:
        print(
            f"[Logic] Post-TTS mic settle: {post_tts_settle_s}s (AEC active={aec_active})"
        )

    wake_words_display = ", ".join(
        getattr(Config, "WAKE_WORDS", None) or [Config.WAKE_WORD]
    )

    _RAPID_TURN_WINDOW_S = 12.0
    _RAPID_TURN_MAX_COUNT = 5
    recent_turn_times: list = []

    def _record_turn_and_check_runaway() -> bool:
        now = time.monotonic()
        recent_turn_times.append(now)
        while recent_turn_times and (now - recent_turn_times[0]) > _RAPID_TURN_WINDOW_S:
            recent_turn_times.pop(0)
        if len(recent_turn_times) > _RAPID_TURN_MAX_COUNT:
            return True
        return False

    try:
        ui_update("boot_progress", "Ready!", 100)

        greeting = _festival_greeting() or "Hello! I am Sara. Systems are online."
        tts.speak(greeting, fast=True)
        ui_update("transcript", "sara", greeting)
        ui_update("footer", f"Wake words: {wake_words_display}")

        while not stop_event.is_set():
            ui_update("status", "sleeping")
            ui_update("footer", f"Say '{wake_words_display}' to wake me...")

            woke = wake_watcher.wait_for_wake()
            if stop_event.is_set() or not woke:
                break

            activity_tracker.touch()
            try:
                db.record_interaction_day()
            except Exception as e:
                print(f"[Streak] record_interaction_day failed: {e}")
            ui_update("status", "waking")
            if hasattr(tts, "clear_interrupt"):
                tts.clear_interrupt()

            # LATENCY: short ack ("Yes?" instead of a full sentence) cuts
            # synthesis+playback time drastically on its own — combined
            # with the TTS phrase cache (see engine.py), after the first
            # cycle this phrase costs ~0 synthesis time, just playback.
            #
            # When AEC is actively cancelling echo (aec_active, computed
            # above), fire the ack asynchronously (block=False) so
            # ears.wait_settle()/listen() below start immediately instead
            # of waiting for playback to finish — this is what actually
            # removes the fixed 1-1.5s tax per wake cycle. Without AEC we
            # keep it blocking, since the mic would otherwise pick up our
            # own ack and STT could misfire on it.
            ack = getattr(Config, "WAKE_ACK_PHRASE", "Yes?")
            try:
                tts.speak(ack, fast=True, block=not aec_active)
            except Exception as e:
                # Ack failing must NEVER stop the command-listening cycle.
                print(f"[Logic] wake-ack speak failed (continuing): {e}")

            empty_retries = 0
            session_start = time.monotonic()
            last_active_time = session_start
            recent_turn_times.clear()

            while not stop_event.is_set():
                ui_update("status", "listening")
                ui_update("footer", "Listening...")
                ears.wait_settle(min_gap=post_tts_settle_s)
                user_input = ears.listen(mode="command")

                if stop_event.is_set():
                    break

                if not user_input:
                    empty_retries += 1
                    idle_elapsed = time.monotonic() - last_active_time
                    retry_limit_hit = (
                        empty_retries >= _MAX_EMPTY_RETRIES
                        and idle_elapsed >= _EMPTY_RETRY_GRACE_S
                    )
                    if idle_elapsed >= _IDLE_SLEEP_TIMEOUT_S or retry_limit_hit:
                        bye = "Going back to sleep."
                        tts.speak(bye, fast=True)
                        ui_update("transcript", "sara", bye)
                        break
                    remaining = max(0, int(_IDLE_SLEEP_TIMEOUT_S - idle_elapsed))
                    ui_update(
                        "footer",
                        f"Didn't catch that \u2014 still listening... ({remaining}s to sleep)",
                    )
                    continue

                empty_retries = 0
                last_active_time = time.monotonic()
                activity_tracker.touch()
                ui_update("transcript", "user", user_input)
                lowered = user_input.lower().strip()

                turn_lang_mode, turn_manual_lang = lang_state.snapshot()
                if turn_lang_mode == "auto":
                    detected_lang = ears.get_detected_language()
                    tts.set_language(detected_lang)
                    _debug_log(f"[Logic] Language this turn (auto): '{detected_lang}'")
                else:
                    tts.set_language(turn_manual_lang)
                    _debug_log(
                        f"[Logic] Language this turn (manual override): '{turn_manual_lang}'"
                    )

                if _matches_phrase_set(lowered, _EXIT_WORDS):
                    farewell = "Shutting down. Goodbye!"
                    ui_update("transcript", "sara", farewell)
                    tts.speak(farewell, fast=True)
                    stop_event.set()
                    break

                if _matches_phrase_set(lowered, _SLEEP_WORDS):
                    reply = "Okay, going back to sleep."
                    tts.speak(reply, fast=True)
                    ui_update("transcript", "sara", reply)
                    break

                if _matches_phrase_set(lowered, _FORGET_WORDS):
                    ui_update("status", "thinking")
                    brain.clear_memory()
                    reply = "Done, I've cleared our conversation history."
                    tts.speak(reply, fast=True)
                    ui_update("transcript", "sara", reply)
                    db_writer.log_message("user", user_input)
                    db_writer.log_message("assistant", reply)
                    if _record_turn_and_check_runaway():
                        break
                    continue

                name = _extract_name(user_input)
                if name:
                    ui_update("status", "thinking")
                    db.set_user_name(name)
                    brain.set_user_name(name)
                    reply = f"Nice to meet you, {name}!"
                    tts.speak(reply, fast=True)
                    ui_update("transcript", "sara", reply)
                    db_writer.log_message("user", user_input)
                    db_writer.log_message("assistant", reply)
                    if _record_turn_and_check_runaway():
                        break
                    continue

                reply_text = _handle_command(
                    user_input,
                    brain,
                    tts,
                    ears,
                    db,
                    reminders,
                    vision,
                    ui_update,
                    volume_state,
                    notes_memory=notes_memory,
                    playback_state=playback_state,
                    confirm_state=confirm_state,
                )

                ui_update("transcript", "sara", reply_text or "(no response)")
                db_writer.log_message("user", user_input)
                db_writer.log_message("assistant", reply_text or "")

                if _record_turn_and_check_runaway():
                    print(
                        "[Logic Warning] Rapid consecutive turns detected "
                        "(possible echo/feedback loop) — forcing sleep as a safety measure."
                    )
                    warn_msg = "I think I might be hearing myself — going back to sleep for a moment."
                    tts.speak(warn_msg, fast=True)
                    ui_update("transcript", "sara", warn_msg)
                    break

        try:
            ears.close()
        except Exception as e:
            print(f"[Warning] ears.close() failed: {e}")

    except Exception as e:
        logger.critical(f"[Fatal Error \u2014 Sara logic thread] {e}", exc_info=True)
    finally:
        proactive_engine.shutdown()
        if notes_memory is not None:
            notes_memory.close()
        tts.shutdown()
        db_writer.shutdown()
        _stop_ollama_background()
        _shutdown_network_executor()