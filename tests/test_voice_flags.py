"""Tests for sara.audio.voice_flags (registry + live Config effect)."""
import dataclasses

import pytest

from config import Config
from sara.audio import voice_flags
from sara.audio.voice_flags import (
    VOICE_FLAGS,
    VoiceFlag,
    flags_state,
    get_flag,
    reset_flags,
    set_flag,
)

_EXPECTED_DEFAULTS = {
    "VOICE_ACK_CACHE": True,
    "VOICE_ACK_CACHE_DISK": False,
    "VOICE_CHUNKER_V2": False,
}


@pytest.fixture(autouse=True)
def _restore_config():
    saved = {f.key: getattr(Config, f.key, None) for f in VOICE_FLAGS}
    yield
    for key, value in saved.items():
        setattr(Config, key, value)


def test_registry_contents():
    assert isinstance(VOICE_FLAGS, tuple)
    assert {f.key for f in VOICE_FLAGS} >= set(_EXPECTED_DEFAULTS)
    assert len({f.key for f in VOICE_FLAGS}) == len(VOICE_FLAGS)
    for flag in VOICE_FLAGS:
        assert isinstance(flag, VoiceFlag)
        assert flag.label.strip() and flag.description.strip() and flag.task.strip()
        assert isinstance(flag.default, bool)


def test_registry_defaults():
    by_key = {f.key: f for f in VOICE_FLAGS}
    for key, default in _EXPECTED_DEFAULTS.items():
        assert by_key[key].default is default
        assert by_key[key].task == "T13"


def test_voice_flag_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        VOICE_FLAGS[0].key = "OTHER"  # type: ignore[misc]


def test_get_flag_reads_config_live(monkeypatch):
    monkeypatch.setattr(Config, "VOICE_CHUNKER_V2", True, raising=False)
    assert get_flag("VOICE_CHUNKER_V2") is True
    monkeypatch.setattr(Config, "VOICE_CHUNKER_V2", False, raising=False)
    assert get_flag("VOICE_CHUNKER_V2") is False


def test_unknown_key_is_false_and_never_raises():
    assert get_flag("NOT_A_FLAG") is False
    assert get_flag("") is False
    assert get_flag(None) is False  # type: ignore[arg-type]


def test_set_flag_applies_to_config():
    assert set_flag("VOICE_CHUNKER_V2", True) is True
    assert Config.VOICE_CHUNKER_V2 is True
    assert get_flag("VOICE_CHUNKER_V2") is True
    assert set_flag("VOICE_ACK_CACHE", False) is True
    assert Config.VOICE_ACK_CACHE is False


def test_set_flag_coerces_to_bool():
    assert set_flag("VOICE_ACK_CACHE_DISK", 1) is True  # type: ignore[arg-type]
    assert Config.VOICE_ACK_CACHE_DISK is True


def test_set_flag_unknown_key_changes_nothing():
    before = flags_state()
    assert set_flag("NOT_A_FLAG", True) is False
    assert not hasattr(Config, "NOT_A_FLAG")
    assert flags_state() == before


def test_reset_flags_restores_defaults():
    set_flag("VOICE_ACK_CACHE", False)
    set_flag("VOICE_ACK_CACHE_DISK", True)
    set_flag("VOICE_CHUNKER_V2", True)
    reset_flags()
    assert flags_state() == {f.key: f.default for f in VOICE_FLAGS}


def test_flags_state_shape():
    state = flags_state()
    assert set(state) == {f.key for f in VOICE_FLAGS}
    assert all(isinstance(v, bool) for v in state.values())


def test_state_follows_live_changes():
    reset_flags()
    set_flag("VOICE_CHUNKER_V2", True)
    assert flags_state()["VOICE_CHUNKER_V2"] is True
    assert voice_flags.get_flag("VOICE_CHUNKER_V2") is True