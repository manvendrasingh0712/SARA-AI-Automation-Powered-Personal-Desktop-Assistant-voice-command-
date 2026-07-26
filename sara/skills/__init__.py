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


def _load_all() -> None:
    # Imported lazily, inside this function rather than at module top, to
    # avoid a circular-import failure: this package is itself imported
    # FROM sara.orchestrator.intent_handlers (at the bottom of that file,
    # specifically so register_handler already exists by the time we get
    # here) — importing it back at our own module top would try to
    # re-enter that still-initializing module before Python has finished
    # setting it up.
    from sara.orchestrator.intent_handlers import register_handler

    for _finder, mod_name, _is_pkg in pkgutil.iter_modules(__path__):
        if mod_name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{mod_name}")
            if not all(hasattr(module, attr) for attr in _REQUIRED_ATTRS):
                print(
                    f"[Skills] Skipping {mod_name}.py — missing one of "
                    f"{_REQUIRED_ATTRS}"
                )
                continue
            register_intent(
                module.INTENT_NAME,
                module.PATTERNS,
                gate=getattr(module, "GATE", None),
            )
            register_handler(module.INTENT_NAME, module.handle)
            print(f"[Skills] Loaded '{module.INTENT_NAME}' from {mod_name}.py")
        except Exception as e:  # noqa: BLE001 — one bad skill must not break the rest
            print(f"[Skills] Failed to load {mod_name}: {e}")


_load_all()
