"""Live-test finding: 'why did i change the volume' answered with the unrelated 'ui theme' change (shared letters 'the')."""
import pytest

from sara.core.memory import PreferencesDB


@pytest.fixture
def db(tmp_path):
    d = PreferencesDB(str(tmp_path / "p.db"))
    for key, old, new, why in [("mic_sensitivity", "50", "70", "you said the mic was too quiet"),
                               ("ui:theme", "ink", "sara", "user changed ui theme from 'ink' to 'sara'"),
                               ("ui:accent_hue", "", "200", "user changed the accent hue"),
                               ("speech_speed", "50", "60", "you asked me to talk faster")]:
        d.log_decision(key, old, new, why, wait=True)
    yield d
    d.close()


@pytest.mark.parametrize("query,expect", [
    ("mic sensitivity", "mic_sensitivity"), ("mic", "mic_sensitivity"), ("mic sensitivty", "mic_sensitivity"),
    ("theme", "ui:theme"), ("my theme", "ui:theme"), ("accent colour", "ui:accent_hue"), ("speech speed", "speech_speed"),
])
def test_real_settings_still_match(db, query, expect):
    got = db.find_decision_by_query(query)
    assert got and got["setting_key"] == expect


@pytest.mark.parametrize("query", ["the volume", "volume", "brightness", "the brightness", "wifi"])
def test_unrelated_queries_do_not_match_anything(db, query):
    assert db.find_decision_by_query(query) is None
