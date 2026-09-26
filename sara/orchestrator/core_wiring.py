"""
sara.orchestrator.core_wiring
build_core_objects() constructs every subsystem (LLM, TTS, STT, DB,
reminders, vision) at startup; _WakeWatcher + run_sara_logic() are the
main always-on conversation loop.
"""
from sara.orchestrator.state import STATE_LOCK, TURN_STATE
from .lazy import _debug_log, _Lazy
from .state import LanguageState, AssistantState
from .ui_bridge import _UICoalescer
from .tts_worker import TTSWorker
from .db_writer import AsyncDBWriter
from .history import _apply_saved_preferences, _finish_brain_setup
from .intent_handlers import _handle_command
from .network_utils import _shutdown_network_executor
from .proactive import ActivityTracker, ProactiveEngine
from .supervisor import ThreadSupervisor, get_notification_watcher_if_any
from ._constants import (
    _MAX_EMPTY_RETRIES,
    _EMPTY_RETRY_GRACE_S,
    _IDLE_SLEEP_TIMEOUT_S,
    _WAKE_POLL_INTERVAL_S,
    _WAKE_WAIT_TIMEOUT_S,
    _DEBUG,
    _POST_TTS_SETTLE_WITH_AEC_S,
    _THREAD_ERROR_BACKOFF_S,
)

import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, NamedTuple, Optional

# FALLBACK-DISCLOSURE FIX: spoken once per wake session, the first turn
# SaraLLM.used_fallback_last_turn() flips True/False (see run_sara_logic
# below) -- so a sudden drop (or return) in answer quality is legible
# instead of feeling like a random glitch. Keyed the same way as
# engine.py's own message dicts (english/hindi/hinglish), picked via
# brain.get_language() so the disclosure matches whatever language the
# turn itself was already in.
_FALLBACK_DISCLOSURE_MESSAGES = {
    "english": "Quick heads up — I'm running on my lighter local brain right now, so I might be a bit slower on the uptake.",
    "hindi": "Ek baat bata doon — abhi main apne chhote, local brain pe chal raha hoon, jawab thode simple ho sakte hain.",
    "hinglish": "Quick heads up yaar — abhi main apne lighter local brain pe chal raha hoon, thoda different lag sakta hoon.",
}

_FALLBACK_RECOVERY_MESSAGES = {
    "english": "And I'm back on my full brain now — should be sharper again.",
    "hindi": "Aur haan, ab main wapas apne poore brain pe aa gaya hoon.",
    "hinglish": "Aur ab main wapas apne full brain pe aa gaya hoon.",
}

from health_check import run_startup_diagnostics

from config import Config
from sara.core.llm import SaraLLM

from sara.audio.tts import TextToSpeech
from sara.audio.stt import SpeechToText
from sara.core.memory import PreferencesDB
from sara.core.memory_consolidation import start_memory_consolidation   # ← NEW
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
        has_probable_tool_intent,
    )

    _HAS_TOOL_ROUTER = True
except Exception as _tool_router_import_err:  # noqa: BLE001
    resolve_tool_call = None
    build_fake_match = None
    TOOL_NAME_TO_INTENT = {}
    has_probable_tool_intent = lambda _t: False
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






class CoreObjects(NamedTuple):
    """
    Named return value of build_core_objects().

    Ye pehle plain positional tuple tha. Ab NamedTuple hai taaki har field
    naam se access ho (co.brain, co.confirm_state) aur order-mismatch se
    silent bug na bane. NamedTuple isliye (dataclass nahi) kyunki ye abhi
    bhi ek real tuple hai — indexing/iteration wala purana behavior intact
    rehta hai.

    confirm/volume/playback/context_state: CROSS-MODALITY FIX -- ye 4 dicts
    ab yahan ek hi baar bante hain aur GUI path (Api) + voice loop
    (run_sara_logic) dono ko SAME instance milta hai, bilkul waise hi jaise
    lang_state/assistant_state ke saath pehle se hota aa raha hai. Pehle
    dono side apni-apni copy banate the, isliye voice se bola gaya
    "close explorer" GUI mein type kiye "yes" se confirm nahi hota tha.
    """
    brain: Any
    tts: Any
    ears: Any
    db: Any
    vision: Any
    reminders: Any
    db_writer: Any
    lang_state: Any
    assistant_state: Any
    notes_memory: Any
    confirm_state: dict
    volume_state: dict
    playback_state: dict
    context_state: dict


