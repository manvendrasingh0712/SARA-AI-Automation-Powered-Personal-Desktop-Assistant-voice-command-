"""
sara.orchestrator.state
Thread-safe shared state: EN/HI language toggle and assistant
active/paused toggle, both driven by the GUI.
"""

import threading

# ── CONCURRENCY FIX (v14, moved from sara/gui/app/core.py) ─────────────
# One process-wide reentrant lock protecting the cross-modality state dicts
# (confirm_state / volume_state / playback_state / context_state) shared
# between the GUI's Api object and the voice loop's run_sara_logic(). Lives
# here (orchestrator layer), not in gui/app/core.py, so a core lock's owner
# isn't the GUI layer.
STATE_LOCK = threading.RLock()


# ----------------------------------------------------------------------------
# Language state
# ----------------------------------------------------------------------------


class LanguageState:
    """mode: "auto" (default) -> per-turn detection drives TTS language,
    exactly as before. "manual" -> the user picked a language via the
    GUI EN/HI toggle; that language is used every turn instead, until
    set_auto() is called again."""

    # See sara/core/llm/engine.py (SaraLLM._serializable) -- self.lang_state
    # is exposed directly off the Api object.
    _serializable = False

    __slots__ = ("_lock", "_mode", "_lang")

    def __init__(self, initial_lang: str = "en"):
        self._lock = threading.Lock()
        self._mode = "auto"
        self._lang = initial_lang

    def set_manual(self, lang: str) -> None:
        with self._lock:
            self._mode = "manual"
            self._lang = lang

    def set_auto(self) -> None:
        with self._lock:
            self._mode = "auto"

    def snapshot(self):
        with self._lock:
            return self._mode, self._lang


# ----------------------------------------------------------------------------
# Assistant state (NEW, v8) — shared between the GUI
# (Api.set_assistant_active in sara/gui/app.py) and this module's
# _WakeWatcher.
#
# When inactive ("paused"), _WakeWatcher stops treating a detected wake
# WORD as a real wake — Sara's passive background listening is genuinely
# off, not just visually hidden. An explicit manual wake (wake_now(), the
# orb tap / "Wake Sara" button) still works even while paused, since
# that's a deliberate user action, not passive listening.
# ----------------------------------------------------------------------------


class AssistantState:
    """active: True (default) -> wake-word detection runs normally.
    False -> wake-word detection is skipped every poll cycle in
    _WakeWatcher, until set_active(True) is called again. Thread-safe
    since the GUI bridge thread and the wake-watcher thread read/write
    this concurrently."""

    # See sara/core/llm/engine.py (SaraLLM._serializable) -- self.assistant_state
    # is exposed directly off the Api object.
    _serializable = False

    __slots__ = ("_lock", "_active")

    def __init__(self, initial_active: bool = True):
        self._lock = threading.Lock()
        self._active = bool(initial_active)

    def set_active(self, value: bool) -> None:
        with self._lock:
            self._active = bool(value)

    def is_active(self) -> bool:
        with self._lock:
            return self._active


# ----------------------------------------------------------------------------
# Turn state -- per-command cancellation + generation id.
# begin() is called when a command starts; cancel() when Stop is pressed.
# Anything long-running checks current_event().is_set() to exit early, and
# late results check is_current(gen) before touching the UI or TTS.
# ----------------------------------------------------------------------------


class TurnState:
    _serializable = False

    __slots__ = ("_lock", "_gen", "_event")

    def __init__(self):
        self._lock = threading.Lock()
        self._gen = 0
        self._event = threading.Event()

    def begin(self):
        """Start a new turn. Returns (generation, cancel_event)."""
        with self._lock:
            self._gen += 1
            self._event = threading.Event()
            return self._gen, self._event

    def cancel(self) -> int:
        """Cancel the current turn (Stop button). Returns its generation."""
        with self._lock:
            self._event.set()
            return self._gen

    def current_event(self) -> threading.Event:
        with self._lock:
            return self._event

    def is_current(self, gen: int) -> bool:
        with self._lock:
            return gen == self._gen and not self._event.is_set()


TURN_STATE = TurnState()
