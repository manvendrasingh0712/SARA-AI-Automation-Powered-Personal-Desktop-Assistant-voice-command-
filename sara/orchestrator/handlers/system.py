"""
sara.orchestrator.handlers.system

Clipboard, screenshot description, calculator, volume/brightness,
opening/closing/restarting/switching applications, typing/keypress,
file find/open, service start/stop, window management, and the
"notify me when a file appears" watcher. Split out of the former
monolithic intent_handlers.py -- see that module's docstring, in
particular the SECURITY HARDENING note covering _h_open_app()/
_h_close_app().
"""
import time

from config import Config

from sara.tools.clipboard import read_clipboard, write_clipboard
from sara.tools import system as system_tools
from .. import notifications

from ..calc_utils import _safe_calc
from ..network_utils import _call_with_timeout
from ..command_helpers import (
    _quick,
    _quick_likely_misfire,
    _ack,
    _activity,
    _activity_label,
    _run_activity,
    _is_risky,
)
from ..context_tracking import _remember_entity, _resolve_app_target
from .._shared_state import (
    _HAS_PLANNING,
    validate_tool_arguments,
    PlanValidationError,
    _RISKY_APP_KEYWORDS,
    _RISKY_SERVICE_KEYWORDS,
    _CONFIRM_PENDING_TTL_S,
)

def _h_clipboard_read(match, ctx):
    return _quick(ctx, f"Your clipboard contains: {read_clipboard()}")


def _h_clipboard_write(match, ctx):
    if not match:
        return None
    return _quick(ctx, write_clipboard(match.group(1)))


