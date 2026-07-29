"""
sara.skills
Drop-in plugin skills. To add a new capability to Sara, add ONE new
.py file here — nothing else in the app needs to change. Each module in
this package (other than this file, and any module/file starting with
"_") is expected to define:

    INTENT_NAME: str                          - unique intent name
    PATTERNS:    list[str]                     - regex patterns, same
                                                  shape as an entry in
                                                  sara/core/intent/patterns.py
    GATE:        tuple[str, ...] | None         - optional cheap
                                                  substring pre-filter,
                                                  same shape as
                                                  _INTENT_GATES there
    DESCRIPTION: str                            - OPTIONAL. Short,
                                                  human-readable summary
                                                  shown on the Settings
                                                  > Skills page. Falls
                                                  back to INTENT_NAME if
                                                  omitted — never
                                                  required, never a
                                                  reason to skip a skill.
    def handle(match, ctx) -> str | None        - same contract as every
                                                  handler in
                                                  sara/orchestrator/intent_handlers.py:
                                                  ctx is the same dict
                                                  (brain, tts, ears, db,
                                                  reminders, vision,
                                                  ui_update, volume_state,
                                                  user_input, notes_memory)

On import of this package (triggered once, at the bottom of
sara/orchestrator/intent_handlers.py, after register_intent()/
register_handler() both already exist), every qualifying module here is
auto-discovered and wired into the LIVE intent matcher via
sara.core.intent.register_intent() + intent_handlers.register_handler().
A module that's missing a required attribute, or raises during import or
registration, is skipped with a printed warning — one broken skill can
never prevent the rest of the app (or the other skills) from starting,
matching every other optional-feature guard already in this codebase
(RAG, tool_router, etc).

User-controlled enable/disable
-------------------------------
Before registering a module, _load_all() checks the preference
`skill_enabled:<mod_name>` (same PreferencesDB the rest of the app uses).
If it's explicitly "0", the skill is skipped (never registered) and
logged as user-disabled. If the preference is unset (None), the skill
defaults to enabled — this makes new skill files "just work" without
requiring an explicit opt-in.

IMPORTANT: this check only runs here, at import time, which only happens
once per process. Flipping the toggle from Settings > Skills only writes
the preference — it does NOT hot (un)register anything. The effect is
visible after Sara is restarted. This is a known, intentional limitation
(not a bug); the Settings UI is responsible for telling the user that.

_LOADED_SKILLS
--------------
A module-level list of dicts, one per discovered module (regardless of
whether it ended up registered, user-disabled, or broken), so the
Settings page can show a complete picture:

    {
        "name": <mod_name>,                # e.g. "daily_briefing"
        "intent": <INTENT_NAME or None>,   # None if import itself failed
        "description": <str or None>,
        "enabled": <bool>,                 # current effective state
        "status": "loaded" | "disabled" | "error",
        "error": <str, only present when status == "error">,
    }

Read this from sara/gui/app/settings.py's get_skills_list().

Current skills:
    daily_briefing.py  - weather + reminders + a headline, spoken as one
                          combined summary ("give me my daily briefing")
    notes_qa.py         - answers questions from .txt/.md class notes via
                          the RAG vector store ("what do my notes say
                          about X")
"""
import importlib
import pkgutil

from sara.core.intent import register_intent

_REQUIRED_ATTRS = ("INTENT_NAME", "PATTERNS", "handle")

# Populated fresh on every _load_all() call (i.e. once per process, at
# import time). See the "_LOADED_SKILLS" section of the module docstring
# above for the exact record shape.
_LOADED_SKILLS = []


