"""
sara.orchestrator.intent_handlers
One small handler per fast-path regex intent (reminders, notes, clipboard,
weather/news/web, system control, calculator, ...) plus _handle_command(),
the dispatcher that routes a detected intent to its handler.

PLANNING ENGINE INTEGRATION (sara.core.planning)
---------------------------------------------------
_handle_command() now attempts a bounded multi-step plan (via
try_plan_and_execute()) for "chat"-intent messages, BEFORE falling back
to the existing single-tool resolve_tool_call() path. This only engages
when sara.core.planning.trigger.should_attempt_plan() detects a genuine
multi-action signal in the message -- a single-tool request is
completely unaffected and takes the exact same path it always has, with
zero added latency.

SECURITY HARDENING (open_url / open_app / close_app)
--------------------------------------------------------
_h_open_url(), _h_open_app(), and _h_close_app() now validate their
arguments through sara.core.planning.schema.validate_tool_arguments()
before calling the real tool function -- this closes the gap where the
fast-path regex capture alone had no scheme allowlist (open_url) or
application allowlist (open_app/close_app) enforcement. See
sara/core/planning/schema.py's module docstring for the full rationale.
This applies uniformly whether the tool was reached via the fast-path
regex matcher, the LLM single-tool resolver, or a multi-step plan --
all three converge on these same three handler functions.
"""
from .calc_utils import _safe_calc, _parse_duration_to_seconds
from .network_utils import _call_with_timeout
from .tts_worker import TTSWorker

import random
import re
import time
import logging
from datetime import datetime, timedelta

import dateparser

from config import Config

from sara.core import routines
from sara.core.intent import detect_intent
from sara.core.unmatched_log import log_unmatched
from sara.tools.reminders import play_alarm_beep
from sara.tools.clipboard import read_clipboard, write_clipboard
from sara.tools import calendar as calendar_tools
from sara.tools import system as system_tools
from sara.tools import web as web_tools

# Same latency fix as sara/tools/reminders.py's add_reminder(): restrict
# dateparser to only the languages Sara actually needs (en/hi), instead
# of letting it try dozens of locales.
_CALENDAR_DATEPARSER_LANGUAGES = ["en", "hi"]
_DEFAULT_MEETING_DURATION_MINUTES = 30

# PRODUCTION-AUDIT ADDITION (Phase 2): long-term memory (RAG) and the
# LLM tool-calling fallback are both optional, additive features — if
# either module fails to import for any reason (e.g. numpy missing),
# the whole app must still start exactly as before, just without that
# one feature. Both are re-checked as None/False below wherever used.
try:
    from sara.core.rag import LongTermMemory

    _HAS_RAG = True
except Exception as _rag_import_err:  # noqa: BLE001
    LongTermMemory = None
    _HAS_RAG = False
    print(
        f"[Core] sara.core.rag unavailable, long-term memory disabled: {_rag_import_err}"
    )

try:
    from sara.core.tool_router import (
        resolve_tool_call,
        build_fake_match,
        TOOL_NAME_TO_INTENT,
    )

    _HAS_TOOL_ROUTER = True
except Exception as _tool_router_import_err:  # noqa: BLE001
    resolve_tool_call = None
    build_fake_match = None
    TOOL_NAME_TO_INTENT = {}
    _HAS_TOOL_ROUTER = False
    print(
        f"[Core] sara.core.tool_router unavailable, LLM tool-calling fallback "
        f"disabled: {_tool_router_import_err}"
    )

# NEW: multi-step planning engine (sara/core/planning/) -- optional,
# additive, same "never break startup" contract as RAG/tool_router
# above. If this fails to import for any reason, _handle_command() below
# simply never attempts a plan and behaves exactly as it did before this
# feature existed.
try:
    from sara.core.planning import try_plan_and_execute
    from sara.core.planning.schema import (
        PlanValidationError,
        validate_tool_arguments,
    )

    _HAS_PLANNING = True
except Exception as _planning_import_err:  # noqa: BLE001
    try_plan_and_execute = None
    validate_tool_arguments = None

    class PlanValidationError(Exception):  # type: ignore[no-redef]
        """Sentinel fallback so isinstance/except checks below never crash
        when sara.core.planning failed to import."""

    _HAS_PLANNING = False
    print(
        f"[Core] sara.core.planning unavailable, multi-step planning "
        f"disabled (single-tool routing is unaffected): {_planning_import_err}"
    )

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("sara.core_logic")

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_EXIT_WORDS = {
    "exit",
    "quit",
    "stop",
    "goodbye",
    "bye",
    "shutdown",
    "band karo",
    "band kar",
    "alvida",
    "phir milenge",
    "bye bye",
    "बंद करो",
    "अलविदा",
}
_SLEEP_WORDS = {
    "sleep",
    "go to sleep",
    "that's all",
    "nothing else",
    "nevermind",
    "so jao",
    "so ja",
    "bas karo",
    "bas kar",
    "theek hai bas",
    "ठीक है बस",
    "सो जाओ",
}
_FORGET_WORDS = {
    "forget our conversation",
    "clear memory",
    "forget everything",
    "clear our conversation",
    "reset memory",
    "sab bhool jao",
    "memory clear karo",
    "history delete karo",
    "conversation bhool jao",
    "सब भूल जाओ",
}

