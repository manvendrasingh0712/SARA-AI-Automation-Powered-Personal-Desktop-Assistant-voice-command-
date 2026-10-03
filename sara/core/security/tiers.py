"""
sara.core.security.tiers
Risk tier of every registered intent (reviewed table).

  T0  read-only, no side effect
  T1  low risk, easily reversible
  T2  impactful, hard to notice or hard to undo
  T3  destructive, irreversible or system-wide

Any intent that is not listed here is treated as UNKNOWN_TIER (fail-safe).
Keep this file in sync with the registered intents (sara/core/intent/patterns.py plus the
skill intents registered from sara/skills); tests/test_security_tiers.py
fails when an intent is missing or an extra name is present.
"""
from __future__ import annotations

from typing import Dict

T0 = 0
T1 = 1
T2 = 2
T3 = 3

UNKNOWN_TIER = T2

TIERS: Dict[str, int] = {
    # T0 - read-only, no side effect
    "check_streak": T0,
    "self_diagnostics": T0,
    "recent_actions": T0,
    "notes_quiz_answers": T0,
    "notes_quiz": T0,
    "notes_summary": T0,
    "notes_qa": T0,
    "joke_feedback": T0,
    "joke_again": T0,
    "tell_joke": T0,
    "briefing_more": T0,
    "daily_briefing": T0,
    "reminder_list": T0,
    "read_notes": T0,
    "list_todos": T0,
    "clipboard_read": T0,
    "weather": T0,
    "news": T0,
    "followup_query": T0,
    "web_search": T0,
    "summarize_url": T0,
    "calculator": T0,
    "system_info": T0,
    "get_brightness_status": T0,
    "find_file": T0,
    "disk_usage": T0,
    "uptime": T0,
    "local_ip": T0,
    "gpu_status": T0,
    "temperature_status": T0,
    "process_list": T0,
    "list_services": T0,
    "time_query": T0,
    "date_query": T0,
    "calendar_today": T0,
    "why_proactive": T0,
    "why_decision": T0,
    "memory_recall": T0,

    # T1 - low risk, easily reversible
    "notes_refresh": T1,
    "switch_mode": T1,
    "undo_setting_change": T1,
    "reminder_add": T1,
    "set_timer": T1,
    "set_alarm": T1,
    "start_stopwatch": T1,
    "lap_stopwatch": T1,
    "stop_stopwatch": T1,
    "take_note": T1,
    "add_todo": T1,
    "complete_todo": T1,
    "clipboard_write": T1,
    "screenshot_describe": T1,
    "play_youtube": T1,
    "play_spotify": T1,
    "open_url": T1,
    "set_volume": T1,
    "mute": T1,
    "unmute": T1,
    "cancel_shutdown": T1,
    "max_volume": T1,
    "min_volume": T1,
    "set_brightness": T1,
    "increase_brightness": T1,
    "decrease_brightness": T1,
    "max_brightness": T1,
    "min_brightness": T1,
    "show_desktop": T1,
    "minimize_all_windows": T1,
    "restore_windows": T1,
    "maximize_active_window": T1,
    "minimize_active_window": T1,
    "snap_window_left": T1,
    "snap_window_right": T1,
    "switch_window": T1,
    "switch_to_application": T1,
    "always_on_top": T1,
    "toggle_fullscreen": T1,
    "play_pause_media": T1,
    "next_track": T1,
    "previous_track": T1,
    "stop_media": T1,
    "copy_selection": T1,
    "select_all": T1,
    "new_tab": T1,
    "next_tab": T1,
    "prev_tab": T1,
    "reload_page": T1,
    "zoom_in": T1,
    "zoom_out": T1,
    "zoom_reset": T1,
    "scroll_up": T1,
    "scroll_down": T1,
    "scroll_top": T1,
    "scroll_bottom": T1,
    "wifi_on": T1,
    "bluetooth_on": T1,
    "bluetooth_off": T1,
    "dark_mode": T1,
    "light_mode": T1,
    "notify_on_file": T1,
    "open_downloads": T1,
    "open_documents": T1,
    "open_desktop_folder": T1,
    "open_pictures": T1,
    "open_music": T1,
    "open_videos": T1,
    "open_this_pc": T1,
    "open_recycle_bin": T1,
    "open_file_explorer": T1,
    "open_control_panel": T1,
    "open_task_manager": T1,
    "open_display_settings": T1,
    "open_sound_settings": T1,
    "open_bluetooth_settings": T1,
    "open_network_settings": T1,
    "open_update_settings": T1,
    "open_apps_settings": T1,
    "open_personalization_settings": T1,
    "open_privacy_settings": T1,
    "open_storage_settings": T1,
    "open_power_settings": T1,
    "open_about_settings": T1,
    "open_nightlight_settings": T1,
    "open_airplane_mode_settings": T1,
    "open_app": T1,

    # T2 - impactful, hard to notice or hard to undo
    "self_diagnostics_fix": T2,
    "reminder_cancel": T2,
    "delete_todo": T2,
    "lock_pc": T2,
    "close_active_window": T2,
    "restart_application": T2,
    "move_window": T2,
    "resize_window": T2,
    "typing_text": T2,
    "press_key": T2,
    "paste_clipboard": T2,
    "undo": T2,
    "redo": T2,
    "close_tab": T2,
    "wifi_off": T2,
    "start_service": T2,
    "stop_service": T2,
    "calendar_create": T2,
    "memory_forget_specific": T2,
    "run_routine": T2,
    "close_app": T2,

    # T3 - destructive, irreversible or system-wide
    "clear_notes": T3,
    "sleep_system": T3,
    "hibernate_system": T3,
    "log_off": T3,
    "shutdown_system": T3,
    "restart_system": T3,
    "empty_recycle_bin": T3,
    "memory_forget_all": T3,
}

# Pending-action names used by confirm_state that are not intent names.
ACTION_ALIASES: Dict[str, str] = {
    "forget_all_memories": "memory_forget_all",
}


def tier_of(intent: str) -> int:
    """Tier of an intent or confirm_state action name; UNKNOWN_TIER when not listed."""
    name = ACTION_ALIASES.get(intent, intent)
    return TIERS.get(name, UNKNOWN_TIER)

