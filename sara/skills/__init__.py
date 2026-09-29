"""
sara.skills
Drop-in plugin skills. To add a capability, add ONE new .py file here —
nothing else in the app needs to change. Helper modules whose names start
with "_" (_framework.py) are never treated as skills.

Two ways to write a skill
-------------------------
1. NEW (preferred) — declare it with the framework decorator; see
   sara/skills/_framework.py for everything it does for you:

       from ._framework import skill, SkillContext, SkillResult

       @skill(name="my_intent", patterns=[r"..."], gate=("word",),
              description="shown in Settings", category="general",
              examples=("a phrase that should trigger it",),
              timeout=None, requires=("notes_memory",))
       def handle(match, ctx: SkillContext):
           return SkillResult(text="...", card={...}, chips=[...])

   One module may contain several @skill functions (several intents).

2. LEGACY — module-level INTENT_NAME / PATTERNS / GATE / DESCRIPTION and
   `def handle(match, ctx)` where ctx is the raw dispatcher dict. Still
   fully supported (wrapped so the live enable/disable switch works).

On import (once, from the bottom of sara/orchestrator/intent_handlers.py,
after register_intent()/register_handler() exist) every qualifying module is
wired into the LIVE intent matcher via sara.core.intent.register_intent() +
intent_handlers.register_handler(). A module that fails to import, has an
invalid regex, or reuses an INTENT_NAME is skipped with a logged error — one
broken skill can never stop the app or the other skills.

Enable / disable (NO restart needed)
------------------------------------
Every skill is registered, and each registered handler first checks the
in-memory switch. A disabled skill returns None, which makes
sara/orchestrator/dispatcher.py fall through to normal chat (logged
"skipped"). The initial state comes from the `skill_enabled:<module>`
preference ("0" = disabled); Settings calls set_skill_enabled_live() to flip
it instantly (and separately persists the preference).

Conflict detector
-----------------
After loading, each skill's `examples` are run through the live matcher
(sara.core.intent.detect_intent, falling back to a local regex check). An
example that resolves to a DIFFERENT intent is logged as a warning and kept in
CONFLICTS, so overlapping patterns are found at startup instead of in use.

PyInstaller / frozen builds
---------------------------
Skills are found by scanning the package folder, which PyInstaller's static
analysis never sees, so _BUNDLED_SKILLS below is an explicit fallback list
merged with the scan. When you add a skill, add its module name there too, and
use `hiddenimports=collect_submodules("sara.skills")` in the .spec file.

_LOADED_SKILLS record (one per module), read by sara/gui/app/settings.py:
    {"name", "intent" (first intent or None), "intents": [...],
     "description", "category", "version", "examples": [...],
     "enabled": bool, "status": "loaded" | "disabled" | "error",
     "error": str (only when status == "error")}

Current skills: daily_briefing, joke, notes_qa, recent_actions,
self_diagnostics, streak.
"""
import importlib
import logging
import re
import threading

from sara.core.intent import register_intent

from . import _framework
from ._framework import SkillSpec, build_adapter

logger = logging.getLogger("sara.skills")

_REQUIRED_ATTRS = ("INTENT_NAME", "PATTERNS", "handle")

# Explicit list for frozen (PyInstaller) builds -- see module docstring.
_BUNDLED_SKILLS = (
    "daily_briefing",
    "joke",
    "notes_qa",
    "recent_actions",
    "self_diagnostics",
    "streak",
)

_LOADED_SKILLS: list = []
CONFLICTS: list = []

_state_lock = threading.Lock()
_DISABLED: set = set()          # module names currently switched off (live)


# ── live enable / disable ──────────────────────────────────────────────────

def is_skill_enabled(mod_name: str) -> bool:
    with _state_lock:
        return mod_name not in _DISABLED


