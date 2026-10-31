import types

import pytest

from sara.core.intent.engine import detect_intent
from sara.tools.system import apps, connectivity, media_keys


@pytest.mark.parametrize("text, verb", [
    ("pause", "pause_media"), ("pause music", "pause_media"), ("pause the song", "pause_media"),
    ("gaana pause karo", "pause_media"), ("gaana roko", "pause_media"),
    ("play music", "resume_media"), ("play", "resume_media"), ("resume", "resume_media"),
    ("resume music", "resume_media"), ("unpause", "resume_media"), ("play the song", "resume_media"),
    ("play some music", "resume_media"), ("music play karo", "resume_media"),
    ("play/pause", "play_pause_media"),
])
def test_pause_and_play_words_pick_the_right_action(text, verb):
    intent, _ = detect_intent(text)
    assert intent == "play_pause_media"
    assert media_keys.media_action_for(text).__name__ == verb


@pytest.mark.parametrize("text, query", [
    ("play despacito", "despacito"), ("play arijit singh songs", "arijit singh songs"),
    ("play the song kesariya", "kesariya"), ("play kesariya on youtube", "kesariya"),
])
def test_real_song_requests_still_search(text, query):
    intent, m = detect_intent(text)
    assert intent == "play_youtube" and m.group(1) == query


def test_pause_does_not_start_paused_music(monkeypatch):
    sent = []
    monkeypatch.setattr(media_keys, "_session_control", lambda action: "It's already paused." if action == "pause" else None)
    monkeypatch.setattr(media_keys, "play_pause_media", lambda: sent.append("key") or "toggled")
    assert media_keys.pause_media() == "It's already paused."
    assert sent == []


def test_pause_falls_back_to_the_key_without_a_session(monkeypatch):
    monkeypatch.setattr(media_keys, "_session_control", lambda action: None)
    monkeypatch.setattr(media_keys, "play_pause_media", lambda: "toggled")
    assert media_keys.pause_media() == "toggled"
    assert media_keys.resume_media() == "toggled"


def _fake_proc(name):
    return types.SimpleNamespace(name=lambda: name, is_running=lambda: True)


def test_close_terminal_tries_the_real_terminal_processes(monkeypatch):
    asked = []

    def fake_terminate(exe):
        asked.append(exe)
        procs = [_fake_proc("WindowsTerminal.exe")] if exe == "WindowsTerminal.exe" else []
        return apps._TerminateResult(procs, None, 0)

    monkeypatch.setattr(apps, "_terminate_matching", fake_terminate)
    monkeypatch.setattr(apps, "_wait_and_kill", lambda procs, grace: None)
    monkeypatch.setattr(apps, "_ensure_windows", lambda: None)
    msg = apps.close_application("terminal")
    assert "WindowsTerminal.exe" in asked and "cmd.exe" in asked
    assert msg.startswith("Closed 1 instance") and "WindowsTerminal.exe" in msg


def test_close_command_prompt_only_touches_cmd(monkeypatch):
    asked = []
    monkeypatch.setattr(apps, "_terminate_matching", lambda exe: asked.append(exe) or apps._TerminateResult([], None, 0))
    monkeypatch.setattr(apps, "_ensure_windows", lambda: None)
    apps.close_application("command prompt")
    assert asked == ["cmd.exe"]


def test_wifi_on_uses_the_radio_api_without_netsh(monkeypatch):
    monkeypatch.setattr(connectivity, "_ensure_windows", lambda: None)
    monkeypatch.setattr(connectivity, "set_radio", lambda kind, on: {"found": True, "error": "", "now_on": on})

    def boom(*a, **k):
        raise AssertionError("netsh must not run when the radio API worked")

    monkeypatch.setattr(connectivity.subprocess, "run", boom)
    assert connectivity.wifi_on() == "WiFi has been turned on."
    assert connectivity.wifi_off() == "WiFi has been turned off."


def test_wifi_falls_back_to_netsh_when_the_radio_api_fails(monkeypatch):
    monkeypatch.setattr(connectivity, "_ensure_windows", lambda: None)
    monkeypatch.setattr(connectivity, "set_radio", lambda kind, on: {"found": False, "error": "no radio"})
    monkeypatch.setattr(connectivity.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    assert connectivity.wifi_on() == "WiFi has been turned on."


def test_toggle_radio_flips_the_current_state(monkeypatch):
    calls = []

    def fake(kind, want):
        calls.append(want)
        return {"found": True, "was_on": True, "now_on": True if want is None else want, "error": ""}

    monkeypatch.setattr(connectivity, "_radio_call", fake)
    res = connectivity.toggle_radio("wifi")
    assert calls == [None, False] and res["now_on"] is False
