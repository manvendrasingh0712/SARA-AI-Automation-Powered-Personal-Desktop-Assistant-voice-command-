"""
sara.gui.app.settings
ApiSettingsMixin -- mute/focus mode, generic preference updates, assistant
active-state, mic sensitivity, speech speed, wifi toggle, language,
notes-index status, and skills management.
"""
from .events import _push


class ApiSettingsMixin:

    # ── Topbar icons (mute / focus mode) ──────────────────────────────
    def set_mute(self, muted):
        try:
            self._pref_writer.enqueue("muted", "1" if muted else "0")
            return {"ok": True}
        except Exception as e:
            print(f"[set_mute error] {e}")
            return {"ok": False}

    def set_focus_mode(self, enabled):
        try:
            self._pref_writer.enqueue("focus_mode", "1" if enabled else "0")
            return {"ok": True}
        except Exception as e:
            print(f"[set_focus_mode error] {e}")
            return {"ok": False}

    # ── System page / Voice page toggle switches ──────────────────────
    def update_setting(self, name, value):
        try:
            self._pref_writer.enqueue(f"setting:{name}", "1" if value else "0")
            return {"ok": True}
        except Exception as e:
            print(f"[update_setting error] {e}")
            _push(
                "notification",
                "ti-alert-triangle",
                "#f87171",
                f"Failed to save setting: {name}",
            )
            return {"ok": False}

    # ── Home page: Pause / Resume Listening ─────────────────────────────
    # NEW UI WIRING: genuinely pauses/resumes the wake-word detection
    # loop via the shared AssistantState object (see gui_main.py), not
    # just a cosmetic UI flag. While paused, the wake WORD is ignored by
    # _WakeWatcher, but an explicit manual wake (wake_now(), i.e. tapping
    # the orb or the Wake Sara button) still works, since that's a
    # deliberate user action rather than passive background listening.
    # State is persisted so it survives an app restart.
    def set_assistant_active(self, active):
        try:
            active = bool(active)
            if self.assistant_state is not None:
                self.assistant_state.set_active(active)
            self._pref_writer.enqueue("assistant_active", "1" if active else "0")
            return {"ok": True, "active": active}
        except Exception as e:
            print(f"[set_assistant_active error] {e}")
            return {"ok": False}

    def get_assistant_active(self):
        try:
            if self.assistant_state is not None:
                return {"ok": True, "active": self.assistant_state.is_active()}
            return {"ok": True, "active": True}
        except Exception as e:
            print(f"[get_assistant_active error] {e}")
            return {"ok": True, "active": True}

    # ── UI state restore on boot ─────────────────────────────────────────
    # NEW UI WIRING: set_mute/set_focus_mode/update_setting/set_language/
    # set_mic_sensitivity/set_speech_speed all persist their values via
    # the pref writer, but nothing ever read them back into the UI on the
    # next launch — the BACKEND correctly restored its own state (ears
    # energy threshold, TTS speed/language) via gui_main.py's
    # _apply_saved_preferences(), but the frontend's toggle switches,
    # slider positions, and language picker always silently reset to
    # their hardcoded HTML defaults. This one call lets js/app.js's
    # boot() fully re-sync its own display with what's actually stored.
    #
    # BUGFIX (this revision): "setting:proactive_mode" was missing from
    # this list even though js/app.js's toggleMap already knew how to
    # restore it — so the master Proactive Suggestions toggle silently
    # reset to its HTML default on every relaunch regardless of what the
    # user had actually chosen. Added it here, plus the 4 new per-trigger
    # sub-toggles (battery/reminders/idle/streak) added alongside it, so
    # all 5 now round-trip through a restart correctly.
    def get_ui_settings(self):
        try:
            keys = [
                "muted",
                "focus_mode",
                "language_mode",
                "mic_sensitivity",
                "speech_speed",
                "setting:sound_effects",
                "setting:startup_sound",
                "setting:show_notifications",
                "setting:voice_replies",
                "setting:proactive_mode",
                "setting:proactive_battery",
                "setting:proactive_reminders",
                "setting:proactive_idle",
                "setting:proactive_streak",
            ]
            data = {k: self.db.get_preference(k) for k in keys}
            return {"ok": True, "data": data}
        except Exception as e:
            print(f"[get_ui_settings error] {e}")
            return {"ok": False, "data": {}}

    # ── Voice Control page sliders ─────────────────────────────────────
    def set_mic_sensitivity(self, value):
        """
        value: 0-100 slider position (higher = more sensitive).
        Maps to SpeechToText.energy_threshold (lower threshold = picks
        up quieter speech = more sensitive).
        """
        try:
            value = max(0, min(100, int(value)))
            self._pref_writer.enqueue("mic_sensitivity", str(value))

            threshold = max(100, 1000 - (value * 9))
            if hasattr(self.ears, "set_manual_energy_threshold"):
                self.ears.set_manual_energy_threshold(threshold)
            elif hasattr(self.ears, "energy_threshold"):
                self.ears.energy_threshold = threshold
            return {"ok": True, "threshold": threshold}
        except Exception as e:
            print(f"[set_mic_sensitivity error] {e}")
            return {"ok": False}

    def set_speech_speed(self, value):
        """
        value: 0-100 slider position (higher = faster speech).
        Maps linearly onto Kokoro's speed range
        (_KOKORO_SPEED_MIN.._KOKORO_SPEED_MAX, see gui_main.py), the same
        range gui_main._apply_saved_preferences() uses to restore this
        value on the next app boot — so a value saved from this slider
        round-trips through a restart without drifting.
        """
        try:
            value = max(0, min(100, int(value)))
            self._pref_writer.enqueue("speech_speed", str(value))

            speed_min, speed_max = (
                self.gui_main._KOKORO_SPEED_MIN,
                self.gui_main._KOKORO_SPEED_MAX,
            )
            span = speed_max - speed_min
            speed_val = round(speed_min + (value / 100.0) * span, 3)

            if hasattr(self.tts, "set_speed"):
                self.tts.set_speed(speed_val)

            from config import Config

            Config.KOKORO_SPEED = speed_val
            Config.KOKORO_SPEED_EN = speed_val
            Config.KOKORO_SPEED_HI = speed_val

            return {"ok": True, "speed": speed_val}
        except Exception as e:
            print(f"[set_speech_speed error] {e}")
            return {"ok": False}

    # ── Command Palette: "Toggle Wi-Fi" — genuine OS-level action ──────
    # Unlike take_note/check_calendar/set_timer (which the frontend now
    # routes to existing UI directly), there's no frontend Wi-Fi control,
    # so this really does need a backend method. Uses Windows' built-in
    # `netsh` utility to read the current adapter state and flip it.
    # NOTE: toggling a network adapter on Windows normally requires the
    # app to be running as Administrator — if it isn't, netsh returns a
    # permission error which is caught below and surfaced to the
    # frontend as a normal (non-crashing) failure message.
    def toggle_wifi(self):
        try:
            import subprocess

            show = subprocess.run(
                ["netsh", "interface", "show", "interface"],
                capture_output=True,
                text=True,
                timeout=6,
            )
            iface_name, state = None, None
            for line in show.stdout.splitlines():
                if "Wi-Fi" in line or "Wireless" in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        state = parts[1]  # "Enabled" / "Disabled"
                        iface_name = " ".join(parts[3:])
                    break

            if not iface_name:
                return {"ok": False, "message": "No Wi-Fi adapter found."}

            new_state = "disable" if state == "Enabled" else "enable"
            result = subprocess.run(
                ["netsh", "interface", "set", "interface", iface_name, new_state],
                capture_output=True,
                text=True,
                timeout=6,
            )
            if result.returncode != 0:
                return {
                    "ok": False,
                    "message": "Wi-Fi toggle failed — try running SARA as Administrator.",
                }
            return {
                "ok": True,
                "message": f"Wi-Fi {'disabled' if new_state == 'disable' else 'enabled'}.",
            }
        except Exception as e:
            print(f"[toggle_wifi error] {e}")
            return {"ok": False, "message": "Wi-Fi toggle failed."}

    # ── Language toggle (Home/System page EN⇄HI switch) ────────────────
    # LANGUAGE-SYNC FEATURE: run_sara_logic() already re-detects language
    # every turn when lang_state is in "auto" mode (same detect_language()
    # + tts.set_language() call as before — untouched). Calling this method
    # flips lang_state to "manual" so that per-turn auto-detection is
    # skipped and every future turn uses the language the user picked,
    # until set_language("auto") is called again. Applied immediately to
    # TTS (and to STT/intent-matching too, if the ears object exposes a
    # set_language method — guarded with hasattr since sara/audio/stt.py
    # isn't visible from here to confirm the exact method name).
    #
    # BUGFIX (this revision): also applied immediately to the LLM brain
    # (SaraLLM.set_language), which was previously never called from here
    # — so the personality/response language stayed English no matter what
    # the user picked, even though TTS/STT switched correctly. See the
    # module-level docstring at the top of this file for full details.
    def set_language(self, lang):
        try:
            lang = (lang or "").lower().strip()
            if lang not in ("en", "hi", "auto"):
                return {"ok": False, "message": "Unsupported language."}

            if self.lang_state is None:
                return {"ok": False, "message": "Language state unavailable."}

            if lang == "auto":
                self.lang_state.set_auto()
                self._pref_writer.enqueue("language_mode", "auto")
                # BUGFIX: previously only lang_state was reset; tts/ears
                # kept whatever language was last manually forced until the
                # next conversation turn happened to overwrite it.
                try:
                    self.tts.set_language("auto")
                except Exception as e:
                    print(f"[set_language auto tts error] {e}")
                try:
                    if hasattr(self.ears, "set_language"):
                        self.ears.set_language("auto")
                except Exception as e:
                    print(f"[set_language auto ears error] {e}")
                return {"ok": True, "mode": "auto"}

            self.lang_state.set_manual(lang)
            self._pref_writer.enqueue("language_mode", lang)

            # Apply immediately so voice/matching changes without waiting
            # for the next conversation turn to pick it up.
            try:
                self.tts.set_language(lang)
            except Exception as e:
                print(f"[set_language tts error] {e}")
            try:
                if hasattr(self.ears, "set_language"):
                    self.ears.set_language(lang)
            except Exception as e:
                print(f"[set_language ears error] {e}")

            # BUGFIX: propagate to the LLM brain too, so the reply
            # personality/language actually switches, not just STT/TTS.
            #
            # SaraLLM.set_language() (sara/core/llm.py) only accepts
            # "english" / "hindi" / "hinglish" (its _VALID_LANGS set), while
            # this method speaks the GUI's "en" / "hi" values — so we map
            # locally instead of changing llm.py's accepted values.
            # "auto" is intentionally not forwarded (handled above, before
            # this point, via lang_state.set_auto()).
            #
            # self.brain is a `_Lazy` wrapper (gui_main.py) that may still
            # be loading SaraLLM on a background thread. This whole block
            # is its own try/except, matching the tts/ears calls above, so
            # a brain that isn't ready yet (or raises for any reason) can
            # never block this bridge call or stop TTS/STT from switching.
            try:
                _brain_lang_map = {"en": "english", "hi": "hindi"}
                brain_lang = _brain_lang_map.get(lang)
                if brain_lang and hasattr(self.brain, "set_language"):
                    self.brain.set_language(brain_lang)
            except Exception as e:
                print(f"[set_language brain error] {e}")

            return {"ok": True, "mode": "manual", "lang": lang}
        except Exception as e:
            print(f"[set_language error] {e}")
            return {"ok": False, "message": "Failed to switch language."}

    # ── Settings page: Notes Q&A index status ──────────────────────────
    # "X notes indexed | last synced: <time ago>" card on the Settings
    # page. Delegates the actual counting to
    # sara.skills.notes_qa.get_notes_index_status(), which walks
    # Config.NOTES_FOLDER directly rather than relying on any
    # PreferencesDB prefix-listing method (that method's existence in
    # sara/core/memory.py wasn't confirmed, so nothing here depends on it).
    #
    # KNOWN GAP: this Api instance is not currently constructed with a
    # `notes_memory` reference (bootstrap.py only passes
    # brain/tts/ears/db/vision/reminders/lang_state/assistant_state --
    # notes_memory currently lives only in
    # sara/orchestrator/core_wiring.py). So "enabled" below is a
    # best-effort guess: if a future revision sets `self.notes_memory`
    # on this Api instance (the same way core_wiring.py builds one), this
    # method will automatically start using its real `.enabled` flag
    # instead of the Config-based guess. Until then, this never crashes
    # the Settings page either way -- worst case it just misreports
    # "enabled" when NOTES_FOLDER is configured but RAG is actually off.
    def get_notes_status(self):
        try:
            from sara.skills.notes_qa import get_notes_index_status
        except Exception as e:
            print(f"[get_notes_status import error] {e}")
            return {"ok": True, "enabled": False, "count": 0, "last_synced": None}

        notes_memory = getattr(self, "notes_memory", None)
        if notes_memory is not None:
            enabled = bool(getattr(notes_memory, "enabled", False))
        else:
            try:
                from config import Config

                enabled = bool(getattr(Config, "NOTES_FOLDER", None))
            except Exception as e:
                print(f"[get_notes_status config error] {e}")
                enabled = False

        if not enabled:
            return {"ok": True, "enabled": False, "count": 0, "last_synced": None}

        try:
            status = get_notes_index_status(self.db)
            return {
                "ok": True,
                "enabled": True,
                "count": status.get("count", 0),
                "last_synced": status.get("last_synced"),
            }
        except Exception as e:
            print(f"[get_notes_status error] {e}")
            return {"ok": True, "enabled": True, "count": 0, "last_synced": None}

    # ── Settings page: Skills manager ───────────────────────────────────
    # Backs the "Skills" section: lists every module sara/skills/
    # auto-discovered at startup (loaded, user-disabled, or broken — see
    # sara.skills._LOADED_SKILLS for the exact record shape) and lets the
    # user flip the `skill_enabled:<name>` preference that _load_all()
    # checks on the NEXT boot. This never re-registers anything live —
    # registration only happens once, at import time — so the frontend
    # is responsible for telling the user a restart is needed.
    def get_skills_list(self):
        try:
            from sara.skills import _LOADED_SKILLS

            return {"ok": True, "data": list(_LOADED_SKILLS)}
        except Exception as e:
            print(f"[get_skills_list error] {e}")
            return {"ok": False, "data": []}

    def set_skill_enabled(self, name, enabled):
        try:
            self._pref_writer.enqueue(f"skill_enabled:{name}", "1" if enabled else "0")
            return {"ok": True}
        except Exception as e:
            print(f"[set_skill_enabled error] {e}")
            return {"ok": False}