def build_core_objects(ui_update):
    """
    Returns: CoreObjects (see above) -- named fields, not a raw tuple.

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
        # audio_level: GUI orb's live level meter (js/home.js 'ev:audio_level').
        # Both callbacks are called from a dedicated background thread inside
        # the audio engine itself (never the real-time mic/output callback),
        # so calling straight into ui_update() here is safe.
        tts_future = pool.submit(
            TextToSpeech,
            aec=aec,
            on_audio_level=lambda lvl: ui_update("audio_level", "tts", lvl),
        )
        ears_future = pool.submit(
            SpeechToText,
            aec=aec,
            on_audio_level=lambda lvl: ui_update("audio_level", "mic", lvl),
        )
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

    # CROSS-MODALITY FIX: ye 4 session-state dicts yahan, lang_state/
    # assistant_state ke saath hi bante hain -- ek hi instance jo aage
    # Api(...) aur run_sara_logic(...) dono ko pass hota hai. Pehle dono
    # apni local copy banate the (core.py __init__ aur run_sara_logic ke
    # andar), jiski wajah se voice<->GUI ke beech confirm/mute/playback/
    # follow-up context kabhi share hi nahi hota tha.
    confirm_state: dict = {}
    volume_state: dict = {}
    playback_state: dict = {}
    context_state: dict = {}

    ui_update("boot_progress", "Restoring preferences...", 58)

    tts = TTSWorker(voice, ears, db)
    db_writer = AsyncDBWriter(db)

    ui_update("boot_progress", "Starting core services...", 68)

    # BUGFIX (Bug 1 — RAG memory never reached the chat brain): built here,
    # BEFORE _make_brain is defined/wrapped in _Lazy(...) below. This
    # ordering is required, not cosmetic: _Lazy starts a background thread
    # and calls its factory (_make_brain) IMMEDIATELY inside __init__ (see
    # sara/orchestrator/lazy.py) — not on first access, despite the name —
    # so notes_memory must already exist before that thread can possibly
    # run, or _make_brain's closure could read it before assignment.
    notes_memory = LongTermMemory() if _HAS_RAG else None

    def _make_brain():
        # BUGFIX (Bug 1): pass the SAME notes_memory instance used by the
        # voice-intent path (ctx["notes_memory"], see run_sara_logic below)
        # into the brain, so a memory saved via one path is recallable via
        # the other instead of each path silently keeping its own store.
        b = SaraLLM(memory=notes_memory)
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

    return CoreObjects(
        brain=brain,
        tts=tts,
        ears=ears,
        db=db,
        vision=vision,
        reminders=reminders,
        db_writer=db_writer,
        lang_state=lang_state,
        assistant_state=assistant_state,
        notes_memory=notes_memory,
        confirm_state=confirm_state,
        volume_state=volume_state,
        playback_state=playback_state,
        context_state=context_state,
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
        "_session_active",
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
        self._session_active = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._session_active.is_set():
                    time.sleep(_WAKE_POLL_INTERVAL_S)
                    continue

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

    def begin_session(self) -> None:
        self._session_active.set()
        self.wake_event.clear()

    def end_session(self) -> None:
        self._session_active.clear()
        self.wake_event.clear()


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
    notes_memory,
    manual_wake_event=None,
    lang_state=None,
    assistant_state=None,
    confirm_state=None,
    volume_state=None,
    playback_state=None,
    context_state=None,
) -> None:
    if manual_wake_event is None:
        manual_wake_event = threading.Event()
    if lang_state is None:
        lang_state = LanguageState()

    ui_update = _UICoalescer(ui_update)

    # CROSS-MODALITY FIX: ye 4 dicts ab build_core_objects() mein bante
    # hain aur yahan parameter ki tarah aate hain, taaki GUI path (Api)
    # aur ye voice loop bilkul SAME instance share karein. Fallback
    # lang_state wale pattern jaisa hi hai: agar koi purana/dusra caller
    # inhe pass na kare to crash nahi hoga, bas purana (per-path isolated,
    # buggy) behavior wapas mil jayega.
    # playback_state = "play next video" YouTube follow-up;
    # confirm_state = close_app/stop_service risky-action yes/cancel flow;
    # volume_state  = mute / pre-mute volume;
    # context_state = "what about jaipur?" type short follow-ups
    #                 (intent_handlers.py -> _h_followup_query / _remember_context).
    if confirm_state is None:
        confirm_state = {}
    if volume_state is None:
        volume_state = {}
    if playback_state is None:
        playback_state = {}
    if context_state is None:
        context_state = {}
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

    # THREAD SUPERVISOR (sara/orchestrator/supervisor.py): every 30s confirms
    # the reminders / notifications / proactive daemon threads are still
    # alive and logs a WARNING if one died. Must never block startup.
    thread_supervisor = None
    try:
        thread_supervisor = ThreadSupervisor(interval_s=30.0)
        thread_supervisor.register(
            "reminders", "sara-reminders", lambda: reminders, restartable=True
        )
        thread_supervisor.register(
            "notifications",
            "sara-notifications",
            get_notification_watcher_if_any,
            restartable=True,
        )
        thread_supervisor.register(
            "proactive", "sara-proactive", lambda: proactive_engine, restartable=True
        )
        thread_supervisor.start()
    except Exception as e:
        print(f"[Supervisor] failed to start (continuing without it): {e}")
        thread_supervisor = None

    # NOTES Q&A (sara/skills/notes_qa.py): notes_memory is now built in
    # build_core_objects() and shared with the brain (see Bug 1 fix in
    # that function) — it arrives here as a parameter instead of being
    # constructed locally, but is otherwise used exactly as before: passed
    # into _handle_command() below via notes_memory so the skill can
    # search() it, and synced once here in a background thread (embedding
    # a folder of notes can take a few seconds and must never delay the
    # assistant becoming responsive).
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
        
    start_memory_consolidation(db, brain, notes_memory)

    aec_active = (
        bool(getattr(Config, "AEC_ENABLED", True))
        and getattr(ears, "_aec", None) is not None
    )
    post_tts_settle_s = (
        _POST_TTS_SETTLE_WITH_AEC_S
        if aec_active
        else float(getattr(Config, "STT_SETTLE_MIN_GAP_S", 1.3))
    )
    if not aec_active:
        logger.warning(
            f"[Logic] AEC unavailable — per-turn mic settle is {post_tts_settle_s}s"
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

            wake_watcher.begin_session()

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
                tts.speak(ack, fast=True, block=True)
            except Exception as e:
                # Ack failing must NEVER stop the command-listening cycle.
                print(f"[Logic] wake-ack speak failed (continuing): {e}")

            empty_retries = 0
            session_start = time.monotonic()
            last_active_time = session_start
            recent_turn_times.clear()
            # FALLBACK-DISCLOSURE FIX: one-shot-per-direction gates, reset
            # at the start of every wake session so a user who wakes SARA
            # again while still degraded (or freshly recovered) hears
            # about it again rather than only once ever.
            session_fallback_disclosed = False
            session_recovery_disclosed = False

            while not stop_event.is_set():
                ui_update("status", "listening")
                ui_update("footer", "Listening...")
                ears.wait_settle(min_gap=post_tts_settle_s)

                # SPECULATIVE WARM-UP: fire ui_update("status", "thinking")
                # the instant a partial (interim) transcript first looks
                # tool-shaped, instead of waiting for the final transcript --
                # shortens the perceived gap before Sara responds. No actual
                # tool runs early; this only nudges the UI status sooner.
                # `_signaled` default-dict is fresh every loop iteration
                # (evaluated at def-time), so it's naturally per-turn.
                def _on_partial_transcript(t, _signaled={"fired": False}):
                    ui_update("transcript_partial", "user", t)
                    if not _signaled["fired"] and has_probable_tool_intent(t):
                        _signaled["fired"] = True
                        ui_update("status", "thinking")

                user_input = ears.listen(
                    mode="command",
                    on_partial_transcript=_on_partial_transcript,
                )
                # NEW: confidence signal riding along on the
                # TranscriptionResult (str subclass) returned by
                # ears.listen() -- see engine.py's TranscriptionResult.
                # Default 1.0 = "fully confident" if this attribute is
                # ever missing, so the new confirmation gate never fires
                # unexpectedly.
                stt_confidence = getattr(user_input, "confidence", 1.0)
                # TASK A/B signal capture: read straight off the
                # TranscriptionResult (str subclass) BEFORE any string
                # ops below (e.g. .strip()/.lower()) risk losing the
                # subclass attributes. Stashed into context_state so
                # dispatcher.py can build the mood/mix hint downstream.
                context_state["turn_signals"] = {
                    "confidence": getattr(user_input, "confidence", None),
                    "speech_rate": getattr(user_input, "speech_rate", None),
                }

                if stop_event.is_set():
                    break

                if not user_input:
                    ui_update("transcript_partial", "user", "")
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
                turn_lang_mode, turn_manual_lang = lang_state.snapshot()
                context_state["turn_lang_mode"] = turn_lang_mode
                if turn_lang_mode == "auto":
                    detected_lang = ears.get_detected_language()
                    tts.set_language(detected_lang)
                    _debug_log(f"[Logic] Language this turn (auto): '{detected_lang}'")
                    # BRAIN-LANGUAGE-SYNC FIX: settings.py's manual toggle
                    # already syncs the LLM brain's reply language
                    # (self.brain.set_language) the instant the user picks
                    # one -- but this auto-detect branch never did, so
                    # brain.get_language() (used by core_wiring.py's own
                    # fallback-disclosure message lookup, and by the LLM's
                    # own reply-language selection) stayed frozen at
                    # Config.SARA_LANGUAGE for the whole session unless the
                    # user manually toggled language at least once. Same
                    # en/hi/hinglish -> english/hindi/hinglish mapping
                    # settings.py uses, confirmed against
                    # sara/audio/stt/helpers.py's _detect_language() /
                    # _lang_from_stt_language(), which are the only two
                    # producers of ears.get_detected_language()'s value.
                    try:
                        _brain_lang_map = {
                            "en": "english",
                            "hi": "hindi",
                            "hinglish": "hinglish",
                        }
                        brain_lang = _brain_lang_map.get(detected_lang)
                        if brain_lang:
                            brain.set_language(brain_lang)
                    except Exception as e:
                        print(f"[Logic] brain.set_language (auto) failed (continuing): {e}")
                else:
                    tts.set_language(turn_manual_lang)
                    _debug_log(
                        f"[Logic] Language this turn (manual override): '{turn_manual_lang}'"
                    )

                # SESSION-CONTROL FIX: exit/sleep/forget-memory/"my name is X"
                # checks used to live HERE, before _handle_command() was ever
                # called -- so they only ever fired for voice input. The GUI's
                # send_text_command() calls _handle_command() directly and
                # never ran through this code, so typing "exit" or "my name
                # is X" in the GUI silently did nothing. Those checks now
                # live inside _handle_command() itself (intent_handlers.py)
                # so both callers get identical behavior. session_control is
                # an out-param: _handle_command() sets
                # session_control["action"] to "exit" or "sleep" when one of
                # those phrases matched; every other case leaves it empty.
                if hasattr(tts, "clear_interrupt"):
                    try:
                        tts.clear_interrupt()
                    except Exception as e:
                        print(f"[Logic] tts.clear_interrupt() failed (continuing): {e}")

                session_control: dict = {}
                with STATE_LOCK:
                    turn_gen, turn_cancel = TURN_STATE.begin()
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
                        context_state=context_state,
                        stt_confidence=stt_confidence,
                        session_control=session_control,
                    )

                if turn_cancel.is_set():
                    logger.info("[Logic] turn cancelled by Stop; dropping late result")
                    continue

                ui_update("transcript", "sara", reply_text or "(no response)")
                db_writer.log_message("user", user_input)
                db_writer.log_message("assistant", reply_text or "")

                # FALLBACK-DISCLOSURE FIX: cheap in-memory flag read (no
                # LLM/network call, no added latency) -- see
                # SaraLLM.used_fallback_last_turn(). Wrapped defensively
                # since a disclosure check must never be able to take
                # down the main conversation loop.
                try:
                    used_fallback = brain.used_fallback_last_turn()
                except Exception as e:
                    used_fallback = None
                    print(f"[Logic] used_fallback_last_turn() check failed (continuing): {e}")

                if used_fallback and not session_fallback_disclosed:
                    disclosure = _FALLBACK_DISCLOSURE_MESSAGES.get(
                        brain.get_language(), _FALLBACK_DISCLOSURE_MESSAGES["english"]
                    )
                    try:
                        tts.speak(disclosure, fast=True)
                    except Exception as e:
                        print(f"[Logic] fallback-disclosure speak failed (continuing): {e}")
                    ui_update("transcript", "sara", disclosure)
                    session_fallback_disclosed = True
                    session_recovery_disclosed = False
                elif used_fallback is False and session_fallback_disclosed and not session_recovery_disclosed:
                    recovery = _FALLBACK_RECOVERY_MESSAGES.get(
                        brain.get_language(), _FALLBACK_RECOVERY_MESSAGES["english"]
                    )
                    try:
                        tts.speak(recovery, fast=True)
                    except Exception as e:
                        print(f"[Logic] fallback-recovery speak failed (continuing): {e}")
                    ui_update("transcript", "sara", recovery)
                    session_recovery_disclosed = True
                    session_fallback_disclosed = False

                # SESSION-CONTROL FIX: act on what _handle_command() decided.
                # "exit" stops the whole app (same as the old inline check);
                # "sleep" just breaks this wake-word loop, same as before.
                action = session_control.pop("action", None)
                if action == "exit":
                    stop_event.set()
                    break
                if action == "sleep":
                    break

                if _record_turn_and_check_runaway():
                    print(
                        "[Logic Warning] Rapid consecutive turns detected "
                        "(possible echo/feedback loop) — forcing sleep as a safety measure."
                    )
                    warn_msg = "I think I might be hearing myself — going back to sleep for a moment."
                    tts.speak(warn_msg, fast=True)
                    ui_update("transcript", "sara", warn_msg)
                    break

            wake_watcher.end_session()

        try:
            ears.close()
        except Exception as e:
            print(f"[Warning] ears.close() failed: {e}")

    except Exception as e:
        logger.critical(f"[Fatal Error \u2014 Sara logic thread] {e}", exc_info=True)
    finally:
        if thread_supervisor is not None:
            thread_supervisor.stop()
        # `reminders` is a _Lazy proxy: this call waits for the background
        # factory to finish and raises RuntimeError if it failed, so it must
        # be wrapped -- a failure here must never block the shutdown steps below.
        try:
            reminders.shutdown()
        except Exception as e:  # noqa: BLE001
            print(f"[Shutdown] reminders shutdown failed: {e}")
        proactive_engine.shutdown()
        if notes_memory is not None:
            notes_memory.close()
        tts.shutdown()
        db_writer.shutdown()
        _shutdown_network_executor()