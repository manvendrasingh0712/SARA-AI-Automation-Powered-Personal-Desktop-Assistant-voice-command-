"""Golden-set loader and schema validation for SARA-Bench."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

log = logging.getLogger("bench.dataset")

GOLDEN_DIR: Path = Path(__file__).resolve().parent / "golden"

DEFAULT_FILES: Tuple[str, ...] = (
    "intents_en.jsonl",
    "intents_en_noisy.jsonl",
    "intents_hinglish.jsonl",
    "intents_hi.jsonl",
    "negatives.jsonl",
    "adversarial.jsonl",
)

LANGS: Tuple[str, ...] = ("en", "hi", "hinglish")
KINDS: Tuple[str, ...] = ("positive", "negative", "adversarial")
STYLES: Tuple[str, ...] = ("formal", "casual", "noisy", "short")

_DEFAULT_KEYS = frozenset({"lang", "kind", "style"})
_ROW_KEYS = frozenset({"t", "i", "s", "l", "g", "n"})

_CATEGORY_GROUPS: Dict[str, Tuple[str, ...]] = {
    "apps": ("open_app", "close_app", "restart_application"),
    "windows": (
        "show_desktop", "minimize_all_windows", "restore_windows",
        "maximize_active_window", "minimize_active_window",
        "close_active_window", "snap_window_left", "snap_window_right",
        "switch_window", "switch_to_application", "move_window",
        "resize_window", "always_on_top", "toggle_fullscreen",
    ),
    "media": (
        "play_youtube", "play_spotify", "play_pause_media", "next_track",
        "previous_track", "stop_media",
    ),
    "volume_brightness": (
        "set_volume", "mute", "unmute", "max_volume", "min_volume",
        "set_brightness", "increase_brightness", "decrease_brightness",
        "max_brightness", "min_brightness", "get_brightness_status",
    ),
    "power": (
        "lock_pc", "sleep_system", "hibernate_system", "log_off",
        "shutdown_system", "restart_system", "cancel_shutdown",
    ),
    "notes_todos": (
        "take_note", "read_notes", "clear_notes", "add_todo", "list_todos",
        "complete_todo", "delete_todo",
    ),
    "reminders_time": (
        "reminder_add", "reminder_list", "reminder_cancel", "set_timer",
        "set_alarm", "start_stopwatch", "lap_stopwatch", "stop_stopwatch",
        "time_query", "date_query", "calendar_today", "calendar_create",
    ),
    "web_info": (
        "weather", "news", "web_search", "summarize_url", "open_url",
    ),
    "files_folders": (
        "find_file", "empty_recycle_bin", "notify_on_file", "open_downloads",
        "open_documents", "open_desktop_folder", "open_pictures",
        "open_music", "open_videos", "open_this_pc", "open_recycle_bin",
        "open_file_explorer",
    ),
    "settings_pages": (
        "wifi_on", "wifi_off", "bluetooth_on", "bluetooth_off", "dark_mode",
        "light_mode", "open_control_panel", "open_task_manager",
        "open_display_settings", "open_sound_settings",
        "open_bluetooth_settings", "open_network_settings",
        "open_update_settings", "open_apps_settings",
        "open_personalization_settings", "open_privacy_settings",
        "open_storage_settings", "open_power_settings",
        "open_about_settings", "open_nightlight_settings",
        "open_airplane_mode_settings",
    ),
    "system_info": (
        "system_info", "disk_usage", "uptime", "local_ip", "gpu_status",
        "temperature_status", "process_list", "list_services",
        "start_service", "stop_service",
    ),
    "memory": ("memory_forget_all", "memory_forget_specific", "memory_recall"),
    "keyboard_mouse": (
        "clipboard_read", "clipboard_write", "typing_text", "press_key",
        "copy_selection", "paste_clipboard", "select_all", "undo", "redo",
        "new_tab", "close_tab", "next_tab", "prev_tab", "reload_page",
        "zoom_in", "zoom_out", "zoom_reset", "scroll_up", "scroll_down",
        "scroll_top", "scroll_bottom",
    ),
    "misc": (
        "switch_mode", "undo_setting_change", "screenshot_describe",
        "followup_query", "calculator", "why_proactive", "why_decision",
        "run_routine",
    ),
}

CATEGORY_BY_INTENT: Dict[str, str] = {
    name: category
    for category, names in _CATEGORY_GROUPS.items()
    for name in names
}
CATEGORY_BY_INTENT["chat"] = "chat"


@dataclass(frozen=True)
class Row:
    """One labelled utterance of the golden set."""

    id: str
    text: str
    lang: str
    expected_intent: str
    expected_groups: Tuple[str, ...]
    category: str
    kind: str
    style: str
    note: str
    source: str


def _fail(name: str, lineno: int, reason: str) -> ValueError:
    return ValueError(f"{name}:{lineno}: {reason}")


def _json_object(raw: str, name: str, lineno: int) -> Dict[str, Any]:
    if not raw.strip():
        raise _fail(name, lineno, "blank line")
    try:
        obj = json.loads(raw)
    except ValueError:
        raise _fail(name, lineno, "invalid JSON") from None
    if not isinstance(obj, dict):
        raise _fail(name, lineno, "line must be a JSON object")
    return obj


def _choice(value: Any, allowed: Sequence[str], label: str,
            name: str, lineno: int) -> str:
    if value not in allowed:
        raise _fail(name, lineno, f"{label} must be one of {list(allowed)}")
    return str(value)


def _parse_defaults(raw: str, name: str) -> Dict[str, str]:
    obj = _json_object(raw, name, 1)
    inner = obj.get("_defaults")
    if set(obj) != {"_defaults"} or not isinstance(inner, dict):
        raise _fail(name, 1, 'first line must be {"_defaults":{...}}')
    if set(inner) != _DEFAULT_KEYS:
        raise _fail(name, 1, "_defaults needs exactly lang, kind, style")
    return {
        "lang": _choice(inner["lang"], LANGS, "lang", name, 1),
        "kind": _choice(inner["kind"], KINDS, "kind", name, 1),
        "style": _choice(inner["style"], STYLES, "style", name, 1),
    }


def _parse_groups(value: Any, name: str, lineno: int) -> Tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(name, lineno, "g must be a list of strings")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item or item != item.lower().strip():
            raise _fail(name, lineno, "g items must be non-empty lowercase stripped strings")
        out.append(item)
    return tuple(out)


def _parse_row(raw: str, lineno: int, name: str, stem: str,
               defaults: Dict[str, str]) -> Row:
    obj = _json_object(raw, name, lineno)
    unknown = set(obj) - _ROW_KEYS
    if unknown:
        raise _fail(name, lineno, f"unknown key(s) {sorted(unknown)}")
    text = obj.get("t")
    if not isinstance(text, str) or not text.strip():
        raise _fail(name, lineno, "t must be a non-empty string")
    intent = obj.get("i")
    if not isinstance(intent, str) or intent not in CATEGORY_BY_INTENT:
        raise _fail(name, lineno, f"unknown intent {intent!r}")
    style = _choice(obj.get("s", defaults["style"]), STYLES, "s", name, lineno)
    lang = _choice(obj.get("l", defaults["lang"]), LANGS, "l", name, lineno)
    groups = _parse_groups(obj.get("g", []), name, lineno)
    note = obj.get("n", "")
    if not isinstance(note, str):
        raise _fail(name, lineno, "n must be a string")
    return Row(
        id=f"{stem}-{lineno:04d}",
        text=text,
        lang=lang,
        expected_intent=intent,
        expected_groups=groups,
        category=CATEGORY_BY_INTENT[intent],
        kind=defaults["kind"],
        style=style,
        note=note,
        source=name,
    )


def _load_file(path: Path) -> List[Row]:
    name = path.name
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail(name, 1, f"unreadable ({type(exc).__name__})") from None
    if not lines:
        raise _fail(name, 1, "file is empty")
    defaults = _parse_defaults(lines[0], name)
    return [
        _parse_row(raw, lineno, name, path.stem, defaults)
        for lineno, raw in enumerate(lines[1:], start=2)
    ]


def _resolve(item: Union[str, Path]) -> Optional[Path]:
    path = Path(item)
    candidates = [path] if path.is_absolute() else [GOLDEN_DIR / path, path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_rows(files: Optional[Sequence[Union[str, Path]]] = None,
              missing: Optional[List[str]] = None) -> List[Row]:
    """Load golden rows; missing files are skipped, logged and appended to `missing`.

    Raises ValueError("<file>:<line>: <reason>") on any schema violation.
    """
    rows: List[Row] = []
    for item in (DEFAULT_FILES if files is None else files):
        path = _resolve(item)
        if path is None:
            log.warning("golden file missing, skipped: %s", item)
            if missing is not None:
                missing.append(Path(item).name)
            continue
        rows.extend(_load_file(path))
    return rows