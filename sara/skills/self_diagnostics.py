"""
sara/skills/self_diagnostics.py

On-demand, voice-triggerable system check.

What runs (all in PARALLEL, each with its own deadline, so one slow or hung
check can't block the rest or the voice loop):
  * the same checks health_check.py runs at startup (run_startup_diagnostics(
    ui_update=None) is reused directly so the two never drift apart; None means
    an on-demand check doesn't re-fire GUI toasts for issues already flagged);
  * long-term memory (sara/core/rag.py LongTermMemory.run_diagnostics(), via
    ctx["notes_memory"]; skipped silently if RAG isn't wired in);
  * internet reachability, GEMINI_API_KEY presence, free disk space and a
    preferences-database read/write round trip.
Every result is normalised to {"name", "friendly_name", "ok", "severity"
("ok" | "warn" | "fail"), "detail", "fix", "latency_ms"}.

The reply is one short spoken summary (first two problems + a fix hint); the
card is a green/yellow/red panel with latency numbers.

"fix it" (within 3 minutes of a check that found problems) performs only SAFE
automatic repairs — re-syncs the notes folder and re-runs every check — and says
plainly what still needs a manual step. It never changes settings or files.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

from health_check import run_startup_diagnostics

from ._framework import SkillContext, SkillResult, skill

logger = logging.getLogger("sara.skills.self_diagnostics")

_CHECK_DEADLINE_S = 20.0
_FIX_WINDOW_S = 180
_last: dict = {"at": 0.0, "results": []}

_FIX_HINTS = (
    (("mic", "microphone"), "Open Settings > Audio and check the microphone device and sensitivity."),
    (("tts", "speech", "voice", "speaker"), "Check your audio output device and volume."),
    (("internet", "network", "wifi"), "Check your Wi-Fi / network connection."),
    (("api key", "gemini_api_key", "api_key"), "Add GEMINI_API_KEY to your .env file, then restart Sara."),
    (("memory", "rag", "embedding"), "Check GEMINI_API_KEY and your internet, then say 'fix it'."),
    (("disk", "storage"), "Free up some disk space."),
    (("database", "preferences"), "Close any other running copy of Sara and check folder permissions."),
)


def fix_hint(result: dict) -> str:
    explicit = result.get("fix")
    if explicit:
        return explicit
    hay = f"{result.get('name', '')} {result.get('friendly_name', '')}".lower()
    for words, hint in _FIX_HINTS:
        if any(w in hay for w in words):
            return hint
    return ""


def _normalise(result: dict, latency_ms: int) -> dict:
    ok = bool(result.get("ok"))
    severity = result.get("severity") or ("ok" if ok else "fail")
    out = dict(result)
    out.update(
        ok=(severity == "ok"),
        severity=severity,
        name=result.get("name", "check"),
        friendly_name=result.get("friendly_name") or result.get("name", "check"),
        detail=result.get("detail", ""),
        latency_ms=int(result.get("latency_ms", latency_ms)),
    )
    out["fix"] = "" if severity == "ok" else fix_hint(out)
    return out


# ── individual checks (each returns a list of result dicts) ────────────────

def _check_internet() -> list:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2.0):
            pass
        return [{"name": "internet", "friendly_name": "internet", "ok": True, "detail": ""}]
    except OSError:
        return [{"name": "internet", "friendly_name": "internet", "ok": False, "severity": "warn",
                 "detail": "I can't reach the internet, so weather, news and cloud features won't work."}]


def _check_api_key() -> list:
    try:
        from config import Config
        key = str(getattr(Config, "GEMINI_API_KEY", "") or "").strip()
    except Exception:  # noqa: BLE001
        key = ""
    if key:
        return [{"name": "api_key", "friendly_name": "API key", "ok": True, "detail": ""}]
    return [{"name": "api_key", "friendly_name": "API key", "ok": False,
             "detail": "GEMINI_API_KEY isn't set, so Sara's cloud brain and notes search can't work."}]


def _check_disk() -> list:
    try:
        from config import Config
        folder = os.path.dirname(os.path.abspath(getattr(Config, "DB_PATH", "."))) or "."
        free_gb = shutil.disk_usage(folder).free / (1024 ** 3)
    except Exception as e:  # noqa: BLE001
        logger.debug("disk check failed: %s", e)
        return []
    if free_gb < 0.2:
        return [{"name": "disk", "friendly_name": "disk space", "ok": False,
                 "detail": f"Only {free_gb:.1f} GB of disk space is left."}]
    if free_gb < 1.0:
        return [{"name": "disk", "friendly_name": "disk space", "ok": False, "severity": "warn",
                 "detail": f"Disk space is getting low ({free_gb:.1f} GB free)."}]
    return [{"name": "disk", "friendly_name": "disk space", "ok": True, "detail": ""}]


def _check_database(db) -> list:
    if db is None or not hasattr(db, "get_preference"):
        return []
    try:
        db.get_preference("skill_enabled:self_diagnostics")
        if hasattr(db, "set_preference"):
            if not db.set_preference("diag_last_run", datetime.now().isoformat()):
                raise RuntimeError("write returned False")
        return [{"name": "database", "friendly_name": "database", "ok": True, "detail": ""}]
    except Exception as e:  # noqa: BLE001
        logger.error("database check failed: %s", e)
        return [{"name": "database", "friendly_name": "database", "ok": False,
                 "detail": "I couldn't read and write my preferences database."}]


def _check_startup() -> list:
    return list(run_startup_diagnostics(ui_update=None) or [])


def _check_rag(notes_memory) -> list:
    if notes_memory is None:
        return []
    return [notes_memory.run_diagnostics()]


# ── runner ─────────────────────────────────────────────────────────────────

def run_all(ctx: SkillContext) -> list:
    """Runs every check in parallel; returns normalised results."""
    tasks = {
        "startup": _check_startup,
        "internet": _check_internet,
        "api_key": _check_api_key,
        "disk": _check_disk,
        "database": lambda: _check_database(ctx.db),
        "rag": lambda: _check_rag(ctx.notes_memory),
    }

    def timed(fn):
        started = time.monotonic()
        raw = fn()
        return raw, int((time.monotonic() - started) * 1000)

    pool = ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="diag")
    futures = {name: pool.submit(timed, fn) for name, fn in tasks.items()}
    try:
        wait(list(futures.values()), timeout=_CHECK_DEADLINE_S)
        results: list = []
        for name, fut in futures.items():
            if not fut.done():
                results.append(_normalise({"name": name, "friendly_name": name, "ok": False,
                                           "severity": "warn",
                                           "detail": f"The {name} check timed out."},
                                          int(_CHECK_DEADLINE_S * 1000)))
                continue
            try:
                raw_list, latency = fut.result()
            except Exception as e:  # noqa: BLE001 -- one crashed check can't kill the rest
                logger.exception("[SelfDiagnostics] check '%s' crashed: %s", name, e)
                results.append(_normalise({"name": name, "friendly_name": name, "ok": False,
                                           "detail": f"The {name} check crashed."}, 0))
                continue
            results.extend(_normalise(r, latency) for r in raw_list if isinstance(r, dict))
        return results
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _join(items: list) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def summarize(results: list, ctx: SkillContext) -> str:
    if not results:
        return ctx.t("I couldn't run any diagnostics just now.", "Abhi diagnostics nahi chala paayi.")
    oks = [r for r in results if r["severity"] == "ok"]
    bad = [r for r in results if r["severity"] != "ok"]
    ok_names = [r["friendly_name"] for r in oks]

    if not bad:
        if len(ok_names) > 5:
            return ctx.t(f"Everything looks good — all {len(ok_names)} checks passed.",
                         f"Sab theek hai — saare {len(ok_names)} checks pass hue.")
        names = _join(ok_names)
        if len(ok_names) == 1:
            return ctx.t(f"Everything looks good — {names} is working fine.",
                         f"Sab theek hai — {names} sahi chal raha hai.")
        return ctx.t(f"Everything looks good — {names} are all working fine.",
                     f"Sab theek hai — {names} sab sahi chal rahe hain.")

    details = " ".join(r["detail"] for r in bad[:2] if r["detail"]).strip()
    extra = len(bad) - 2
    if extra > 0:
        details += ctx.t(f" Plus {extra} more issue{'s' if extra != 1 else ''}.",
                         f" Aur {extra} issue aur hain.")
    hint = next((r["fix"] for r in bad if r.get("fix")), "")
    text = ctx.t("I found some problems: ", "Mujhe kuch problems mili hain: ") + details
    if oks and len(ok_names) <= 5:
        text = (ctx.t(f"{_join(ok_names)} look fine, but ", f"{_join(ok_names)} theek hain, lekin ")
                + details)
    if hint:
        text += " " + hint
    if _auto_fixable(bad):
        text += " " + ctx.t("Say 'fix it' and I'll retry the automatic fixes.",
                            "'Fix it' bolo, main automatic fixes dobara try karungi.")
    return text


def _auto_fixable(bad: list) -> bool:
    return any(
        any(w in f"{r['name']} {r['friendly_name']}".lower() for w in ("memory", "rag", "internet", "notes"))
        for r in bad
    )


def _card(results: list, ctx: SkillContext) -> dict:
    overall = ("fail" if any(r["severity"] == "fail" for r in results) else
               "warn" if any(r["severity"] == "warn" for r in results) else "ok")
    return {
        "type": "health",
        "title": ctx.t("System health", "System health"),
        "overall": overall,
        "rows": [{"name": r["friendly_name"], "severity": r["severity"], "detail": r["detail"],
                  "latency_ms": r["latency_ms"], "fix": r["fix"]} for r in results],
    }


@skill(
    name="self_diagnostics",
    patterns=[
        r"check why (?:the )?(?:mic|microphone) (?:isn'?t|is n't|is not) working",
        r"why (?:isn'?t|is n't|is not) (?:my |the )?(?:mic|microphone) working",
        r"check (?:my |the )?system health",
        r"(?:run|start) (?:a |the )?(?:system )?diagnostics?",
        r"diagnostics? chalao",
        r"system (?:ki )?jaanch karo",
        r"sara (?:theek|thik) se kaam kar rahi hai(?: kya)?",
        r"^\s*is (?:everything|sara) (?:working|ok|okay|fine)\s*\??\s*$",
        r"kya sab (?:theek|thik) (?:se )?chal raha hai",
    ],
    gate=("diagnostic", "health", "microphone", "mic", "jaanch", "chalao",
          "theek", "thik", "working", "everything", "fine"),
    description="Runs parallel health checks (mic, memory, internet, API key, disk, database) and speaks a summary",
    category="system",
    timeout=45,
    examples=("run diagnostics", "check system health", "diagnostics chalao"),
)
def handle(match, ctx: SkillContext):
    ctx.status("thinking")
    ctx.ack("Running a system check.", "System check chala rahi hoon.")
    results = run_all(ctx)
    _last.update(at=time.time(), results=results)
    chips = [ctx.t("Fix it", "Fix it")] if any(r["severity"] != "ok" for r in results) else []
    return SkillResult(text=summarize(results, ctx), card=_card(results, ctx) if results else None, chips=chips)


@skill(
    name="self_diagnostics_fix",
    patterns=[
        r"^\s*(?:please )?(?:fix it|fix that|fix this|try to fix (?:it|that))\s*[.!]*\s*$",
        r"^\s*(?:theek|thik|sahi) karo\s*[.!]*\s*$",
    ],
    gate=("fix", "theek", "thik", "sahi"),
    description="'Fix it' after a system check — retries the safe automatic repairs",
    category="system",
    timeout=60,
)
def handle_fix(match, ctx: SkillContext):
    if time.time() - _last["at"] > _FIX_WINDOW_S or not any(r["severity"] != "ok" for r in _last["results"]):
        return None  # no recent failing check: "fix it" is about something else
    ctx.ack("Trying to fix that.", "Theek karne ki koshish kar rahi hoon.")
    did: list = []
    if ctx.notes_memory is not None:
        try:
            from .notes_qa import sync_notes_folder
            queued = sync_notes_folder(ctx.notes_memory, ctx.db)
            did.append(ctx.t("re-synced your notes", "notes dobara sync kiye")
                       + (f" ({queued} queued)" if queued else ""))
        except Exception as e:  # noqa: BLE001
            logger.error("notes re-sync during fix failed: %s", e)
    results = run_all(ctx)
    _last.update(at=time.time(), results=results)
    still = [r for r in results if r["severity"] != "ok"]
    prefix = (ctx.t("I " + _join(did) + " and re-ran the checks. ",
                    "Maine " + _join(did) + " aur checks dobara chalaye. ")
              if did else ctx.t("I re-ran the checks. ", "Maine checks dobara chalaye. "))
    if not still:
        body = ctx.t("Everything is fine now.", "Ab sab theek hai.")
    else:
        body = ctx.t("Still not fixed: ", "Ye abhi bhi theek nahi hua: ") + " ".join(
            f"{r['friendly_name']} — {r['fix'] or r['detail']}" for r in still[:2])
    return SkillResult(text=prefix + body, card=_card(results, ctx))