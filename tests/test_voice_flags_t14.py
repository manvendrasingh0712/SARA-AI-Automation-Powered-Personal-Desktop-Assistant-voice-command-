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
    # D41: flipped False -> True. The subsystem was complete and tested but
    # inert (audit finding D32), and until D39 it could not fire at all --
    # a permissive VAD held `is_voice` True so the endpoint decision was
    # never reached on a silence chunk. Must stay in step with
    # Config.VOICE_ENDPOINT_ADAPTIVE: get_flag() reads Config, reset_flags()
    # writes this registry value, so a mismatch makes "reset to defaults"
    # change behaviour. See the long note on the Config field.
    assert row.default is True
    assert row.task == "T14"
    assert row.label == "Smarter end-of-speech detection"
    assert row.description


def test_keys_unique_and_on_config():
    keys = [f.key for f in vf.VOICE_FLAGS]
    assert len(keys) == len(set(keys))
    assert isinstance(getattr(Config, "VOICE_ENDPOINT_ADAPTIVE"), bool)


def test_set_get_reset_roundtrip():
    # D41: sets the flag AWAY from its new default (True) so the reset
    # assertion still proves something. Setting it to the default would
    # make the final check pass whether reset_flags() worked or not.
    assert vf.set_flag("VOICE_ENDPOINT_ADAPTIVE", False) is True
    assert vf.get_flag("VOICE_ENDPOINT_ADAPTIVE") is False
    assert vf.flags_state()["VOICE_ENDPOINT_ADAPTIVE"] is False
    vf.reset_flags()
    assert vf.get_flag("VOICE_ENDPOINT_ADAPTIVE") is True


def test_unknown_key_rejected():
    assert vf.set_flag("ENDPOINT_MIN_MS", True) is False
    assert vf.get_flag("NOPE") is False