_STRONG_NAME_PHRASES = (
    "my name is ",
    "call me ",
    "mera naam hai ",
    "mera naam ",
    "mujhe bulao ",
    "main hoon ",
)
_WEAK_NAME_PHRASES = ("i am ", "i'm ")

_WEAK_NAME_BLOCKLIST = {
    "sorry",
    "sure",
    "fine",
    "okay",
    "ok",
    "going",
    "not",
    "just",
    "here",
    "still",
    "really",
    "so",
    "very",
    "trying",
    "about",
    "done",
    "ready",
    "afraid",
    "glad",
    "happy",
    "sad",
    "tired",
    "busy",
    "confused",
    "lost",
    "good",
    "great",
    "alright",
    "kidding",
    "joking",
    "serious",
    "curious",
    "worried",
    "excited",
    "bored",
    "annoyed",
    "stressed",
    "hungry",
}

_MAX_EMPTY_RETRIES = 3
_EMPTY_RETRY_GRACE_S = 8.0
_IDLE_SLEEP_TIMEOUT_S = 180

# CONFIRMATION FLOW: closing/stopping something "risky" (a core system
# process/service, not an everyday app) asks for a yes/no first instead
# of just doing it. Matched as a case-insensitive substring against the
# app/service name, so e.g. "explorer" also catches "explorer.exe".
# Tune these lists as needed -- err on the side of adding, not removing.
_RISKY_APP_KEYWORDS = (
    "explorer", "taskmgr", "task manager", "cmd", "command prompt",
    "powershell", "terminal", "regedit", "registry", "services.msc",
    "control panel", "defender", "antivirus", "firewall", "vpn",
    "svchost", "winlogon", "csrss", "system32",
)
_RISKY_SERVICE_KEYWORDS = (
    "defend", "wuau", "dns", "dhcp", "eventlog", "rpcss", "winmgmt",
    "netlogon", "lanman", "cryptsvc", "bits", "schedule", "power",
    "audiosrv", "spooler", "themes",
)

_CONFIRM_YES_WORDS = {
    "yes", "yeah", "yep", "confirm", "sure", "go ahead", "do it",
    "haan", "ha", "kar do", "kardo", "theek hai", "ok", "okay",
}
_CONFIRM_NO_WORDS = {
    "no", "nope", "cancel", "abort", "never mind", "nevermind",
    "nahi", "mat karo", "rehne do", "chhodo",
}
_CONFIRM_PENDING_TTL_S = 30.0


def _is_risky(name: str, keywords) -> bool:
    lowered = (name or "").lower()
    return any(kw in lowered for kw in keywords)


_WAKE_POLL_INTERVAL_S = 0.05
_WAKE_WAIT_TIMEOUT_S = 0.3

_BARGE_IN_POLL_S = 0.05
_BARGE_IN_GRACE_S = 0.2
_TTS_IDLE_POLL_S = 0.5
_WATCH_IDLE_POLL_S = 0.5
_DB_WRITER_IDLE_POLL_S = 1.0

_NETWORK_TOOL_TIMEOUT_S = 6.0

_CALC_EXPR_RE = re.compile(r"^[\d\s\+\-\*\/\(\)\.\%]+$")
_CALC_MAX_LEN = 200
_CALC_MAX_NUMBER_DIGITS = 12
_CALC_MAX_POW_OPS = 1
_CALC_MAX_EXPONENT_VALUE = 1000
_CALC_EXPONENT_RE = re.compile(r"\*\*\s*([+-]?\d+)")

