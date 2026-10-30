import time

from sara.orchestrator.context_tracking import _resolve_app_target


def _ctx(app):
    return {"context_state": {"recent_entities": {"last_app": {"value": app, "ts": time.time()}}}}


def test_generic_app_word_without_context_asks_instead_of_closing_app():
    assert _resolve_app_target({}, "the app") is None
    assert _resolve_app_target({}, "app") is None


def test_generic_app_word_resolves_to_last_app():
    assert _resolve_app_target(_ctx("notepad"), "the app") == "notepad"


def test_real_app_name_unchanged():
    assert _resolve_app_target({}, "chrome") == "chrome"
