import os
import re
import subprocess
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config

_KEYS = ("contextual_reminders", "contextual_notes", "awake_awareness", "late_night_nudges")


def _read(*parts):
    with open(os.path.join(_PROJECT_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class SettingsWiringTests(unittest.TestCase):
    def test_toggles_exist_in_settings_page_and_default_on(self):
        html = _read("sara", "gui", "index.html")
        for key in _KEYS:
            pattern = rf'<div class="toggle on" data-setting="{key}" role="switch" tabindex="0" aria-checked="true">'
            self.assertEqual(len(re.findall(pattern, html)), 1, key)

    def test_keys_are_restored_on_boot(self):
        source = _read("sara", "gui", "app", "settings.py")
        for key in _KEYS:
            self.assertIn(f'"setting:{key}"', source, key)

    def test_keys_are_read_by_the_backend(self):
        backend = _read("sara", "orchestrator", "proactive.py") + _read("sara", "orchestrator", "core_wiring.py")
        for key in _KEYS:
            # Some keys are read as "setting:<key>", others through a loop over bare names.
            found = f"setting:{key}" in backend or f'"{key}"' in backend
            self.assertTrue(found, f"{key} is not read anywhere in proactive.py / core_wiring.py")

    def test_html_is_balanced(self):
        html = _read("sara", "gui", "index.html")
        self.assertEqual(len(re.findall(r"<div\b", html)), len(re.findall(r"</div>", html)))


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        expected = {
            "REMINDER_GRACE_DEFAULT_MIN": 45,
            "REMINDER_GRACE_CRITICAL_MIN": 15,
            "REMINDER_GRACE_IMPORTANT_MIN": 60,
            "REMINDER_GRACE_ROUTINE_MIN": 45,
            "CONTEXTUAL_REMINDER_LLM": True,
            "CONTEXTUAL_REMINDER_LLM_TIMEOUT_S": 4.0,
            "CONTEXTUAL_REMINDER_NOTES_MIN_SCORE": 0.45,
            "CONTEXTUAL_REMINDER_NOTES_HIGH_SCORE": 0.65,
            "CONTEXTUAL_REMINDER_NUDGE_COOLDOWN_MINUTES": 30,
            "CONTEXTUAL_REMINDER_MAX_NUDGES": 2,
            "CONTEXTUAL_REMINDER_AWAKE_WINDOW_MINUTES": 10,
            "CONTEXTUAL_REMINDER_POST_DUE_GRACE_MINUTES": 120,
        }
        env_names = set(expected)
        if any(os.getenv(n) for n in env_names):
            self.skipTest("a smart-reminder value is set in .env / environment")
        for name, value in expected.items():
            self.assertEqual(getattr(Config, name), value, name)

    def test_every_smart_reminder_key_is_documented(self):
        example = _read(".env.example")
        names = [n for n in vars(Config) if n.startswith(("REMINDER_GRACE_", "CONTEXTUAL_REMINDER_"))]
        self.assertGreaterEqual(len(names), 12)
        for name in names:
            self.assertIn(name + "=", example, name)

    def test_env_variable_overrides_default(self):
        env = dict(os.environ, REMINDER_GRACE_DEFAULT_MIN="20", CONTEXTUAL_REMINDER_MAX_NUDGES="3",
                   CONTEXTUAL_REMINDER_LLM="False")
        out = subprocess.run(
            [sys.executable, "-c",
             "from config import Config; print(Config.REMINDER_GRACE_DEFAULT_MIN, "
             "Config.CONTEXTUAL_REMINDER_MAX_NUDGES, Config.CONTEXTUAL_REMINDER_LLM)"],
            cwd=_PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(out.stdout.strip().splitlines()[-1], "20 3 False", out.stderr[-300:])


if __name__ == "__main__":
    unittest.main()