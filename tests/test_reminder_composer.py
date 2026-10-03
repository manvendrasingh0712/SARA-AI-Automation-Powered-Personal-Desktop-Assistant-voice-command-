import os
import sys
import time
import unittest
from datetime import datetime
from types import SimpleNamespace

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.core.llm.provider_runtime import GenResult
from sara.tools.reminder_composer import ReminderComposer

_NOW = datetime(2026, 10, 3, 1, 0)


def _cfg(**kw):
    base = dict(CONTEXTUAL_REMINDER_LLM=True, CONTEXTUAL_REMINDER_LLM_TIMEOUT_S=0.5)
    base.update(kw)
    return SimpleNamespace(**base)


def _ok(text, provider="gemini"):
    return GenResult(text, provider, "success", ((provider, "success"),), 120)


def _fail(kind, provider="none"):
    return GenResult(None, provider, kind, (("gemini", kind),), 50)


def _composer(generate, cfg=None, breaker_getter=None):
    return ReminderComposer(
        cfg or _cfg(), breaker_getter=breaker_getter, generate=generate, clock=lambda: _NOW
    )


class ComposerTests(unittest.TestCase):
    def test_llm_success(self):
        c = _composer(lambda *a, **k: _ok("It's 1 AM, time to get some rest."))
        r = c.start("sleep")()
        self.assertEqual((r.source, r.provider, r.failure), ("llm", "gemini", "success"))
        self.assertEqual(r.text, "It's 1 AM, time to get some rest.")
        self.assertEqual((r.category, r.level), ("sleep", "routine"))

    def test_invalid_output_uses_template(self):
        c = _composer(lambda *a, **k: _ok("**Sure!** time to sleep"))
        r = c.start("sleep")()
        self.assertEqual((r.source, r.failure), ("template", "invalid_output"))
        self.assertEqual(r.text, "It's 1:00 AM. Time to wind down and get some rest.")

    def test_provider_failure_uses_template_and_reports_reason(self):
        c = _composer(lambda *a, **k: _fail("quota"))
        r = c.start("check the oven")()
        self.assertEqual((r.source, r.failure), ("template", "quota"))
        self.assertEqual(r.text, "Reminder: check the oven")

    def test_llm_disabled_never_calls_generate(self):
        calls = []
        c = _composer(lambda *a, **k: calls.append(1), cfg=_cfg(CONTEXTUAL_REMINDER_LLM=False))
        r = c.start("sleep")()
        self.assertEqual((r.source, r.failure), ("template", "disabled"))
        self.assertEqual(calls, [])

    def test_generate_exception_uses_template(self):
        def boom(*a, **k):
            raise RuntimeError("boom")

        r = _composer(boom).start("sleep")()
        self.assertEqual((r.source, r.failure), ("template", "error"))

    def test_slow_generate_hits_deadline(self):
        def slow(*a, **k):
            time.sleep(5)
            return _ok("too late")

        started = time.monotonic()
        wait = _composer(slow).start("sleep")
        self.assertLess(time.monotonic() - started, 0.3)  # start() does not block
        r = wait()
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual((r.source, r.failure), ("template", "timeout"))

    def test_empty_message_gets_default(self):
        calls = []
        r = _composer(lambda *a, **k: calls.append(1)).start("")()
        self.assertEqual((r.source, r.failure), ("default", "empty"))
        self.assertEqual(calls, [])

    def test_prompt_and_breaker_are_passed_to_generate(self):
        seen = {}

        def gen(prompt, system, cfg, **kw):
            seen.update(prompt=prompt, system=system, kw=kw)
            return _ok("Time to rest now.")

        marker = object()
        _composer(gen, breaker_getter=lambda: marker).start("sleep")()
        self.assertIn("<verified_reminder>sleep</verified_reminder>", seen["prompt"])
        self.assertIs(seen["kw"]["breaker"], marker)
        self.assertEqual(seen["kw"]["total_budget_s"], 0.5)

    def test_breaker_getter_error_does_not_break_delivery(self):
        def bad_getter():
            raise RuntimeError("no brain")

        r = _composer(lambda *a, **k: _ok("x"), breaker_getter=bad_getter).start("sleep")()
        self.assertEqual(r.source, "template")
        self.assertTrue(r.text)

    def test_describe_is_loggable(self):
        r = _composer(lambda *a, **k: _ok("Time to rest now.")).start("sleep")()
        self.assertIn("wording=llm", r.describe())
        self.assertIn("provider=gemini", r.describe())


if __name__ == "__main__":
    unittest.main()