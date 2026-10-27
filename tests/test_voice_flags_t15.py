import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import Config
from sara.audio import voice_flags as vf

KEYS = ("VOICE_DUCKING", "VOICE_SEMANTIC_BARGE_IN")


@pytest.fixture(autouse=True)
def _restore():
    saved = {k: getattr(Config, k, None) for k in KEYS}
    yield
    for k, v in saved.items():
        setattr(Config, k, v)


def test_rows_registered_with_labels_and_task():
    rows = {f.key: f for f in vf.VOICE_FLAGS}
    for key in KEYS:
        assert key in rows
        assert rows[key].default is False and rows[key].task == "T15"
    assert rows["VOICE_DUCKING"].label == "Lower Sara's voice when I start talking"
    assert rows["VOICE_SEMANTIC_BARGE_IN"].label.startswith("Understand interruptions")


def test_set_get_reset_and_state():
    assert vf.set_flag("VOICE_DUCKING", True) is True
    assert vf.get_flag("VOICE_DUCKING") is True
    assert vf.flags_state()["VOICE_DUCKING"] is True
    vf.reset_flags()
    assert vf.get_flag("VOICE_DUCKING") is False
    assert vf.get_flag("VOICE_SEMANTIC_BARGE_IN") is False


def test_unknown_key_rejected():
    assert vf.set_flag("NOPE", True) is False
    assert vf.get_flag("NOPE") is False


def test_config_defaults():
    assert Config.VOICE_DUCK_DB == -12.0
    assert Config.VOICE_DUCK_RAMP_MS == 40
    assert Config.BARGE_COLLECT_MS == 550
    assert Config.BARGE_AMBIGUOUS_MS == 800
    assert Config.BARGE_NOAEC_MIN_SPEECH_MS == 300
    assert Config.BARGE_NOAEC_THRESHOLD_BOOST == 1.5