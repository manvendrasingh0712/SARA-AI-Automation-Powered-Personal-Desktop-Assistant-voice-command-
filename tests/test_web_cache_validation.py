from urllib.parse import parse_qs, urlsplit

import pytest

from sara.tools import web


_PAGE_TEXT = "A useful article paragraph with enough text to be retained by extraction. " * 3


class _Response:
    encoding = "utf-8"

    def __init__(self, text: str):
        self._body = f"<html><body><p>{text}</p></body></html>".encode(self.encoding)

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self._body

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _isolated_web_cache(monkeypatch):
    with web._cache_lock:
        web._cache.clear()

    calls = []

    def fake_pinned_get(url, _pinned_ip):
        calls.append(url)
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        label = " / ".join(query.get("q", query.get("tag", ["page"])))
        return _Response(f"{label}: {_PAGE_TEXT}"), None, None

    monkeypatch.setattr(web, "_validate_url_and_pin", lambda _url: (None, "203.0.113.10"))
    monkeypatch.setattr(web, "_pinned_get", fake_pinned_get)
    monkeypatch.setattr("sara.core.security.untrusted.note_untrusted", lambda *_args: None)
    return calls


def test_different_query_values_have_distinct_cache_entries(_isolated_web_cache):
    calls = _isolated_web_cache

    python_result = web.read_webpage("https://example.test/search?q=python")
    java_result = web.read_webpage("https://example.test/search?q=java")

    assert "python" in python_result
    assert "java" in java_result
    assert len(calls) == 2


def test_repeated_query_parameters_are_preserved_in_cache_identity(_isolated_web_cache):
    calls = _isolated_web_cache
    url = "https://example.test/search?tag=python&tag=java"
    another_url = "https://example.test/search?tag=python&tag=java&tag=rust"

    result = web.read_webpage(url)
    another_result = web.read_webpage(another_url)

    assert "python / java" in result
    assert "python / java / rust" in another_result
    assert urlsplit(calls[0]).query == "tag=python&tag=java"
    assert urlsplit(calls[1]).query == "tag=python&tag=java&tag=rust"
    assert len(calls) == 2


def test_identical_request_reuses_cached_extraction(_isolated_web_cache):
    calls = _isolated_web_cache
    url = "https://example.test/article?q=python"

    first = web.read_webpage(url)
    second = web.read_webpage(url)

    assert second == first
    assert len(calls) == 1


def test_url_fragments_do_not_create_separate_cache_entries(_isolated_web_cache):
    calls = _isolated_web_cache
    first = web.read_webpage("https://example.test/article?q=python#section-one")
    second = web.read_webpage("https://example.test/article?q=python#section-two")

    assert second == first
    assert len(calls) == 1


def test_max_chars_is_part_of_cache_identity(_isolated_web_cache):
    calls = _isolated_web_cache
    url = "https://example.test/article?q=python"

    short = web.read_webpage(url, max_chars=20)
    longer = web.read_webpage(url, max_chars=80)

    assert short != longer
    assert len(calls) == 2


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "20", None, []])
def test_invalid_max_chars_returns_existing_error_without_fetch(value, _isolated_web_cache):
    calls = _isolated_web_cache

    result = web.read_webpage("https://example.test/article", max_chars=value)

    assert result == "Error: max_chars must be a positive integer."
    assert calls == []


def test_positive_integer_max_chars_is_accepted(_isolated_web_cache):
    calls = _isolated_web_cache

    result = web.read_webpage("https://example.test/article", max_chars=40)

    assert not result.startswith("Error:")
    assert len(calls) == 1