def set_skill_enabled_live(mod_name: str, enabled: bool) -> bool:
    """Flips a skill on/off immediately. Returns False for an unknown skill."""
    known = any(r["name"] == mod_name for r in _LOADED_SKILLS)
    with _state_lock:
        if enabled:
            _DISABLED.discard(mod_name)
        else:
            _DISABLED.add(mod_name)
    for rec in _LOADED_SKILLS:
        if rec["name"] == mod_name and rec.get("status") in ("loaded", "disabled"):
            rec["enabled"] = bool(enabled)
            rec["status"] = "loaded" if enabled else "disabled"
    return known


def get_skills_info() -> list:
    """Snapshot for the Settings page (copies, safe to serialise)."""
    return [dict(r) for r in _LOADED_SKILLS]


# ── persisted enable/disable (read once at import) ─────────────────────────

def _read_disabled_skills() -> set:
    """
    Skill module names whose `skill_enabled:<name>` preference is "0", via ONE
    short-lived READ-ONLY sqlite connection (mode=ro, so a missing DB file on
    the very first run is not created as a side effect).

    Not the shared PreferencesDB: that one is only created later, in
    sara.orchestrator.core_wiring.build_core_objects() (it starts a writer
    thread), after this package is already imported. Every failure means
    "nothing disabled" -- discovery must never fail because of this.
    """
    try:
        import sqlite3
        from pathlib import Path

        from config import Config

        uri = Path(Config.DB_PATH).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            rows = conn.execute(
                "SELECT key FROM preferences WHERE key LIKE 'skill_enabled:%' AND value = '0'"
            ).fetchall()
        finally:
            conn.close()
        return {str(r[0]).split(":", 1)[1] for r in rows}
    except Exception as e:  # noqa: BLE001
        logger.info("Could not read skill enabled-state (all skills stay enabled): %s", e)
        return set()


def _is_skill_user_disabled(mod_name: str) -> bool:
    """Backward-compatible single-skill lookup."""
    return mod_name in _read_disabled_skills()


# ── discovery / validation ─────────────────────────────────────────────────

def _discover_module_names() -> tuple:
    """(sorted names, names found on disk)."""
    import pkgutil

    found = []
    try:
        found = [m for _f, m, _p in pkgutil.iter_modules(__path__) if not m.startswith("_")]
    except Exception as e:  # noqa: BLE001
        logger.warning("Skill folder scan failed, using bundled list only: %s", e)
    names = list(found)
    for bundled in _BUNDLED_SKILLS:
        if bundled not in names:
            names.append(bundled)
    return sorted(names), set(found)


def _validate_patterns(patterns) -> str:
    if not isinstance(patterns, (list, tuple)) or not patterns:
        return "PATTERNS must be a non-empty list of regex strings"
    for p in patterns:
        if not isinstance(p, str):
            return f"PATTERNS entry is not a string: {p!r}"
        try:
            re.compile(p)
        except re.error as e:
            return f"invalid regex {p!r}: {e}"
    return ""


def _legacy_spec(module, mod_name: str) -> SkillSpec:
    return SkillSpec(
        name=module.INTENT_NAME,
        patterns=list(module.PATTERNS),
        handler=module.handle,
        gate=getattr(module, "GATE", None),
        description=getattr(module, "DESCRIPTION", module.INTENT_NAME),
        category=getattr(module, "CATEGORY", "general"),
        examples=tuple(getattr(module, "EXAMPLES", ())),
        module=mod_name,
        legacy=True,
    )


def _check_conflicts(registered: list) -> None:
    """Runs every skill's `examples` through the live matcher."""
    CONFLICTS.clear()
    try:
        from sara.core.intent import detect_intent
    except Exception:  # noqa: BLE001
        detect_intent = None

    for spec in registered:
        own = {s.name for s in registered if s.module == spec.module}
        for example in spec.examples:
            resolved = None
            if detect_intent is not None:
                try:
                    resolved = detect_intent(example)[0]
                except Exception:  # noqa: BLE001
                    resolved = None
            if resolved is None:  # local fallback: which skills' regexes hit?
                hits = [
                    s.name for s in registered
                    if any(re.search(p, example, re.IGNORECASE) for p in s.patterns)
                ]
                resolved = spec.name if spec.name in hits else (hits[0] if hits else None)
            if resolved != spec.name and resolved not in own:
                msg = f"example {example!r} of '{spec.name}' resolves to '{resolved}'"
                CONFLICTS.append({"skill": spec.name, "example": example, "resolves_to": resolved})
                logger.warning("Skill pattern conflict: %s", msg)


