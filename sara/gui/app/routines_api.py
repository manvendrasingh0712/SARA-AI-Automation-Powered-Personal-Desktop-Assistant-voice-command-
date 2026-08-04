"""
sara/gui/app/routines_api.py
ApiRoutinesMixin -- Settings > Routines page CRUD + a "test run" button.
Backed by PreferencesDB's routines table (sara/core/memory.py) and
sara/core/routines.py's run_routine(). Wired into the Api class in
sara/gui/app/engine.py, same shape as every other Api*Mixin here
(reminders.py, notes.py, settings.py, ...).

Every method returns a small JSON-safe dict ({"ok": bool, ...}) rather
than raising, since these are called directly from JS across the
pywebview bridge -- an uncaught exception here would surface as an
opaque bridge error in the browser console instead of a readable
message on the Settings page.
"""
import threading

from .events import _push


class ApiRoutinesMixin:

    # ── Settings > Routines (Automation) page ─────────────────────────

    def list_routines(self):
        """Returns every saved routine's definition (JSON-safe)."""
        try:
            return {"ok": True, "data": self.db.list_routines()}
        except Exception as e:
            print(f"[list_routines error] {e}")
            return {"ok": False, "data": [], "error": str(e)}

    def get_routine(self, name):
        """Returns one routine's definition, or an error if it doesn't exist."""
        try:
            routine = self.db.get_routine(name)
            if routine is None:
                return {"ok": False, "data": None, "error": "Routine not found."}
            return {"ok": True, "data": routine}
        except Exception as e:
            print(f"[get_routine error] {e}")
            return {"ok": False, "data": None, "error": str(e)}

    def save_routine(self, name, label, steps, trigger_time=None):
        """
        Creates or updates a routine. `steps` is validated FIRST -- every
        step's `key`/`name` must genuinely exist right now (in
        SIMPLE_ACTIONS, the registered intents, or the currently-loaded
        skills) -- an invalid step is rejected with an error message
        instead of being silently saved, so a routine can never be saved
        in a state that would just fail at run time.
        """
        try:
            if not name or not str(name).strip():
                return {"ok": False, "error": "Routine name is required."}
            if not isinstance(steps, list) or not steps:
                return {"ok": False, "error": "Routine needs at least one step."}

            error = self._validate_routine_steps(steps)
            if error:
                return {"ok": False, "error": error}

            definition = {
                "name": name,
                "label": label or name,
                "steps": steps,
                "trigger_time": trigger_time or None,
            }
            ok = self.db.save_routine(name, definition)
            if not ok:
                return {"ok": False, "error": "Failed to save routine."}
            return {"ok": True}
        except Exception as e:
            print(f"[save_routine error] {e}")
            return {"ok": False, "error": str(e)}

    def delete_routine(self, name):
        """Deletes a routine by name. Does NOT touch any other routine."""
        try:
            ok = self.db.delete_routine(name)
            return {"ok": ok}
        except Exception as e:
            print(f"[delete_routine error] {e}")
            return {"ok": False, "error": str(e)}

    def run_routine_now(self, name):
        """
        Test-run button. Fires the routine in a background thread (same
        fire-and-forget shape as send_text_command() in core.py) --
        a routine can take several seconds end-to-end (multiple
        sequential, individually-blocking TTS calls), so this must not
        block the pywebview JS-bridge thread. The caller gets an
        immediate {"ok": True} the moment the routine is confirmed to
        exist and has been handed off; the actual spoken output and
        transcript entries stream in afterwards via the normal
        ui_update("transcript", ...) push events, same as any voice
        command's replies.
        """
        try:
            existing = self.db.get_routine(name)
        except Exception as e:
            print(f"[run_routine_now error] {e}")
            return {"ok": False, "error": str(e)}

        if existing is None:
            return {"ok": False, "error": "Routine not found."}

        def _worker():
            from sara.core import routines

            ctx = {
                "brain": self.brain,
                "tts": self.tts,
                "ears": self.ears,
                "db": self.db,
                "reminders": self.reminders,
                "vision": self.vision,
                "ui_update": _push,
                # Reuses the SAME persistent per-session dicts core.py's
                # __init__ now creates once (self.volume_state/playback_state/
                # confirm_state), instead of a fresh {} per call -- so a
                # routine step that touches mute state or the YouTube
                # "next video" follow-up behaves consistently with every
                # other GUI-typed command, not as an isolated bubble.
                "volume_state": self.volume_state,
                "playback_state": self.playback_state,
                "confirm_state": self.confirm_state,
                "user_input": f"[test-run routine: {name}]",
                "notes_memory": None,
            }
            try:
                outcomes = routines.run_routine(name, ctx)
            except Exception as e:  # noqa: BLE001 — background thread, nothing to propagate to
                print(f"[run_routine_now worker error] {e}")
                return

            # run_routine() already speaks every step in order as it runs
            # (see sara/core/routines.py's module docstring) -- just
            # surface each one in the transcript here.
            for outcome in outcomes:
                text = outcome.get("text")
                if not text:
                    continue
                try:
                    _push("transcript", "sara", text)
                except Exception as e:
                    print(f"[run_routine_now ui_update error] {e}")

        threading.Thread(target=_worker, daemon=True, name="sara-routine-test-run").start()
        return {"ok": True}

    # ── Validation ──────────────────────────────────────────────────────

    def _validate_routine_steps(self, steps):
        """
        Returns an error message string if any step is malformed or
        references something that doesn't actually exist right now (a
        deleted/renamed SIMPLE_ACTIONS key, intent, or skill) -- None if
        every step checks out.
        """
        try:
            simple_actions = (
                set(self.system_tools.SIMPLE_ACTIONS.keys()) if self.system_tools else set()
            )
        except Exception:
            simple_actions = set()

        try:
            from sara.orchestrator.intent_handlers import _INTENT_HANDLERS

            intent_names = set(_INTENT_HANDLERS.keys())
        except Exception:
            intent_names = set()

        try:
            from sara.skills import _LOADED_SKILLS

            skill_names = {
                s["intent"] for s in _LOADED_SKILLS
                if s.get("status") == "loaded" and s.get("intent")
            }
        except Exception:
            skill_names = set()

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                return f"Step {i + 1} is not a valid step."
            step_type = step.get("type")

            if step_type == "simple_action":
                key = step.get("key")
                if not key or key not in simple_actions:
                    return f"Step {i + 1}: unknown simple_action '{key}'."
            elif step_type == "intent":
                name = step.get("name")
                if not name or name not in intent_names:
                    return f"Step {i + 1}: unknown intent '{name}'."
            elif step_type == "skill":
                name = step.get("name")
                if not name or name not in skill_names:
                    return f"Step {i + 1}: unknown or disabled skill '{name}'."
            else:
                return f"Step {i + 1}: invalid step type '{step_type}'."

        return None