def _h_screenshot_describe(match, ctx):
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    return _quick(
        ctx,
        _run_activity(
            ctx, "screen", "Checking your screen", "Screen checked", "Couldn't check the screen",
            lambda: ctx["vision"].describe_screen(),
        ),
    )



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
    # PRONOUN RESOLUTION (NEW): the open_app fast-path regex captures
    # whatever sits in the target position with no awareness that it
    # might be a pronoun/reference ("that", "usko", ...) rather than a
    # real app name -- this handler is reached directly, so it never
    # goes through _route_chat_message()'s pronoun handling. Resolve it
    # against the last remembered app BEFORE allowlist validation, so an
    # unresolved reference gets a clear "which app?" message instead of
    # being validated as a literal (and rejected as an unknown app).
    resolved_target = _resolve_app_target(ctx, target)
    if resolved_target is None:
        # LIKELY-MISFIRE SIGNAL (NEW): unresolved pronoun/reference, not
        # a genuine failure -- see _LikelyMisfireReply for why this uses
        # _quick_likely_misfire() instead of plain _quick().
        return _quick_likely_misfire(
            ctx, "Which app would you like me to open?"
        )
    target = resolved_target
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
            _activity(ctx, "error", "app", f"Couldn't open {_activity_label(target)}")
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] open_app validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely open that application.")
    # CONTEXT TRACKING (NEW): remember the (validated) app name so a later
    # turn -- e.g. a future pronoun-style "close it" handler -- has
    # something to resolve "it" against. Recorded after validation so an
    # app name the allowlist just rejected is never remembered as "last
    # opened".
    _remember_entity(ctx, "last_app", target)
    _ack(ctx)
    label = _activity_label(target)
    return _quick(
        ctx,
        _run_activity(
            ctx, "app", f"Opening {label}", f"{label} opened", f"Couldn't open {label}",
            lambda: _call_with_timeout(
                system_tools.open_application, target, tool_name="open_application"
            ),
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
    # PRONOUN RESOLUTION (NEW): same rationale as _h_open_app() above --
    # this fast-path handler never goes through _route_chat_message(), so
    # "close it"/"usko band kar do" would otherwise reach allowlist
    # validation with the literal pronoun as the "app name". Resolve
    # against last_app first.
    resolved_app_name = _resolve_app_target(ctx, app_name)
    if resolved_app_name is None:
        # LIKELY-MISFIRE SIGNAL (NEW): see _h_open_app() above.
        return _quick_likely_misfire(
            ctx, "Which app would you like me to close?"
        )
    app_name = resolved_app_name
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
            _activity(ctx, "error", "app", f"Couldn't close {_activity_label(app_name)}")
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] close_app validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely close that application.")
    # CONTEXT TRACKING (NEW): same rationale as _h_open_app() above --
    # remember the (validated) app name regardless of whether it then
    # turns out to be risky/pending-confirmation, since the user has
    # unambiguously named it either way.
    _remember_entity(ctx, "last_app", app_name)
    _remember_entity(ctx, "last_closed_app", app_name)
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
    label = _activity_label(app_name)
    return _quick(
        ctx,
        _run_activity(
            ctx, "app", f"Closing {label}", f"{label} closed", f"Couldn't close {label}",
            lambda: _call_with_timeout(
                system_tools.close_application, app_name, tool_name="close_application"
            ),
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
    file_query = match.group(1).strip()
    # CONTEXT TRACKING (NEW): same rationale as _h_open_app()'s last_app
    # tracking above -- remember the searched-for file so a later turn
    # has something to resolve a "that file" style reference against.
    _remember_entity(ctx, "last_file", file_query)
    return _quick(
        ctx,
        _run_activity(
            ctx, "file", "Searching files", "File found", "Couldn't find that file",
            lambda: _call_with_timeout(system_tools.find_file, file_query, tool_name="find_file"),
        ),
    )


def _h_open_file(match, ctx):
    """
    Finds and opens a file captured by the new open_file fast-path
    regex (or resolved by the LLM single-tool router -- see
    TOOL_NAME_TO_INTENT/TOOLS_SCHEMA in tool_router.py). Mirrors
    _h_find_file() above exactly (_ack(), "thinking" status,
    last_file context tracking, _run_activity() card) -- the only
    difference is which system_tools function it calls: this backs the
    ambiguity-safe find_and_open_file() rather than the search-only
    find_file(), so "open my resume" actually opens the file instead
    of only reading its path back.
    """
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    file_query = match.group(1).strip()
    # CONTEXT TRACKING (NEW): same rationale as _h_find_file()'s
    # last_file tracking above -- remember the file so a later turn has
    # something to resolve a "that file" style reference against.
    _remember_entity(ctx, "last_file", file_query)
    return _quick(
        ctx,
        _run_activity(
            ctx, "file", "Opening file", "Done", "Couldn't open that file",
            lambda: _call_with_timeout(
                system_tools.find_and_open_file, file_query, tool_name="find_and_open_file"
            ),
        ),
    )


def _h_start_service(match, ctx):
    if not match:
        return None
    _ack(ctx)
    service_name = match.group(1).strip()
    label = _activity_label(service_name)
    return _quick(
        ctx,
        _run_activity(
            ctx, "service", f"Starting {label}", f"{label} started", f"Couldn't start {label}",
            lambda: _call_with_timeout(
                system_tools.start_service, service_name, tool_name="start_service"
            ),
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
    label = _activity_label(service_name)
    return _quick(
        ctx,
        _run_activity(
            ctx, "service", f"Stopping {label}", f"{label} stopped", f"Couldn't stop {label}",
            lambda: _call_with_timeout(
                system_tools.stop_service, service_name, tool_name="stop_service"
            ),
        ),
    )

def _h_restart_application(match, ctx):
    if not match:
        return None
    app_name = match.group(1).strip()
    # PRONOUN RESOLUTION (NEW): same rationale as _h_open_app()/
    # _h_close_app() above -- this fast-path handler never goes through
    # _route_chat_message(), so "restart it"/"usko restart karo" would
    # otherwise be passed straight to system_tools.restart_application()
    # as the literal pronoun. Resolve against last_app first, and bail
    # out with a clarifying question (rather than a confusing failure
    # from the underlying tool) if there's nothing fresh to resolve to.
    resolved_app_name = _resolve_app_target(ctx, app_name)
    if resolved_app_name is None:
        # LIKELY-MISFIRE SIGNAL (NEW): see _h_open_app() above.
        return _quick_likely_misfire(
            ctx, "Which app would you like me to restart?"
        )
    app_name = resolved_app_name
    # CONTEXT TRACKING (BUGFIX): this handler never updated last_app
    # before, so a "restart chrome" followed by "close it" had nothing
    # to resolve "it" against. Recorded here, same as _h_open_app()/
    # _h_close_app(), so the slot stays accurate for the next follow-up.
    _remember_entity(ctx, "last_app", app_name)
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    label = _activity_label(app_name)
    return _quick(
        ctx,
        _run_activity(
            ctx, "app", f"Restarting {label}", f"{label} restarted", f"Couldn't restart {label}",
            lambda: _call_with_timeout(
                system_tools.restart_application, app_name, tool_name="restart_application"
            ),
        ),
    )


def _h_switch_to_application(match, ctx):
    if not match:
        return None
    app_name = match.group(1).strip()
    # PRONOUN RESOLUTION (NEW): same rationale as the other three app
    # handlers above -- "switch to it"/"usme switch karo" would
    # otherwise be passed straight through as the literal pronoun.
    resolved_app_name = _resolve_app_target(ctx, app_name)
    if resolved_app_name is None:
        # LIKELY-MISFIRE SIGNAL (NEW): see _h_open_app() above.
        return _quick_likely_misfire(
            ctx, "Which app would you like me to switch to?"
        )
    app_name = resolved_app_name
    # CONTEXT TRACKING (BUGFIX): same rationale as
    # _h_restart_application() above -- this handler never updated
    # last_app before, leaving nothing for a later "close it" to resolve
    # against.
    _remember_entity(ctx, "last_app", app_name)
    _ack(ctx)
    return _quick(
        ctx,
        _call_with_timeout(
            system_tools.switch_to_application, app_name, tool_name="switch_to_application"
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
    

def _h_notify_on_file(match, ctx):
    """
    "tell me when the download finishes" / "jab download complete ho
    jaye batana" -- arms (or re-arms) a single watch on the user's
    Downloads folder via the background NotificationWatcher
    (sara/orchestrator/notifications.py). Only one watch is active at a
    time: calling this again while a watch is already running silently
    REPLACES the previous target -- it never crashes and never silently
    no-ops -- see NotificationWatcher.watch_for_next_file()'s docstring.

    notifications.get_watcher() is handed ctx["tts"]/ctx["ui_update"] so
    it can lazily create the process-wide singleton if
    sara/gui/app/bootstrap.py's init_watcher() call somehow hasn't run
    yet (defensive only -- in normal operation the singleton already
    exists by the time any voice command reaches this handler).
    """
    watcher = notifications.get_watcher(ctx["tts"], ctx["ui_update"])
    if watcher is None:
        return _quick(ctx, "Sorry, file notifications aren't available right now.")
    if not getattr(Config, "NOTIFICATIONS_ENABLED", True):
        return _quick(
            ctx, "File notifications are turned off right now, so I can't watch for that."
        )
    result = watcher.watch_for_next_file()
    return _quick(ctx, result)