_OLLAMA_HOST = getattr(Config, "OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = getattr(Config, "OLLAMA_MODEL", "qwen2.5")
_OLLAMA_READY_TIMEOUT_S = 60
_OLLAMA_POLL_INTERVAL_S = 0.25

_DEBUG = getattr(Config, "DEBUG_MODE", False)

# Kokoro speed range. Kokoro's `speed` parameter is DIRECTLY
# proportional to playback rate (1.0 = normal, >1.0 = faster).
_KOKORO_SPEED_MIN = 0.6
_KOKORO_SPEED_MAX = 1.4

_POST_TTS_SETTLE_WITH_AEC_S = 0.3

_THREAD_ERROR_BACKOFF_S = 0.5




# ----------------------------------------------------------------------------
# Command dispatch
# ----------------------------------------------------------------------------


def _quick(ctx: dict, text: str) -> str:
    ctx["ui_update"]("status", "speaking")
    ctx["tts"].speak(text, fast=True)
    return text


_ACK_PHRASES = (
    "On it!",
    "Sure thing!",
    "Ek second...",
    "Done-ish, hold on!",
    "Coming right up!",
)


def _ack(ctx: dict) -> None:
    """
    Fires an instant, non-blocking acknowledgment so the user hears
    something immediately instead of dead silence while a genuinely
    slow action (app launch, service control, network call, screen
    description, ...) runs right after it. Picks a random phrase each
    time so it doesn't feel robotic/repetitive.

    Must NEVER raise: a TTS/UI hiccup here should never block or kill
    the actual command that follows it.
    """
    try:
        ctx["ui_update"]("status", "working")
        ctx["tts"].speak(random.choice(_ACK_PHRASES), fast=True, block=False)
    except Exception as e:
        print(f"[Core] _ack() failed (non-fatal, command continues): {e}")


def _h_reminder_add(match, ctx):
    if not match:
        return None
    return _quick(ctx, ctx["reminders"].add_reminder(match.group(1), match.group(2)))


def _h_reminder_list(match, ctx):
    return _quick(ctx, ctx["reminders"].list_reminders())


def _h_reminder_cancel(match, ctx):
    return _quick(ctx, ctx["reminders"].cancel_all_reminders())


def _h_set_timer(match, ctx):
    if not match:
        return None
    duration_text = match.group(1).strip()
    seconds = _parse_duration_to_seconds(duration_text)
    if not seconds:
        return _quick(
            ctx, f"Sorry, I couldn't understand the duration '{duration_text}'."
        )

    tts, ui_update = ctx["tts"], ctx["ui_update"]

    def _timer_done(msg: str):
        try:
            play_alarm_beep(repetitions=2)
        except Exception as e:
            print(f"[Warning] alarm beep failed: {e}")
        ui_update("status", "speaking")
        tts.speak(msg, fast=True)
        ui_update("transcript", "sara", f"\u23f0 {msg}")

    return _quick(ctx, system_tools.set_timer(seconds, duration_text, _timer_done))


def _h_take_note(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.take_note(match.group(1).strip()))


def _h_read_notes(match, ctx):
    return _quick(ctx, system_tools.read_notes())


def _h_clear_notes(match, ctx):
    return _quick(ctx, system_tools.clear_notes())


def _h_clipboard_read(match, ctx):
    return _quick(ctx, f"Your clipboard contains: {read_clipboard()}")


def _h_clipboard_write(match, ctx):
    if not match:
        return None
    return _quick(ctx, write_clipboard(match.group(1)))


def _h_screenshot_describe(match, ctx):
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    return _quick(ctx, ctx["vision"].describe_screen())


def _h_weather(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    location = match.group(1)
    _remember_context(ctx, "weather", location)
    return _quick(ctx, _call_with_timeout(web_tools.get_weather, location))


def _h_news(match, ctx):
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    if match and match.lastindex and match.lastindex >= 1:
        topic = match.group(1)
        _remember_context(ctx, "news", topic)
        return _quick(ctx, _call_with_timeout(web_tools.get_news, topic))
    return _quick(ctx, _call_with_timeout(web_tools.get_news))


# ── Context Follow-ups ("what about jaipur?" / "aur dilli ka?") ────────
# ctx["context_state"] is a plain dict, created once per run in
# sara/orchestrator/core_wiring.py -- same pattern as playback_state and
# confirm_state -- so it genuinely persists turn-to-turn. Only weather
# and news currently opt in via _remember_context(); any future intent
# can opt in the same way by calling it with its own intent name and
# slot value.
_CONTEXT_TTL_S = 120  # short window: a natural follow-up, not a resumed
                       # conversation from minutes ago


def _remember_context(ctx, intent_name: str, slot_value: str) -> None:
    if not slot_value:
        return
    ctx["context_state"]["last_slot_intent"] = {
        "intent": intent_name,
        "slot": slot_value,
        "ts": time.time(),
    }


# Maps a remembered intent name to the tool function that intent's own
# handler calls, so a follow-up can redo "the same kind of question"
# with a new slot value without duplicating each handler's own
# ack/status/error-handling logic.
_FOLLOWUP_TOOL_BY_INTENT = {
    "weather": web_tools.get_weather,
    "news": web_tools.get_news,
}


def _h_followup_query(match, ctx):
    if not match:
        return None
    state = ctx["context_state"].get("last_slot_intent")
    if not state or (time.time() - state.get("ts", 0) > _CONTEXT_TTL_S):
        return _quick(
            ctx, "I'm not sure what you're asking about — could you rephrase that?"
        )
    tool_fn = _FOLLOWUP_TOOL_BY_INTENT.get(state["intent"])
    if tool_fn is None:
        return _quick(
            ctx, "I'm not sure what you're asking about — could you rephrase that?"
        )
    new_slot = match.group(1).strip()
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    _remember_context(ctx, state["intent"], new_slot)
    return _quick(ctx, _call_with_timeout(tool_fn, new_slot))


def _h_play_youtube(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")
    query = match.group(1).strip()
    result = _call_with_timeout(web_tools.play_youtube, query, tool_name="play_youtube")
    if isinstance(result, str) and result.startswith("Playing"):
        ctx["playback_state"]["youtube"] = {"query": query, "index": 0}
    return _quick(ctx, result)


def _h_play_next_youtube(match, ctx):
    """
    'next video' / 'agla video chalao' follow-up — only makes sense
    right after a play_youtube call, so it needs ctx["playback_state"]
    to know which search to continue.
    """
    state = ctx["playback_state"].get("youtube")
    if not state:
        return _quick(ctx, "I'm not playing anything from YouTube right now.")
    ctx["ui_update"]("status", "thinking")
    result = _call_with_timeout(
        web_tools.play_next_youtube,
        state["query"],
        state["index"],
        tool_name="play_next_youtube",
    )
    if isinstance(result, tuple) and len(result) == 2:
        message, new_index = result
        state["index"] = new_index
    else:
        # _call_with_timeout hit its own timeout/exception path and
        # returned a plain error string instead of our (msg, index) tuple.
        message = result
    return _quick(ctx, message)


def _h_play_spotify(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")
    return _quick(
        ctx, _call_with_timeout(web_tools.play_spotify, match.group(1).strip())
    )


def _h_web_search(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    return _quick(ctx, _call_with_timeout(web_tools.search_web, match.group(1)))


def _h_summarize_url(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")
    page_text = _call_with_timeout(web_tools.read_webpage, match.group(1))
    if isinstance(page_text, str) and (
        page_text.startswith("Error:") or page_text.startswith("Sorry,")
    ):
        return _quick(ctx, page_text)
    return _quick(ctx, ctx["brain"].summarize_text(page_text))


def _h_open_url(match, ctx):
    """
    Opens a URL captured by the open_url fast-path regex (or resolved by
    the LLM single-tool router, or proposed by a multi-step plan -- all
    three converge here).

    SECURITY HARDENING: the captured URL is validated via
    sara.core.planning.schema.validate_tool_arguments() before being
    passed to web_tools.open_url() -- only http:// and https:// schemes
    are ever allowed through; javascript:/data:/file:/vbscript:/etc. are
    rejected with a clear spoken message instead of being opened. If
    sara.core.planning failed to import (_HAS_PLANNING is False), this
    degrades to the original unvalidated behavior rather than crashing
    -- matching this codebase's "optional feature missing must never
    break the app" convention.
    """
    if not match:
        return None
    raw_url = match.group(1)
    if _HAS_PLANNING and validate_tool_arguments is not None:
        try:
            validated = validate_tool_arguments("open_url", {"url": raw_url})
            raw_url = validated["url"]
        except PlanValidationError as e:
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] open_url validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely open that link.")
    _ack(ctx)
    return _quick(ctx, web_tools.open_url(raw_url))


def _h_calculator(match, ctx):
    if not match:
        return None
    expr = match.group(1).strip() if match.lastindex and match.lastindex >= 1 else ""
    if expr and expr.lower() not in ("calculator", "calc"):
        return _quick(ctx, _safe_calc(expr))
    return _quick(ctx, system_tools.open_application("calc"))


def _h_system_info(match, ctx):
    return _quick(ctx, system_tools.get_system_summary())


def _h_set_volume(match, ctx):
    if not match:
        return None
    volume_state = ctx["volume_state"]
    try:
        level = int(match.group(1))
        volume_state["last"] = level
        return _quick(ctx, system_tools.set_volume(level))
    except (TypeError, ValueError, IndexError):
        lowered_input = ctx["user_input"].lower()
        if any(w in lowered_input for w in ("up", "increase", "raise", "louder")):
            return _quick(ctx, system_tools.adjust_volume(10))
        if any(
            w in lowered_input
            for w in ("down", "decrease", "lower", "reduce", "quieter")
        ):
            return _quick(ctx, system_tools.adjust_volume(-10))
        return _quick(ctx, "What volume level would you like?")


def _h_set_brightness(match, ctx):
    if not match:
        return None
    try:
        return _quick(ctx, system_tools.set_brightness(int(match.group(1))))
    except (TypeError, ValueError, IndexError):
        return _quick(ctx, "What brightness level would you like?")


def _h_mute(match, ctx):
    volume_state = ctx["volume_state"]
    get_vol_func = getattr(system_tools, "get_volume", None)
    if get_vol_func:
        try:
            current = get_vol_func()
            if current and current > 0:
                volume_state["pre_mute"] = current
        except Exception:
            pass
    return _quick(ctx, system_tools.set_volume(0))


def _h_unmute(match, ctx):
    restore_to = ctx["volume_state"].get("pre_mute", 50)
    return _quick(ctx, system_tools.set_volume(restore_to))


def _h_open_app(match, ctx):
    """
    Launches an application captured by the open_app fast-path regex (or
    resolved by the LLM single-tool router, or proposed by a multi-step
    plan -- all three converge here).

    SECURITY HARDENING: the captured application name is validated
    against Config.APP_LAUNCH_ALLOWLIST via
    sara.core.planning.schema.validate_tool_arguments() before being
    passed to system_tools.open_application() -- an unrecognized
    application name is rejected with a clear spoken message instead of
    being launched. Set Config.APP_LAUNCH_ALLOWLIST_ENABLED=False to
    disable this enforcement entirely (not recommended). If
    sara.core.planning failed to import, this degrades to the original
    unvalidated behavior rather than crashing.
    """
    if not match:
        return None
    target = match.group(1).strip()
    if _HAS_PLANNING and validate_tool_arguments is not None:
        try:
            allowed_apps = frozenset(getattr(Config, "APP_LAUNCH_ALLOWLIST", []))
            allowlist_enabled = getattr(Config, "APP_LAUNCH_ALLOWLIST_ENABLED", True)
            validated = validate_tool_arguments(
                "open_app",
                {"target": target},
                allowed_apps=allowed_apps,
                app_allowlist_enabled=allowlist_enabled,
            )
            target = validated["target"]
        except PlanValidationError as e:
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] open_app validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely open that application.")
    _ack(ctx)
    return _quick(
        ctx,
        _call_with_timeout(
            system_tools.open_application, target, tool_name="open_application"
        ),
    )


def _h_close_app(match, ctx):
    """
    Closes an application captured by the close_app fast-path regex (or
    resolved by the LLM single-tool router, or proposed by a multi-step
    plan -- all three converge here).

    SECURITY HARDENING: same allowlist validation as _h_open_app() above,
    applied BEFORE the existing risky-app confirmation flow -- an
    unrecognized application is rejected outright rather than reaching
    the "are you sure?" prompt at all.
    """
    if not match:
        return None
    app_name = match.group(1).strip()
    if _HAS_PLANNING and validate_tool_arguments is not None:
        try:
            allowed_apps = frozenset(getattr(Config, "APP_LAUNCH_ALLOWLIST", []))
            allowlist_enabled = getattr(Config, "APP_LAUNCH_ALLOWLIST_ENABLED", True)
            validated = validate_tool_arguments(
                "close_app",
                {"target": app_name},
                allowed_apps=allowed_apps,
                app_allowlist_enabled=allowlist_enabled,
            )
            app_name = validated["target"]
        except PlanValidationError as e:
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] close_app validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely close that application.")
    if _is_risky(app_name, _RISKY_APP_KEYWORDS):
        ctx["confirm_state"]["pending"] = {
            "action": "close_app",
            "target": app_name,
            "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
        }
        return _quick(
            ctx, f"{app_name} is a system app -- are you sure you want to close it? Say yes or cancel."
        )
    _ack(ctx)
    return _quick(
        ctx,
        _call_with_timeout(
            system_tools.close_application, app_name, tool_name="close_application"
        ),
    )


def _h_typing_text(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.type_text(match.group(1).strip()))


def _h_press_key(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.press_key(match.group(1).strip()))


def _h_find_file(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    return _quick(
        ctx,
        _call_with_timeout(system_tools.find_file, match.group(1).strip(), tool_name="find_file"),
    )

def _h_start_service(match, ctx):
    if not match:
        return None
    _ack(ctx)
    return _quick(
        ctx,
        _call_with_timeout(
            system_tools.start_service, match.group(1).strip(), tool_name="start_service"
        ),
    )


def _h_stop_service(match, ctx):
    if not match:
        return None
    service_name = match.group(1).strip()
    if _is_risky(service_name, _RISKY_SERVICE_KEYWORDS):
        ctx["confirm_state"]["pending"] = {
            "action": "stop_service",
            "target": service_name,
            "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
        }
        return _quick(
            ctx,
            f"{service_name} looks like a core system service -- are you sure you want to stop it? Say yes or cancel.",
        )
    _ack(ctx)
    return _quick(
        ctx,
        _call_with_timeout(
            system_tools.stop_service, service_name, tool_name="stop_service"
        ),
    )

def _h_restart_application(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    return _quick(
        ctx,
        _call_with_timeout(
            system_tools.restart_application, match.group(1).strip(), tool_name="restart_application"
        ),
    )


def _h_switch_to_application(match, ctx):
    if not match:
        return None
    _ack(ctx)
    return _quick(
        ctx,
        _call_with_timeout(
            system_tools.switch_to_application, match.group(1).strip(), tool_name="switch_to_application"
        ),
    )


def _h_move_resize_window(match, ctx):
    if not match:
        return None
    _ack(ctx)
    app_name, position = match.group(1).strip(), match.group(2).strip()
    return _quick(ctx, system_tools.move_window(app_name, position))


def _h_always_on_top(match, ctx):
    if not match:
        return None
    _ack(ctx)
    return _quick(ctx, system_tools.toggle_always_on_top(match.group(1).strip()))


def _h_fullscreen(match, ctx):
    _ack(ctx)
    app_name = match.group(1).strip() if (match and match.lastindex) else ""
    return _quick(ctx, system_tools.toggle_fullscreen(app_name))
    
def _h_time_query(match, ctx):
    return _quick(ctx, f"It's {datetime.now().strftime('%I:%M %p')}.")


def _h_date_query(match, ctx):
    return _quick(ctx, f"Today is {datetime.now().strftime('%A, %B %d, %Y')}.")


def _h_why_proactive(match, ctx):
    """
    Explainable-AI handler for "why did you say that?" / "kyu bola?".
    Looks up the most recent sara/orchestrator/proactive.py nudge (logged
    via db.log_proactive_event) and speaks the specific, human-readable
    reason that was recorded for it at the time it fired.
    """
    db = ctx["db"]
    event = None
    if db is not None and hasattr(db, "get_last_proactive_event"):
        try:
            event = db.get_last_proactive_event()
        except Exception as e:
            print(f"[Proactive] get_last_proactive_event failed: {e}")
    if not event:
        return _quick(ctx, "I haven't said anything on my own recently.")
    reason = event.get("reason") or "I don't have a specific reason recorded for that one."
    return _quick(ctx, reason)


def _h_calendar_today(match, ctx):
    """
    "aaj ka schedule batao" / "what's on my calendar today" -- reads
    today's real Google Calendar events via sara/tools/calendar.py and
    speaks a short human-readable summary. _ack() first since this is a
    real network call to the Calendar API, not an instant local lookup.
    """
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    events = calendar_tools.get_today_events()
    if not events:
        status = calendar_tools.get_calendar_status()
        if not status.get("connected"):
            return _quick(
                ctx,
                "Calendar isn't connected yet. Add credentials.json and try again.",
            )
        return _quick(ctx, "You have nothing on your calendar today.")

    lines = []
    for event in events[:5]:
        summary = event.get("summary") or "an event"
        start_iso = event.get("start", "")
        try:
            start_dt = datetime.fromisoformat(start_iso)
            when = start_dt.strftime("%I:%M %p")
        except (TypeError, ValueError):
            when = ""
        lines.append(f"{summary}{f' at {when}' if when else ''}")

    return _quick(ctx, "Here's today's schedule: " + "; ".join(lines) + ".")


def _h_calendar_create(match, ctx):
    """
    "kal 3 baje meeting set karo" / "schedule a meeting tomorrow at 5pm
    called <title>" -- parses the natural-language time via dateparser
    (exact same restricted-language pattern as sara/tools/reminders.py's
    add_reminder()) and creates a real Google Calendar event via
    sara/tools/calendar.py. ACCURACY FIRST: if the time can't be
    confidently parsed, this never guesses -- it asks the user to
    rephrase instead of creating an event at the wrong time.
    """
    if not match:
        return None

    groups = match.groupdict() if match.groupdict() else {}
    when_text = (groups.get("when") or "").strip()
    title = (groups.get("title") or "").strip()

    if not when_text:
        return _quick(ctx, "When would you like to schedule that meeting?")

    _ack(ctx)
    ctx["ui_update"]("status", "thinking")

    start_dt = dateparser.parse(
        when_text,
        languages=_CALENDAR_DATEPARSER_LANGUAGES,
        settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
    )

    if not start_dt:
        return _quick(
            ctx, f"I couldn't understand the time '{when_text}'. Could you rephrase it?"
        )

    if start_dt <= datetime.now():
        return _quick(ctx, "That time has already passed. Please give me a future time.")

    end_dt = start_dt + timedelta(minutes=_DEFAULT_MEETING_DURATION_MINUTES)
    result = calendar_tools.create_event(title or "Meeting", start_dt, end_dt)
    return _quick(ctx, result.get("message", "Sorry, I couldn't create that event."))


def _h_run_routine(match, ctx):
    if not match:
        return None
    requested = (match.group(1) or "").strip()
    if not requested:
        return _quick(ctx, "Which routine should I run?")

    db = ctx.get("db")
    resolved = routines.resolve_routine_name(db, requested)
    if not resolved:
        return _quick(ctx, f"I couldn't find a routine called '{requested}'.")

    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    # run_routine() already speaks every step in order as it runs (see
    # sara/core/routines.py's module docstring) -- do NOT speak these
    # again here, just surface each one in the transcript.
    outcomes = routines.run_routine(resolved, ctx)

    spoken_texts = []
    for outcome in outcomes:
        text = outcome.get("text")
        if not text:
            continue
        try:
            ctx["ui_update"]("transcript", "sara", text)
        except Exception as e:
            print(f"[Routines] transcript push failed: {e}")
        spoken_texts.append(text)

    return " ".join(spoken_texts) if spoken_texts else "Routine finished."


_INTENT_HANDLERS = {
    "reminder_add": _h_reminder_add,
    "reminder_list": _h_reminder_list,
    "reminder_cancel": _h_reminder_cancel,
    "set_timer": _h_set_timer,
    "take_note": _h_take_note,
    "read_notes": _h_read_notes,
    "clear_notes": _h_clear_notes,
    "clipboard_read": _h_clipboard_read,
    "clipboard_write": _h_clipboard_write,
    "screenshot_describe": _h_screenshot_describe,
    "weather": _h_weather,
    "news": _h_news,
    "followup_query": _h_followup_query,
    "play_youtube": _h_play_youtube,
    "play_next_youtube": _h_play_next_youtube,
    "play_spotify": _h_play_spotify,
    "web_search": _h_web_search,
    "summarize_url": _h_summarize_url,
    "open_url": _h_open_url,
    "calculator": _h_calculator,
    "system_info": _h_system_info,
    "set_volume": _h_set_volume,
    "set_brightness": _h_set_brightness,
    "mute": _h_mute,
    "unmute": _h_unmute,
    "open_app": _h_open_app,
    "close_app": _h_close_app,
    "typing_text": _h_typing_text,
    "press_key": _h_press_key,
    "find_file": _h_find_file,
    "start_service": _h_start_service,
    "stop_service": _h_stop_service,
    "restart_application": _h_restart_application,
    "switch_to_application": _h_switch_to_application,
    "move_window": _h_move_resize_window,
    "resize_window": _h_move_resize_window,
    "always_on_top": _h_always_on_top,
    "toggle_fullscreen": _h_fullscreen,
    "time_query": _h_time_query,
    "date_query": _h_date_query,
    "why_proactive": _h_why_proactive,
    "calendar_today": _h_calendar_today,
    "calendar_create": _h_calendar_create,
    "run_routine": _h_run_routine,
}


def register_handler(name: str, fn) -> None:
    """
    Registers (or replaces) the handler function for intent `name` —
    the sibling of sara.core.intent.register_intent(), used by
    sara/skills/__init__.py's plugin auto-discovery so a new skill file
    can wire up its own handle() without editing this file's
    _INTENT_HANDLERS table by hand. `fn` must accept (match, ctx) and
    return a string (or None to fall through, same contract as every
    handler above).
    """
    _INTENT_HANDLERS[name] = fn


# Auto-discovers and registers every skill in sara/skills/ (Daily
# Briefing, Notes Q&A, and any future drop-in skill file) via
# register_intent()/register_handler() above. Imported here, at the
# bottom of this module, specifically so both functions already exist by
# the time sara/skills/__init__.py runs its discovery loop. Wrapped in
# try/except for the same reason the RAG/tool_router imports above are:
# a missing or broken skill must degrade to "that one skill doesn't
# work", never to "the app won't start".
try:
    import importlib

    importlib.import_module("sara.skills")
except Exception as _skills_import_err:  # noqa: BLE001
    print(f"[Core] sara.skills unavailable, plugin skills disabled: {_skills_import_err}")


def _build_plan_dispatch_fn(ctx: dict):
    """
    Builds the DispatchFn callback sara.core.planning.try_plan_and_execute()
    needs to actually execute a proposed plan's steps.

    Kept as its own small factory (rather than inlined in _handle_command
    below) so its contract is easy to unit-test in isolation: given a
    tool name from TOOL_NAME_TO_INTENT and a validated arguments dict, it
    builds a fake regex match via tool_router.build_fake_match() (the
    exact same mechanism the existing single-tool LLM resolver already
    uses) and calls the matching entry in _INTENT_HANDLERS -- i.e. a
    planned step reaches the real tool function through IDENTICAL code
    to a fast-path or single-tool-resolved command. There is no separate
    "plan execution" code path for the tool functions themselves; only
    the decision of WHICH tools to call and in WHAT order is new.

    Raises RuntimeError (never returns None) on any failure -- the
    executor in sara.core.planning.executor treats any raised exception
    from this callback identically to a tool function raising directly,
    triggering its existing retry/skip/partial-success logic.
    """

    def _dispatch(tool_name: str, tool_args: dict) -> str:
        mapped_intent = TOOL_NAME_TO_INTENT.get(tool_name)
        if not mapped_intent:
            raise RuntimeError(f"Unknown tool '{tool_name}' -- no mapped intent.")
        tool_handler = _INTENT_HANDLERS.get(mapped_intent)
        if tool_handler is None:
            raise RuntimeError(f"No handler registered for intent '{mapped_intent}'.")
        fake_match = build_fake_match(tool_name, tool_args)
        result = tool_handler(fake_match, ctx)
        if result is None:
            raise RuntimeError(
                f"Handler for intent '{mapped_intent}' (tool '{tool_name}') "
                f"returned no result."
            )
        return result

    return _dispatch


def _handle_command(
    user_input,
    brain,
    tts: TTSWorker,
    ears,
    db,
    reminders,
    vision,
    ui_update,
    volume_state: dict,
    notes_memory=None,
    playback_state: dict = None,
    confirm_state: dict = None,
    context_state: dict = None,
) -> str:
    if playback_state is None:
        playback_state = {}
    if confirm_state is None:
        confirm_state = {}
    if context_state is None:
        context_state = {}

    ctx = {
        "brain": brain,
        "tts": tts,
        "ears": ears,
        "db": db,
        "reminders": reminders,
        "vision": vision,
        "ui_update": ui_update,
        "volume_state": volume_state,
        "user_input": user_input,
        "notes_memory": notes_memory,
        "playback_state": playback_state,
        "confirm_state": confirm_state,
        "context_state": context_state,
    }

    # ── Pending destructive-action confirmation (close_app / stop_service
    # on something risky) takes priority over normal intent detection --
    # if Sara just asked "are you sure?", this turn's job is to answer
    # that, not to be re-parsed as a brand-new command. Expires after
    # _CONFIRM_PENDING_TTL_S so a stale "yes" minutes later doesn't
    # accidentally trigger an old, forgotten action.
    pending = confirm_state.get("pending")
    if pending:
        if time.time() > pending.get("expires_at", 0):
            confirm_state.pop("pending", None)
        else:
            reply = (user_input or "").strip().lower()
            if reply in _CONFIRM_YES_WORDS:
                confirm_state.pop("pending", None)
                action, target = pending["action"], pending["target"]
                _ack(ctx)
                if action == "close_app":
                    result = _call_with_timeout(
                        system_tools.close_application, target, tool_name="close_application"
                    )
                elif action == "stop_service":
                    result = _call_with_timeout(
                        system_tools.stop_service, target, tool_name="stop_service"
                    )
                else:
                    result = "Sorry, I lost track of what I was confirming."
                return _quick(ctx, result)
            if reply in _CONFIRM_NO_WORDS:
                confirm_state.pop("pending", None)
                return _quick(ctx, "Okay, cancelled.")
            # Anything else: fall through to normal intent detection below
            # (user changed their mind / asked something unrelated) but
            # drop the stale pending confirmation so it can't fire later.
            confirm_state.pop("pending", None)

    intent, match = detect_intent(user_input)

    handler = _INTENT_HANDLERS.get(intent)
    if handler is not None:
        try:
            result = handler(match, ctx)
        except Exception as e:
            # A single bad handler (built-in or a sara/skills/ plugin) must
            # never be allowed to propagate up into run_sara_logic()'s
            # outer try/except, which treats any escaped exception as
            # fatal and exits the ENTIRE main voice loop thread. One
            # failed command should just be one failed command.
            print(f"[Core] Handler for intent '{intent}' raised: {e}")
            result = _quick(
                ctx, "Sorry, I ran into a problem with that. Let's try something else."
            )
        if result is not None:
            return result

    if intent in system_tools.SIMPLE_ACTIONS:
        try:
            return _quick(ctx, system_tools.SIMPLE_ACTIONS[intent]())
        except Exception as e:
            print(f"[Core] SIMPLE_ACTIONS['{intent}'] raised: {e}")
            return _quick(
                ctx, "Sorry, I ran into a problem with that. Let's try something else."
            )

    # ── Multi-step planning engine ──────────────────────────────────────
    # Only attempted for "chat"-intent messages (the fast-path regex
    # matcher above found nothing), and only actually engages an LLM
    # call if sara.core.planning.trigger.should_attempt_plan() detects a
    # genuine multi-action signal -- see that module's docstring for the
    # exact trigger conditions. A single-tool "chat" message costs
    # nothing extra here: should_attempt_plan() is a pure string check
    # that returns False before any LLM call is even considered, and
    # execution falls straight through to the existing
    # resolve_tool_call() block below, completely unchanged.
    #
    # try_plan_and_execute() NEVER raises (see its own docstring) -- the
    # try/except here is a second, redundant safety net purely so a
    # hypothetical future bug in that contract can never escalate into
    # killing the whole voice loop thread, matching the same defensive
    # posture as the resolve_tool_call() block immediately below it.
    if (
        intent == "chat"
        and _HAS_PLANNING
        and try_plan_and_execute is not None
        and getattr(Config, "PLANNING_ENABLED", True)
    ):
        try:
            allowed_apps = frozenset(getattr(Config, "APP_LAUNCH_ALLOWLIST", []))
            plan_outcome = try_plan_and_execute(
                user_input,
                brain.model_name,
                _build_plan_dispatch_fn(ctx),
                Config,
                allowed_apps=allowed_apps,
            )
            if plan_outcome is not None:
                return _quick(ctx, plan_outcome.final_message)
        except Exception as e:  # noqa: BLE001 -- absolute safety net
            print(f"[Planning] Multi-step plan attempt failed unexpectedly: {e}")
            # Deliberately falls through to the single-tool path below
            # rather than returning an error to the user -- a planning
            # bug should degrade to "handled like a single-tool request"
            # wherever that's still possible, not surface as a failure.

    if (
        intent == "chat"
        and getattr(Config, "TOOL_CALLING_ENABLED", True)
        and resolve_tool_call
        and build_fake_match
        and TOOL_NAME_TO_INTENT
    ):
        try:
            resolved = resolve_tool_call(user_input, brain.model_name)
            tool_name = resolved.get("name")
            tool_args = resolved.get("arguments", {})
            mapped_intent = TOOL_NAME_TO_INTENT.get(tool_name)
            if mapped_intent:
                fake_match = build_fake_match(tool_name, tool_args)
                tool_handler = _INTENT_HANDLERS.get(mapped_intent)
                if tool_handler is not None:
                    tool_result = tool_handler(fake_match, ctx)
                    if tool_result is not None:
                        return tool_result
        except Exception as e:
            print(f"[ToolRouter] resolution failed: {e}")

    # ── Self-learning fallback log ───────────────────────────────────────
    # Reached only when the fast-path regex matcher, the multi-step
    # planner, AND the single-tool LLM resolver all failed to route this
    # to a real tool -- i.e. it's about to be answered as plain chat.
    # Logging it here (rather than at the top the moment detect_intent()
    # returns "chat") means a query the planner or tool-router DID
    # manage to resolve via the LLM never shows up as a "miss" -- only
    # genuinely unhandled input does, which is what's actually useful to
    # review later for new _INTENT_PATTERNS/_INTENT_GATES entries.
    if intent == "chat":
        log_unmatched(user_input)

    ui_update("status", "thinking")
    try:
        stream = brain.generate_response_stream(user_input)
        sentences = tts.speak_stream(
            stream, on_first_chunk=lambda: ui_update("status", "speaking")
        )
        return " ".join(sentences)
    except Exception as e:
        print(f"[Error] LLM stream failed: {e}")
        return _quick(
            ctx, "Sorry, I had trouble responding to that. Could you try again?"
        )