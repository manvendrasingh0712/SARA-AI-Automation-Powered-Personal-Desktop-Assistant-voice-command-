import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.core.llm import provider_runtime as pr


class _Err(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class _Breaker:
    def __init__(self, active=False):
        self._active = active
        self.records = []

    def active(self):
        return self._active

    def record(self, ok):
        self.records.append(ok)


def _cfg(**kw):
    base = dict(LLM_BACKEND="gemini", LLM_FALLBACK_ENABLED=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _calls(gemini=None, ollama=None):
    return mock.patch.dict(
        pr._CALLS,
        {
            "gemini": gemini or mock.Mock(return_value="gemini text"),
            "ollama": ollama or mock.Mock(return_value="ollama text"),
        },
    )


class ClassifyFailureTests(unittest.TestCase):
    def test_status_codes(self):
        self.assertEqual(pr.classify_failure(_Err("You exceeded your current quota", 429)), pr.QUOTA)
        self.assertEqual(pr.classify_failure(_Err("slow down", 429)), pr.RATE_LIMIT)
        self.assertEqual(pr.classify_failure(_Err("nope", 403)), pr.AUTH)
        self.assertEqual(pr.classify_failure(_Err("API key not valid", 400)), pr.AUTH)
        self.assertEqual(pr.classify_failure(_Err("bad request", 400)), pr.INVALID)
        self.assertEqual(pr.classify_failure(_Err("oops", 503)), pr.SERVER)
        self.assertEqual(pr.classify_failure(_Err("slow", 408)), pr.TIMEOUT)

    def test_exception_types(self):
        self.assertEqual(pr.classify_failure(TimeoutError("x")), pr.TIMEOUT)
        self.assertEqual(pr.classify_failure(ConnectionError("x")), pr.NETWORK)

    def test_message_fallbacks(self):
        self.assertEqual(pr.classify_failure(RuntimeError("request timed out")), pr.TIMEOUT)
        self.assertEqual(pr.classify_failure(RuntimeError("RESOURCE_EXHAUSTED")), pr.QUOTA)
        self.assertEqual(pr.classify_failure(RuntimeError("something odd")), pr.UNAVAILABLE)


class GenerateShortTests(unittest.TestCase):
    def test_primary_success(self):
        with _calls() as _:
            r = pr.generate_short("p", "s", _cfg())
        self.assertEqual((r.text, r.provider, r.failure), ("gemini text", "gemini", pr.SUCCESS))
        self.assertEqual(r.attempts, (("gemini", pr.SUCCESS),))

    def test_gemini_quota_falls_back_to_ollama(self):
        g = mock.Mock(side_effect=_Err("quota exceeded", 429))
        with _calls(gemini=g):
            r = pr.generate_short("p", "s", _cfg())
        self.assertEqual((r.text, r.provider), ("ollama text", "ollama"))
        self.assertEqual(r.attempts, (("gemini", pr.QUOTA), ("ollama", pr.SUCCESS)))

    def test_empty_primary_falls_back(self):
        with _calls(gemini=mock.Mock(return_value="")):
            r = pr.generate_short("p", "s", _cfg())
        self.assertEqual(r.provider, "ollama")
        self.assertEqual(r.attempts[0], ("gemini", pr.EMPTY))

    def test_both_fail_returns_none(self):
        g = mock.Mock(side_effect=_Err("x", 503))
        o = mock.Mock(side_effect=ConnectionError("down"))
        with _calls(gemini=g, ollama=o):
            r = pr.generate_short("p", "s", _cfg())
        self.assertIsNone(r.text)
        self.assertEqual(r.provider, "none")
        self.assertEqual(r.failure, pr.NETWORK)
        self.assertEqual(r.attempts, (("gemini", pr.SERVER), ("ollama", pr.NETWORK)))

    def test_ollama_primary_never_calls_gemini(self):
        g = mock.Mock(return_value="should not be used")
        with _calls(gemini=g):
            r = pr.generate_short("p", "s", _cfg(LLM_BACKEND="ollama"))
        self.assertEqual(r.provider, "ollama")
        g.assert_not_called()

    def test_fallback_disabled(self):
        g = mock.Mock(side_effect=_Err("x", 503))
        o = mock.Mock(return_value="ollama text")
        with _calls(gemini=g, ollama=o):
            r = pr.generate_short("p", "s", _cfg(LLM_FALLBACK_ENABLED=False))
        self.assertIsNone(r.text)
        o.assert_not_called()

    def test_active_breaker_skips_gemini(self):
        g = mock.Mock(return_value="gemini text")
        b = _Breaker(active=True)
        with _calls(gemini=g):
            r = pr.generate_short("p", "s", _cfg(), breaker=b)
        self.assertEqual(r.provider, "ollama")
        g.assert_not_called()
        self.assertEqual(r.attempts[0], ("gemini", pr.UNAVAILABLE))

    def test_breaker_records_success_and_failure(self):
        b = _Breaker()
        with _calls():
            pr.generate_short("p", "s", _cfg(), breaker=b)
        self.assertEqual(b.records, [True])
        b2 = _Breaker()
        with _calls(gemini=mock.Mock(side_effect=_Err("x", 503))):
            pr.generate_short("p", "s", _cfg(), breaker=b2)
        self.assertEqual(b2.records, [False])

    def test_timeout_does_not_count_against_breaker(self):
        b = _Breaker()
        with _calls(gemini=mock.Mock(side_effect=TimeoutError("slow"))):
            pr.generate_short("p", "s", _cfg(), breaker=b)
        self.assertEqual(b.records, [])

    def test_invalid_failure_does_not_count_against_breaker(self):
        b = _Breaker()
        with _calls(gemini=mock.Mock(side_effect=_Err("bad", 400))):
            pr.generate_short("p", "s", _cfg(), breaker=b)
        self.assertEqual(b.records, [])

    def test_hard_deadline(self):
        def slow(*a, **k):
            time.sleep(3)
            return "late"

        started = time.monotonic()
        with _calls(gemini=slow, ollama=mock.Mock(side_effect=_Err("x", 503))):
            r = pr.generate_short("p", "s", _cfg(), timeout_s=0.3)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(r.attempts[0], ("gemini", pr.TIMEOUT))
        self.assertIsNone(r.text)

    def test_total_budget_skips_second_provider(self):
        def slow(*a, **k):
            time.sleep(3)
            return "late"

        o = mock.Mock(return_value="ollama text")
        with _calls(gemini=slow, ollama=o):
            r = pr.generate_short("p", "s", _cfg(), timeout_s=0.6, total_budget_s=0.9)
        self.assertIsNone(r.text)
        o.assert_not_called()

    def test_never_raises_on_broken_breaker(self):
        class Bad:
            def active(self):
                raise RuntimeError("boom")

            def record(self, ok):
                raise RuntimeError("boom")

        with _calls():
            r = pr.generate_short("p", "s", _cfg(), breaker=Bad())
        self.assertEqual(r.text, "gemini text")


if __name__ == "__main__":
    unittest.main()