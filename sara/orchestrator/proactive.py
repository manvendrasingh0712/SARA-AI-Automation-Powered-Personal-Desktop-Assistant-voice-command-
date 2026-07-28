"""
sara.orchestrator.proactive
Proactive Engine — Sara's agentic layer: a background loop that perceives
a few passive signals and speaks up on its own, without a wake word,
when one of them crosses a threshold.

Perceive -> Reason -> Act, every PROACTIVE_CHECK_INTERVAL_S seconds:
    1. Perceive — read battery / upcoming-reminder / idle-time signals.
       All cheap, cached reads; this runs on its own daemon thread (same
       shape as sara/tools/reminders.py's ReminderManager poller), never
       on the main voice loop, so it can't add latency to a real
       conversation turn.
    2. Reason — a rule-based threshold + a per-trigger cooldown decides
       WHETHER to speak (fast, free, no LLM call needed just to check).
       If a trigger fires, an OPTIONAL, isolated LLM call decides WHAT to
       say (falls back to a plain template instantly if disabled, the
       LLM isn't ready, or the call fails for any reason).
    3. Act — speaks via the existing TTS engine and pushes a toast via
       the existing `ui_update("notification", ...)` protocol
       (sara/gui/js/app.js's window.saraEvent already handles this kind).

Gating (all of these silence every trigger, checked fresh every tick):
    - AssistantState.is_active() is False (Home page "Pause Listening")
    - the "focus_mode" preference is on (topbar Focus Mode toggle)
    - the "setting:proactive_mode" preference is explicitly "0" (Settings
      page master toggle — defaults ON; only an explicit off switches it
      off)

Per-trigger gating (checked inside each individual trigger's own check
function, on top of the master gate above):
    - the "setting:proactive_battery" preference is explicitly "0"
    - the "setting:proactive_reminders" preference is explicitly "0"
    - the "setting:proactive_idle" preference is explicitly "0"
    - the "setting:proactive_streak" preference is explicitly "0"
  Same defaulting rule as the master toggle: missing (None) means the
  default, which is enabled — only an explicit "0" (the Settings page
  sub-toggle switched off) disables that one trigger. This keeps existing
  installations that predate these keys behaving exactly as before.

Design note on the LLM phrasing step: it deliberately does NOT go through
SaraLLM.generate_response()/generate_response_stream(). Those append every
call to the real conversation history and (if RAG is enabled) to
long-term memory too, and can block for up to LLM_WARMUP_WAIT_S seconds
on a cold model — none of which is appropriate for a background system
nudge. Instead this module makes its own small, direct, stateless call
via the same module-level client helpers sara/core/llm/engine.py's own
summarize_text() uses (_get_ollama_client/_get_gemini_client), wrapped in
the same never-block, always-fall-back-to-template shape used everywhere
else in this codebase.
"""

import threading
import time
from typing import Any, Callable, Dict, Optional

from config import Config

_DEBUG = getattr(Config, "DEBUG_MODE", False)


class ActivityTracker:
    """
    Thread-safe "time since the last real conversation turn" clock.

    touch() is called by the main voice loop (sara/orchestrator/core_wiring.py
    run_sara_logic()) on wake and on every real user turn. idle_seconds()
    is read by ProactiveEngine's idle/break trigger. Deliberately tiny and
    dependency-free so touching it from the hot conversation loop never
    adds meaningful overhead.
    """

    __slots__ = ("_lock", "_last_ts")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ts = time.monotonic()

    def touch(self) -> None:
        with self._lock:
            self._last_ts = time.monotonic()

    def idle_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_ts


def _quick_llm_rephrase(template: str, lang: str) -> Optional[str]:
    """
    Best-effort, single-attempt, stateless rephrase of `template` into a
    warmer, more natural sentence. Returns None (never raises) on any
    failure so the caller can fall back to the template instantly — this
    must never be allowed to block or crash the proactive thread.

    Intentionally bypasses SaraLLM entirely (see module docstring) and
    talks to the configured backend directly, mirroring the exact
    client-call shape sara/core/llm/engine.py's _summarize_ollama /
    _summarize_gemini already use, so behavior stays consistent with the
    rest of the app (same client helpers, same config knobs).
    """
    try:
        from sara.core.llm.clients import _get_gemini_client, _get_ollama_client

        system_prompt = (
            "You are Sara, a voice assistant. Rephrase the following short "
            "notification into ONE brief, warm, natural sentence (max 20 "
            "words) with the exact same meaning. Do not add anything new. "
            f"Respond only in {lang}, no markdown, no quotes."
        )

        if getattr(Config, "LLM_BACKEND", "ollama") == "ollama":
            client = _get_ollama_client(Config)
            if not client:
                return None
            resp = client.chat(
                model=getattr(Config, "OLLAMA_MODEL", "qwen2.5"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": template},
                ],
                options={"num_predict": 60},
                keep_alive=getattr(Config, "OLLAMA_KEEP_ALIVE", "5m"),
            )
            text = (resp.message.content or "").strip()
        else:
            client = _get_gemini_client(Config)
            if not client:
                return None
            from google.genai import types

            resp = client.models.generate_content(
                model=getattr(Config, "GEMINI_MODEL", "gemini-2.5-flash"),
                contents=[{"role": "user", "parts": [{"text": template}]}],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt, temperature=0.6
                ),
            )
            text = (resp.text or "").strip()

        return text or None
    except Exception as e:  # noqa: BLE001 — must never propagate
        if _DEBUG:
            print(f"[Proactive] LLM rephrase failed, using template: {e}")
        return None


