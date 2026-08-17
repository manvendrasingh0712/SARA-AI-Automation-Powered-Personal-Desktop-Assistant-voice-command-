"""
sara/skills/self_diagnostics.py

On-demand, voice-triggerable version of the same checks health_check.py
already runs automatically at startup (run_startup_diagnostics()).

DESIGN DECISION (refactor vs. call-through): health_check.py was
refactored (option "a" from the task) so each _check_X() function
returns a small list of structured {"name", "friendly_name", "ok",
"detail"} result dicts, IN ADDITION to its existing log/notify side
effects (_fail()/_ok()), which are completely untouched. That refactor
was judged safe because:
  - Every existing _fail()/_ok() call still fires at exactly the same
    place, with exactly the same message, as before -- startup behavior
    (log lines + GUI notifications) is byte-for-byte identical.
  - run_startup_diagnostics() previously returned None, and nothing at
    its one existing call site (gui_main.build_core_objects()) used the
    return value -- so changing None -> list is purely additive.
This skill therefore reuses run_startup_diagnostics() directly instead
of re-implementing the checks, so the two never drift apart.

ui_update=None is passed deliberately: an on-demand check the user
explicitly asked for shouldn't re-fire GUI toast notifications for
issues that (if real) were probably already flagged once at startup --
this skill speaks one consolidated summary instead.

Every check inside health_check.py already has its own try/except (see
that module's docstring), and this skill wraps the whole call in one
more try/except so a totally unexpected crash still can't take down the
voice loop -- it just becomes "sorry, I ran into a problem".
"""
import logging

from health_check import run_startup_diagnostics

logger = logging.getLogger("sara.skills.self_diagnostics")

INTENT_NAME = "self_diagnostics"
PATTERNS = [
    r"check why (?:the )?(?:mic|microphone) (?:isn'?t|is n't|is not) working",
    r"why (?:isn'?t|is n't|is not) (?:my |the )?(?:mic|microphone) working",
    r"check (?:my |the )?(?:system )?health",
    r"(?:run|start) (?:a |the )?(?:system )?diagnostics?",
    r"diagnostics? chalao",
    r"system (?:ki )?jaanch karo",
    r"sara (?:theek|thik) se kaam kar rahi hai(?: kya)?",
    r"is (?:everything|sara) (?:working|ok|okay|fine)\??",
    r"kya sab (?:theek|thik) (?:se )?chal raha hai",
]
GATE = (
    "diagnostic", "health", "microphone", "mic", "jaanch", "chalao",
    "theek", "thik", "working", "everything", "fine",
)


def _join(items: list) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _summarize(results: list) -> str:
    if not results:
        return "I couldn't run any diagnostics just now."

    oks = [r for r in results if r.get("ok")]
    fails = [r for r in results if not r.get("ok")]

    if not fails:
        names = _join([r.get("friendly_name", "") for r in oks])
        return f"Everything looks good — {names} are all working fine." if names else "Everything looks good."

    fail_text = " ".join(r.get("detail", "") for r in fails).strip()
    if not oks:
        return "I found some problems: " + fail_text

    ok_names = _join([r.get("friendly_name", "") for r in oks])
    verb = "looks" if len(oks) == 1 else "look"
    return f"{ok_names} {verb} fine, but {fail_text}"


def handle(match, ctx):
    tts = ctx["tts"]
    ui_update = ctx.get("ui_update")
    if ui_update is not None:
        try:
            ui_update("status", "thinking")
        except Exception:
            pass

    try:
        results = run_startup_diagnostics(ui_update=None)
    except Exception as e:  # noqa: BLE001 -- must never crash the voice loop
        logger.exception(f"[SelfDiagnostics] run_startup_diagnostics crashed: {e}")
        text = "Sorry, I ran into a problem running diagnostics."
        tts.speak(text, fast=True)
        return text

    text = _summarize(results)
    tts.speak(text, fast=True)
    return text