"""
Runs every smart-reminder test file in one process and prints one summary.

    python tests/run_reminder_suite.py

Exit code is 0 only if everything passed.
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

MODULES = (
    "test_reminder_grace",
    "test_reminder_event",
    "test_reminder_context",
    "test_reminder_prompt",
    "test_provider_runtime",
    "test_reminder_composer",
    "test_reminder_upcoming",
    "test_proactive_upcoming",
    "test_reminder_evidence",
    "test_reminder_followup",
    "test_proactive_followup",
    "test_reminder_settings",
    "test_reminder_scenarios",
    "test_reminder_night_story",
)


def main() -> int:
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    for name in MODULES:
        suite.addTests(loader.loadTestsFromName(f"tests.{name}"))
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    print()
    print(
        f"SMART REMINDERS: {result.testsRun} tests, "
        f"{len(result.failures)} failed, {len(result.errors)} errors, "
        f"{len(result.skipped)} skipped"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())