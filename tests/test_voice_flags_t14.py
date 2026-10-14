"""Registry row for the T14 adaptive-endpointing flag."""
import pytest

from config import Config
from sara.audio import voice_flags as vf


@pytest.fixture(autouse=True)
def _restore():
    saved = vf.flags_state()
    yield
    for key, value in saved.items():
        vf.set_flag(key, value)


def _row():
    return next(f for f in vf.VOICE_FLAGS if f.key == "VOICE_ENDPOINT_ADAPTIVE")


def test_row_metadata():
    row = _row()
    assert row.default is False
    assert row.task == "T14"
    assert row.label == "Smarter end-of-speech detection"
    assert row.description


def test_keys_unique_and_on_config():
    keys = [f.key for f in vf.VOICE_FLAGS]
    assert len(keys) == len(set(keys))
    assert isinstance(getattr(Config, "VOICE_ENDPOINT_ADAPTIVE"), bool)


def test_set_get_reset_roundtrip():
    assert vf.set_flag("VOICE_ENDPOINT_ADAPTIVE", True) is True
    assert vf.get_flag("VOICE_ENDPOINT_ADAPTIVE") is True
    assert vf.flags_state()["VOICE_ENDPOINT_ADAPTIVE"] is True
    vf.reset_flags()
    assert vf.get_flag("VOICE_ENDPOINT_ADAPTIVE") is False


def test_unknown_key_rejected():
    assert vf.set_flag("ENDPOINT_MIN_MS", True) is False
    assert vf.get_flag("NOPE") is False