"""
sara.gui.app.modes
ApiModesMixin -- GUI Mode-Switcher (Settings page).

Voice already has a fully working mode-switcher in
sara/orchestrator/intent_handlers.py (_MODE_BUNDLES / _MODE_ALIASES /
_MODE_CONFIRMATIONS / _h_switch_mode()). This mixin does NOT duplicate
any of that -- it imports those same tables so there is exactly ONE
source of truth for what each mode applies, used by both voice and GUI.
See _h_switch_mode()'s own docstring in intent_handlers.py for the full
rationale behind each mode's bundle.
"""
from sara.orchestrator.intent_handlers import (
    _MODE_BUNDLES,
    _MODE_ALIASES,
    _MODE_CONFIRMATIONS,
)


class ApiModesMixin:

    # ── Mode Switcher (Settings page) ───────────────────────────────
    def get_modes_status(self):
        """Returns the currently active mode (for highlighting the right
        button on load) plus the list of valid mode names."""
        try:
            if hasattr(self, "db") and hasattr(self.db, "get_preference"):
                active = self.db.get_preference("active_mode") or "normal"
                return {"ok": True, "active_mode": active, "modes": list(_MODE_BUNDLES.keys())}
        except Exception as e:
            print(f"[get_modes_status error] {e}")
        return {"ok": False, "active_mode": None, "modes": list(_MODE_BUNDLES.keys())}

    def apply_mode(self, mode_name):
        """Applies one of _MODE_BUNDLES's mode bundles via db.set_preference()
        for each key -- exactly mirroring _h_switch_mode() in
        sara/orchestrator/intent_handlers.py. If the bundle includes
        assistant_active, it is also applied live to self.assistant_state
        (mirroring settings.py's set_assistant_active()) so switching mode
        immediately pauses/resumes wake-word listening instead of only
        taking effect after a restart. Gaming Mode's mic_sensitivity
        is also applied live via self.ears if that object is reachable on
        this mixin the same way it is in intent_handlers.py's ctx["ears"];
        if not, it falls back to "applies after a restart", same as the
        voice version does when ctx has no "ears" object."""
        try:
            if not hasattr(self, "_pref_writer"):
                return {"ok": False, "error": "Preferences aren't available right now.", "active_mode": None}

            requested = (mode_name or "").strip().lower()
            resolved_name = _MODE_ALIASES.get(requested, requested)
            bundle = _MODE_BUNDLES.get(resolved_name)
            if bundle is None:
                return {"ok": False, "error": "I don't recognize that mode.", "active_mode": None}

            for key, value in bundle.items():
                self._pref_writer.enqueue(key, value)
            self._pref_writer.enqueue("active_mode", resolved_name)

            confirmation = _MODE_CONFIRMATIONS[resolved_name]

            if "assistant_active" in bundle:
                active_flag = bundle["assistant_active"] == "1"
                assistant_state = getattr(self, "assistant_state", None)
                applied_live = False
                if assistant_state is not None:
                    try:
                        assistant_state.set_active(active_flag)
                        applied_live = True
                    except Exception as e:
                        print(f"[apply_mode live assistant_active error] {e}")
                if not applied_live:
                    confirmation += " Assistant active/paused state will apply after a restart."

            if "mic_sensitivity" in bundle:
                ears = getattr(self, "ears", None)
                applied_live = False
                if ears is not None:
                    try:
                        value = int(bundle["mic_sensitivity"])
                        threshold = max(100, 1000 - (value * 9))
                        if hasattr(ears, "set_manual_energy_threshold"):
                            ears.set_manual_energy_threshold(threshold)
                            applied_live = True
                        elif hasattr(ears, "energy_threshold"):
                            ears.energy_threshold = threshold
                            applied_live = True
                    except Exception as e:
                        print(f"[apply_mode live mic sensitivity error] {e}")
                if not applied_live:
                    confirmation += " Mic sensitivity change will apply after a restart."

            return {"ok": True, "active_mode": resolved_name, "message": confirmation}
        except Exception as e:
            print(f"[apply_mode error] {e}")
            return {"ok": False, "error": "Something went wrong applying that mode.", "active_mode": None}