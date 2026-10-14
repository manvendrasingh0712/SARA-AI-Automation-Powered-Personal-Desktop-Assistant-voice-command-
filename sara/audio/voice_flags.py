"""
sara.audio.voice_flags
Registry of the voice-pipeline feature flags (live toggles, no restart).
Every key is also a boolean attribute on Config.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from config import Config

_LOCK = threading.Lock()


@dataclass(frozen=True)
class VoiceFlag:
    """One user-visible voice feature flag."""

    key: str
    default: bool
    label: str
    description: str
    task: str


VOICE_FLAGS: tuple[VoiceFlag, ...] = (
    VoiceFlag(
        key="VOICE_ACK_CACHE",
        default=True,
        label="Instant acknowledgements",
        description=(
            "Prepares short replies such as 'On it!' in the background after "
            "start-up, so SARA answers without a synthesis delay."
        ),
        task="T13",
    ),
    VoiceFlag(
        key="VOICE_ACK_CACHE_DISK",
        default=False,
        label="Remember acknowledgements between runs",
        description=(
            "Saves the prepared short replies on disk so the next start-up "
            "is ready sooner."
        ),
        task="T13",
    ),
    VoiceFlag(
        key="VOICE_CHUNKER_V2",
        default=False,
        label="Faster first words",
        description=(
            "Starts speaking a long reply at the first safe pause, without "
            "cutting numbers, abbreviations, links or Hindi words."
        ),
        task="T13",
    ),
    VoiceFlag(
        key="VOICE_ENDPOINT_ADAPTIVE",
        default=False,
        label="Smarter end-of-speech detection",
        description=(
            "Answers sooner after a finished command and waits longer when "
            "you pause mid-sentence, in English, Hindi and Hinglish."
        ),
        task="T14",
    ),
)

_BY_KEY = {flag.key: flag for flag in VOICE_FLAGS}


def get_flag(key: str) -> bool:
    """Live value of a flag from Config; unknown key or any error -> False."""
    try:
        if key not in _BY_KEY:
            return False
        return bool(getattr(Config, key, _BY_KEY[key].default))
    except Exception:
        return False


def set_flag(key: str, value: bool) -> bool:
    """Set a flag on the running app. True when applied, False for an unknown key."""
    try:
        if key not in _BY_KEY:
            return False
        with _LOCK:
            setattr(Config, key, bool(value))
        return True
    except Exception:
        return False


def reset_flags() -> None:
    """Restore every registered flag to its default."""
    for flag in VOICE_FLAGS:
        set_flag(flag.key, flag.default)


def flags_state() -> dict[str, bool]:
    """Current value of every registered flag."""
    return {flag.key: get_flag(flag.key) for flag in VOICE_FLAGS}