class ProactiveEngine:
    """
    Background agent. Construct once with the shared components
    build_core_objects() already built, start() it once the main voice
    loop is up, and shutdown() it during app teardown — same lifecycle
    shape as sara/tools/reminders.py's ReminderManager.
    """

    def __init__(
        self,
        db,
        reminders,
        tts,
        ui_update: Callable[..., None],
        activity_tracker: ActivityTracker,
        assistant_state: Any = None,
        lang_state: Any = None,
    ) -> None:
        self._db = db
        self._reminders = reminders
        self._tts = tts
        self._ui_update = ui_update
        self._activity = activity_tracker
        self._assistant_state = assistant_state
        self._lang_state = lang_state

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Per-trigger-key cooldown bookkeeping (monotonic timestamps).
        self._last_fired: Dict[str, float] = {}
        # Reminder heads-up de-dupe — once a reminder id has been
        # announced, never announce it again this run. The existing
        # on-time alarm in reminders.py still fires independently at the
        # real due time regardless of whether this heads-up ever ran.
        self._reminder_notified_ids: set = set()

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not getattr(Config, "PROACTIVE_ENABLED", True):
            if _DEBUG:
                print("[Proactive] PROACTIVE_ENABLED=False, not starting.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="sara-proactive"
        )
        self._thread.start()
        if _DEBUG:
            print("[Proactive] Background engine started.")

    def stop(self) -> None:
        self._stop_event.set()

    def shutdown(self) -> None:
        self.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------

    def _poll_loop(self) -> None:
        interval = max(5, int(getattr(Config, "PROACTIVE_CHECK_INTERVAL_S", 60)))
        while not self._stop_event.wait(timeout=interval):
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001 — a bad tick must never kill the thread
                print(f"[Proactive] tick failed: {e}")

    def _tick(self) -> None:
        if not self._enabled_now():
            return
        self._check_battery()
        self._check_upcoming_reminders()
        self._check_idle_break()
        self._check_streak_milestone()

    # ------------------------------------------------------------
    # Gating — respects focus mode, pause state, and the Settings toggles
    # ------------------------------------------------------------

    def _enabled_now(self) -> bool:
        """Master gate — checked once per tick, before any trigger runs."""
        try:
            if self._assistant_state is not None and not self._assistant_state.is_active():
                return False
        except Exception:
            pass
        try:
            if self._db is not None:
                if self._db.get_preference("focus_mode") == "1":
                    return False
                # Default ON: only an explicit "0" (Settings page toggle
                # switched off) disables this. Never having been set at
                # all (None) means the default, which is enabled.
                if self._db.get_preference("setting:proactive_mode") == "0":
                    return False
        except Exception:
            pass
        return True

    def _trigger_enabled(self, key: str) -> bool:
        """
        Per-trigger sub-gate — checked inside each individual trigger's
        own check function, on top of _enabled_now() above. `key` is one
        of "battery", "reminders", "idle", "streak" and maps to the
        Settings page preference "setting:proactive_<key>".

        Same defaulting rule as the master toggle: missing (None) means
        the default, which is enabled — only an explicit "0" disables
        this one trigger. This keeps existing installations that predate
        these per-trigger keys behaving exactly as before.
        """
        try:
            if self._db is not None:
                if self._db.get_preference(f"setting:proactive_{key}") == "0":
                    return False
        except Exception:
            pass
        return True

    def _cooldown_ready(self, key: str) -> bool:
        cooldown_s = max(0, int(getattr(Config, "PROACTIVE_COOLDOWN_MINUTES", 30))) * 60
        last = self._last_fired.get(key)
        return last is None or (time.monotonic() - last) >= cooldown_s

    # ------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------

    def _check_battery(self) -> None:
        if not self._trigger_enabled("battery"):
            return
        if not self._cooldown_ready("battery"):
            return
        try:
            from sara.tools.system.system_info import get_battery_raw

            raw = get_battery_raw()
        except Exception as e:
            if _DEBUG:
                print(f"[Proactive] battery check failed: {e}")
            return
        if raw is None:
            return  # desktop / no battery sensor
        percent, plugged = raw
        threshold = int(getattr(Config, "PROACTIVE_BATTERY_LOW_PERCENT", 15))
        if plugged or percent > threshold:
            return
        template = f"Heads up, battery is at {percent} percent. You might want to plug in soon."
        reason = (
            f"Your battery was at {percent}% and not plugged in "
            f"(the threshold is {threshold}%)."
        )
        self._speak_and_notify(template, icon="ti-battery-1", color="#f87171",
                                trigger="battery", reason=reason)
        self._last_fired["battery"] = time.monotonic()

    def _check_upcoming_reminders(self) -> None:
        if not self._trigger_enabled("reminders"):
            return
        if self._reminders is None or not hasattr(self._reminders, "get_upcoming"):
            return
        lead_minutes = int(getattr(Config, "PROACTIVE_REMINDER_LEAD_MINUTES", 15))
        try:
            upcoming = self._reminders.get_upcoming(lead_minutes)
        except Exception as e:
            if _DEBUG:
                print(f"[Proactive] reminder check failed: {e}")
            return
        for item in upcoming:
            rid = item.get("id")
            if rid is None or rid in self._reminder_notified_ids:
                continue
            text = item.get("text") or "something"
            due_at = item.get("due_at", "")
            template = f'Just a heads-up, "{text}" is coming up soon.'
            reason = (
                f'You have a reminder "{text}" due at {due_at}, '
                f"which is within the next {lead_minutes} minutes."
            )
            self._speak_and_notify(template, icon="ti-alarm", color="#60a5fa",
                                    trigger="reminder", reason=reason)
            self._reminder_notified_ids.add(rid)

    def _check_idle_break(self) -> None:
        if not self._trigger_enabled("idle"):
            return
        if not self._cooldown_ready("idle_break"):
            return
        idle_minutes_needed = int(getattr(Config, "PROACTIVE_IDLE_BREAK_MINUTES", 90))
        idle_s = self._activity.idle_seconds()
        if idle_s < (idle_minutes_needed * 60):
            return
        template = "You've been at it for a while, this might be a good time for a short break."
        reason = (
            f"It had been about {int(idle_s // 60)} minutes since we last talked "
            f"(the threshold is {idle_minutes_needed} minutes)."
        )
        self._speak_and_notify(template, icon="ti-coffee", color="#a78bfa",
                                trigger="idle_break", reason=reason)
        # Resets this trigger's own cooldown clock. If the user then has a
        # real conversation turn, ActivityTracker.touch() drops
        # idle_seconds() back near zero and this naturally won't fire
        # again until another full idle stretch passes; if the user stays
        # away, it won't re-fire until PROACTIVE_COOLDOWN_MINUTES either.
        self._last_fired["idle_break"] = time.monotonic()

    def _check_streak_milestone(self) -> None:
        if not self._trigger_enabled("streak"):
            return
        if self._db is None or not hasattr(self._db, "get_preference"):
            return
        milestone = self._db.get_preference("streak_pending_milestone")
        if not milestone:
            return
        template = f"By the way, we've talked {milestone} days in a row now!"
        reason = f"Your daily talk streak just reached {milestone} days."
        self._speak_and_notify(
            template, icon="ti-flame", color="#fb923c",
            trigger="streak", reason=reason,
        )
        # Clear it so this doesn't repeat on every future tick — only fires
        # once, right after record_interaction_day() sets it for the day
        # the milestone was actually crossed.
        try:
            self._db.set_preference("streak_pending_milestone", "")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Speak + notify (shared by every trigger)
    # ------------------------------------------------------------

    def _speak_and_notify(
        self, template: str, icon: str, color: str, trigger: str, reason: str
    ) -> None:
        text = self._phrase(template)
        try:
            self._tts.speak(text, fast=True)
        except Exception as e:
            print(f"[Proactive] tts.speak failed: {e}")
        try:
            self._ui_update("transcript", "sara", text)
            # Explicit tag (generic "notification" ki jagah) — taaki
            # frontend ko reliably pata chale ki ye ek unprompted nudge
            # hai, icon guess kiye bina.
            self._ui_update("proactive_notification", icon, color, text, trigger)
        except Exception as e:
            print(f"[Proactive] ui_update failed: {e}")
        try:
            if self._db is not None and hasattr(self._db, "log_proactive_event"):
                # Fire-and-forget (wait=False) — logging must never add
                # latency to the nudge itself.
                self._db.log_proactive_event(trigger, text, reason, wait=False)
        except Exception as e:
            if _DEBUG:
                print(f"[Proactive] log_proactive_event failed: {e}")

    def _phrase(self, template: str) -> str:
        if not getattr(Config, "PROACTIVE_LLM_PHRASING", True):
            return template
        lang = "English"
        try:
            if self._lang_state is not None:
                _, code = self._lang_state.snapshot()
                lang = {"hi": "Hindi"}.get(code, "English")
        except Exception:
            pass
        rephrased = _quick_llm_rephrase(template, lang)
        return rephrased or template