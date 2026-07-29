"""
sara.tools.system.services
Start, stop, and list Windows services by name.

psutil.win_service_iter() is used for read-only listing/lookup (no
elevated rights needed for that). Starting/stopping a service is NOT
something psutil can do — it only queries state — so that goes through
the Windows `sc` command-line utility instead. Most services require
Sara to be running with administrator privileges to start/stop; that
failure mode is detected and reported clearly rather than surfacing a
raw OS error code.
"""
from ._shared import _ensure_windows

import logging
import subprocess
import platform

import psutil

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

_SC_TIMEOUT_SECONDS = 15


def _find_service(name: str):
    """
    Case-insensitive lookup by short service name OR display name (what a
    person would actually say, e.g. "print spooler" rather than "Spooler").
    Falls back to a substring match on display name. Returns the service's
    info dict (as from psutil.win_service_iter()...as_dict()) or None.
    """
    name_lower = name.strip().lower()
    try:
        all_services = [svc.as_dict() for svc in psutil.win_service_iter()]
    except Exception as e:
        logger.error(f"_find_service failed to enumerate services: {e}")
        return None

    for info in all_services:
        if info["name"].lower() == name_lower or info["display_name"].lower() == name_lower:
            return info

    for info in all_services:
        if name_lower in info["display_name"].lower():
            return info

    return None


def list_services(running_only: bool = True) -> str:
    _ensure_windows()
    try:
        names = []
        for svc in psutil.win_service_iter():
            try:
                info = svc.as_dict()
            except Exception:
                continue
            if running_only and info["status"] != "running":
                continue
            names.append(info["display_name"])

        if not names:
            return "No matching services were found."

        names.sort()
        shown = names[:10]
        more = f", and {len(names) - 10} more" if len(names) > 10 else ""
        return f"{len(names)} services running: {', '.join(shown)}{more}."
    except Exception as e:
        logger.error(f"list_services failed: {e}")
        return "Sorry, I couldn't list services right now."


def _run_sc(action: str, service_name: str) -> str:
    _ensure_windows()

    if not service_name or not service_name.strip():
        return "No service name was provided."

    match = _find_service(service_name)
    target = match["name"] if match else service_name.strip()
    label = match["display_name"] if match else service_name.strip()
    verb = "Started" if action == "start" else "Stopped"
    verb_ing = "Starting" if action == "start" else "Stopping"

    try:
        result = subprocess.run(
            ["sc", action, target],
            capture_output=True,
            text=True,
            timeout=_SC_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            return f"{verb} the {label} service."

        stderr = (result.stderr or result.stdout or "").strip()
        if result.returncode == 5 or "access is denied" in stderr.lower():
            return (
                f"I don't have permission to {action} {label}. "
                f"Try running Sara as administrator."
            )
        return f"Couldn't {action} {label}: {stderr or 'unknown error'}."
    except subprocess.TimeoutExpired:
        return f"{verb_ing} {label} timed out."
    except Exception as e:
        logger.error(f"_run_sc({action}) failed for '{service_name}': {e}")
        return f"Sorry, I couldn't {action} '{service_name}' right now."


def start_service(service_name: str) -> str:
    return _run_sc("start", service_name)


def stop_service(service_name: str) -> str:
    return _run_sc("stop", service_name)