def _load_all() -> None:
    # Lazy import: this package is itself imported FROM
    # sara.orchestrator.intent_handlers (at the bottom of that file, so
    # register_handler already exists); importing it at module top would
    # re-enter that still-initialising module.
    from sara.orchestrator.intent_handlers import register_handler

    _LOADED_SKILLS.clear()
    persisted_disabled = _read_disabled_skills()
    with _state_lock:
        _DISABLED.clear()
        _DISABLED.update(persisted_disabled)

    names, found_on_disk = _discover_module_names()
    seen_intents: dict = {}
    registered: list = []

    def _error_record(mod_name, intent, description, err, intents=()):
        _LOADED_SKILLS.append({
            "name": mod_name, "intent": intent, "intents": list(intents),
            "description": description, "category": None, "version": None,
            "examples": [], "enabled": mod_name not in persisted_disabled,
            "status": "error", "error": err,
        })

    for mod_name in names:
        full_name = f"{__name__}.{mod_name}"
        _framework.take_specs(mod_name)  # drop leftovers from a previous import
        try:
            module = importlib.import_module(full_name)
        except ModuleNotFoundError as e:
            if mod_name not in found_on_disk and e.name == full_name:
                logger.debug("Bundled skill '%s' not present, skipping.", mod_name)
                continue
            logger.error("Failed to load %s: %s", mod_name, e)
            _error_record(mod_name, None, None, str(e))
            continue
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to load %s: %s", mod_name, e)
            _error_record(mod_name, None, None, str(e))
            continue

        specs = _framework.take_specs(mod_name)
        if all(hasattr(module, a) for a in _REQUIRED_ATTRS) and not any(
            s.name == module.INTENT_NAME for s in specs
        ):
            specs.append(_legacy_spec(module, mod_name))

        if not specs:
            logger.warning("Skipping %s.py — no @skill and no %s", mod_name, _REQUIRED_ATTRS)
            _error_record(
                mod_name, getattr(module, "INTENT_NAME", None),
                getattr(module, "DESCRIPTION", None),
                f"no @skill declared and missing one of {_REQUIRED_ATTRS}",
            )
            continue

        good, problems = [], []
        for spec in specs:
            problem = _validate_patterns(spec.patterns)
            if not problem and spec.name in seen_intents:
                problem = (
                    f"duplicate INTENT_NAME '{spec.name}' "
                    f"(already used by {seen_intents[spec.name]}.py)"
                )
            if problem:
                logger.error("Skipping intent '%s' of %s.py — %s", spec.name, mod_name, problem)
                problems.append(f"{spec.name}: {problem}")
                continue
            try:
                register_intent(spec.name, spec.patterns, gate=spec.gate)
                register_handler(spec.name, build_adapter(spec, is_skill_enabled))
            except Exception as e:  # noqa: BLE001 — one bad skill must not break the rest
                logger.error("Failed to register '%s' of %s.py: %s", spec.name, mod_name, e)
                problems.append(f"{spec.name}: {e}")
                continue
            seen_intents[spec.name] = mod_name
            good.append(spec)
            registered.append(spec)
            logger.info("Loaded '%s' from %s.py", spec.name, mod_name)

        primary = specs[0]
        enabled = is_skill_enabled(mod_name)
        record = {
            "name": mod_name,
            "intent": (good[0].name if good else primary.name),
            "intents": [s.name for s in good],
            "description": primary.description or primary.name,
            "category": primary.category,
            "version": primary.version,
            "examples": [e for s in good for e in s.examples][:6],
            "enabled": enabled,
            "status": ("loaded" if enabled else "disabled") if good else "error",
        }
        if problems:
            record["error"] = "; ".join(problems)
            if not good:
                record["status"] = "error"
        _LOADED_SKILLS.append(record)

    _check_conflicts(registered)


_load_all()