def _is_skill_user_disabled(mod_name: str) -> bool:
    """
    Reads the `skill_enabled:<mod_name>` preference directly from the
    SQLite preferences DB, via a short-lived, read-only connection opened
    and closed just for this one lookup.

    This intentionally does NOT go through the shared PreferencesDB
    instance the rest of the app uses, because that instance is only
    ever created inside sara.orchestrator.core_wiring.build_core_objects()
    (it spawns a background writer thread on construction) — and this
    package's _load_all() runs at IMPORT time, from the bottom of
    sara.orchestrator.intent_handlers, which is itself imported by
    main.py's re-exports before build_core_objects() has run. There is no
    live PreferencesDB object to borrow yet. Opening a second full
    PreferencesDB() here just to read one value would leak a second
    background writer thread for the app's entire lifetime, so this uses
    sqlite3 directly instead — one connection, one read, closed
    immediately.

    Returns False (== "treat as enabled") for every failure case: db file
    doesn't exist yet (very first run), preferences table doesn't exist
    yet, sqlite3 is locked by the writer thread, or any other error —
    skill discovery must never fail or block because of this.
    """
    try:
        import sqlite3
        from config import Config

        conn = sqlite3.connect(Config.DB_PATH, timeout=1.0)
        try:
            cur = conn.execute(
                "SELECT value FROM preferences WHERE key = ?",
                (f"skill_enabled:{mod_name}",),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        return row is not None and row[0] == "0"
    except Exception as e:
        print(f"[Skills] Could not read enabled-state for {mod_name}: {e}")
        return False


def _load_all() -> None:
    # Imported lazily, inside this function rather than at module top, to
    # avoid a circular-import failure: this package is itself imported
    # FROM sara.orchestrator.intent_handlers (at the bottom of that file,
    # specifically so register_handler already exists by the time we get
    # here) — importing it back at our own module top would try to
    # re-enter that still-initializing module before Python has finished
    # setting it up.
    from sara.orchestrator.intent_handlers import register_handler

    _LOADED_SKILLS.clear()

    for _finder, mod_name, _is_pkg in pkgutil.iter_modules(__path__):
        if mod_name.startswith("_"):
            continue

        user_disabled = _is_skill_user_disabled(mod_name)

        # Import first, regardless of enabled/disabled — the Settings
        # page needs INTENT_NAME/DESCRIPTION to display a disabled skill
        # too (so the user can see what they're toggling back on).
        try:
            module = importlib.import_module(f"{__name__}.{mod_name}")
        except Exception as e:
            print(f"[Skills] Failed to load {mod_name}: {e}")
            _LOADED_SKILLS.append({
                "name": mod_name,
                "intent": None,
                "description": None,
                "enabled": not user_disabled,
                "status": "error",
                "error": str(e),
            })
            continue

        if not all(hasattr(module, attr) for attr in _REQUIRED_ATTRS):
            print(
                f"[Skills] Skipping {mod_name}.py — missing one of "
                f"{_REQUIRED_ATTRS}"
            )
            _LOADED_SKILLS.append({
                "name": mod_name,
                "intent": getattr(module, "INTENT_NAME", None),
                "description": getattr(module, "DESCRIPTION", None),
                "enabled": not user_disabled,
                "status": "error",
                "error": f"missing one of {_REQUIRED_ATTRS}",
            })
            continue

        description = getattr(module, "DESCRIPTION", module.INTENT_NAME)

        if user_disabled:
            print(f"[Skills] '{mod_name}' disabled by user, skipping.")
            _LOADED_SKILLS.append({
                "name": mod_name,
                "intent": module.INTENT_NAME,
                "description": description,
                "enabled": False,
                "status": "disabled",
            })
            continue

        try:
            register_intent(
                module.INTENT_NAME,
                module.PATTERNS,
                gate=getattr(module, "GATE", None),
            )
            register_handler(module.INTENT_NAME, module.handle)
            print(f"[Skills] Loaded '{module.INTENT_NAME}' from {mod_name}.py")
            _LOADED_SKILLS.append({
                "name": mod_name,
                "intent": module.INTENT_NAME,
                "description": description,
                "enabled": True,
                "status": "loaded",
            })
        except Exception as e:  # noqa: BLE001 — one bad skill must not break the rest
            print(f"[Skills] Failed to load {mod_name}: {e}")
            _LOADED_SKILLS.append({
                "name": mod_name,
                "intent": module.INTENT_NAME,
                "description": description,
                "enabled": True,
                "status": "error",
                "error": str(e),
            })


_load_all()