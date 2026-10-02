import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

# Same bootstrap as tests/test_barge_in_handoff.py: allow running this file
# directly by putting the project root (config.py, sara/) on sys.path.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.core.security import policy, taint
from sara.core.security.policy import (
    ALLOW,
    CONFIRM,
    DENY,
    MATRIX,
    decide,
    is_sensitive_path,
    user_message,
    validate_args,
)
from sara.core.security.tiers import T0, T1, T2, T3, TIERS, UNKNOWN_TIER, tier_of


def _cfg(mode="standard"):
    return SimpleNamespace(SECURITY_MODE=mode)


# One real intent per tier, taken from the provided tier table.
_T0_INTENT, _T1_INTENT, _T2_INTENT, _T3_INTENT = "weather", "set_volume", "typing_text", "shutdown_system"


class MatrixTests(unittest.TestCase):
    def test_sample_intents_have_expected_tiers(self):
        self.assertEqual(tier_of(_T0_INTENT), T0)
        self.assertEqual(tier_of(_T1_INTENT), T1)
        self.assertEqual(tier_of(_T2_INTENT), T2)
        self.assertEqual(tier_of(_T3_INTENT), T3)

    def test_matrix_covers_every_mode_context_and_tier(self):
        for mode, contexts in MATRIX.items():
            self.assertEqual(set(contexts), {"clean", "tainted", "flagged"})
            for row in contexts.values():
                self.assertEqual(len(row), 4)
                self.assertTrue(set(row) <= {ALLOW, CONFIRM, DENY})

    def test_read_only_intents_are_always_allowed(self):
        for mode in ("standard", "strict"):
            for context in ("clean", "tainted", "flagged"):
                self.assertEqual(MATRIX[mode][context][T0], ALLOW)

    def test_stricter_context_never_loosens_a_decision(self):
        rank = {ALLOW: 0, CONFIRM: 1, DENY: 2}
        for mode, contexts in MATRIX.items():
            for tier in range(4):
                clean = rank[contexts["clean"][tier]]
                tainted = rank[contexts["tainted"][tier]]
                flagged = rank[contexts["flagged"][tier]]
                self.assertLessEqual(clean, tainted)
                self.assertLessEqual(tainted, flagged)

    def test_strict_is_never_looser_than_standard(self):
        rank = {ALLOW: 0, CONFIRM: 1, DENY: 2}
        for context in ("clean", "tainted", "flagged"):
            for tier in range(4):
                self.assertGreaterEqual(
                    rank[MATRIX["strict"][context][tier]],
                    rank[MATRIX["standard"][context][tier]],
                )

    def _decide(self, intent, mode, tainted, flagged, args=None):
        return decide(intent, args, tainted=tainted, flagged=flagged, cfg=_cfg(mode))

    def test_standard_clean_allows_everything(self):
        for intent in (_T0_INTENT, _T1_INTENT, _T2_INTENT, _T3_INTENT):
            d = self._decide(intent, "standard", False, False)
            self.assertEqual(d.action, ALLOW, intent)
            self.assertEqual(d.reason, "ok")

    def test_standard_tainted(self):
        self.assertEqual(self._decide(_T1_INTENT, "standard", True, False).action, ALLOW)
        self.assertEqual(self._decide(_T2_INTENT, "standard", True, False).action, CONFIRM)
        self.assertEqual(self._decide(_T3_INTENT, "standard", True, False).action, CONFIRM)

    def test_standard_flagged(self):
        self.assertEqual(self._decide(_T0_INTENT, "standard", True, True).action, ALLOW)
        self.assertEqual(self._decide(_T1_INTENT, "standard", True, True).action, CONFIRM)
        self.assertEqual(self._decide(_T2_INTENT, "standard", True, True).action, DENY)
        self.assertEqual(self._decide(_T3_INTENT, "standard", True, True).action, DENY)

    def test_flagged_without_tainted_flag_still_counts(self):
        d = self._decide(_T2_INTENT, "standard", False, True)
        self.assertEqual(d.context, "flagged")
        self.assertEqual(d.action, DENY)

    def test_strict_mode(self):
        self.assertEqual(self._decide(_T3_INTENT, "strict", False, False).action, CONFIRM)
        self.assertEqual(self._decide(_T1_INTENT, "strict", True, False).action, CONFIRM)
        self.assertEqual(self._decide(_T3_INTENT, "strict", True, False).action, DENY)
        self.assertEqual(self._decide(_T1_INTENT, "strict", True, True).action, DENY)

    def test_off_mode_allows_everything_without_validation(self):
        d = decide("open_url", {"url": "javascript:alert(1)"},
                   tainted=True, flagged=True, cfg=_cfg("off"))
        self.assertEqual(d.action, ALLOW)
        self.assertEqual(d.reason, "security_off")

    def test_unknown_intent_is_t2(self):
        d = decide("totally_new_intent", None, tainted=True, flagged=False, cfg=_cfg())
        self.assertEqual(d.tier, UNKNOWN_TIER)
        self.assertEqual(d.action, CONFIRM)

    def test_confirm_state_alias_resolves(self):
        d = decide("forget_all_memories", None, tainted=False, flagged=False, cfg=_cfg("strict"))
        self.assertEqual(d.tier, T3)
        self.assertEqual(d.action, CONFIRM)

    def test_non_string_intent_does_not_crash(self):
        d = decide(["x"], None, tainted=False, flagged=False, cfg=_cfg())
        self.assertEqual(d.tier, UNKNOWN_TIER)

    def test_defaults_come_from_the_taint_record(self):
        with mock.patch.object(taint, "_current_gen", return_value=500):
            taint.clear_turn()
            self.assertEqual(decide(_T2_INTENT, cfg=_cfg()).action, ALLOW)
            taint.mark_turn("web_page")
            self.assertEqual(decide(_T2_INTENT, cfg=_cfg()).action, CONFIRM)
            taint.mark_turn("web_page", flagged=True)
            self.assertEqual(decide(_T2_INTENT, cfg=_cfg()).action, DENY)
            taint.clear_turn()

    def test_invalid_args_are_denied_even_when_matrix_would_allow(self):
        d = decide("open_url", {"url": "javascript:alert(1)"},
                   tainted=False, flagged=False, cfg=_cfg())
        self.assertEqual(d.action, DENY)
        self.assertEqual(d.reason, "invalid_args:bad_url_scheme")
        self.assertEqual(d.args, {})

    def test_valid_args_are_returned_as_a_copy(self):
        original = {"url": "  https://example.com  "}
        d = decide("open_url", original, tainted=False, flagged=False, cfg=_cfg())
        self.assertEqual(d.action, ALLOW)
        self.assertEqual(d.args["url"], "https://example.com")
        self.assertEqual(original["url"], "  https://example.com  ")

    def test_internal_error_fails_closed(self):
        with mock.patch.object(policy, "validate_args", side_effect=RuntimeError("boom")):
            with self.assertLogs("sara.core.security.policy", level="ERROR"):
                d = decide(_T0_INTENT, {}, tainted=False, flagged=False, cfg=_cfg())
        self.assertEqual(d.action, DENY)
        self.assertEqual(d.reason, "policy_error")

    def test_every_tier_in_the_table_is_valid(self):
        for intent, tier in TIERS.items():
            self.assertIn(tier, (T0, T1, T2, T3), intent)


class UrlTests(unittest.TestCase):
    def check(self, url, intent="open_url"):
        return validate_args(intent, {"url": url})

    def test_allowed_urls(self):
        for url in (
            "https://example.com/a?b=1", "http://example.com", "example.com",
            "www.example.com/path", "localhost:8080", "example.com:8443/x",
            "  https://example.com  ",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.check(url).ok)

    def test_dangerous_schemes_denied(self):
        for url in (
            "javascript:alert(1)", "JaVaScRiPt:alert(1)", "  javascript:alert(1)",
            "java\tscript:alert(1)", "java\nscript:alert(1)", "javascript:1",
            "data:text/html,<script>1</script>", "file:///C:/Windows/win.ini",
            "vbscript:msgbox(1)", "ms-settings:privacy", "ftp://example.com",
            "C:\\Windows\\System32\\cmd.exe", "powershell:foo",
        ):
            with self.subTest(url=url):
                result = self.check(url)
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "bad_url_scheme")

    def test_empty_and_huge_urls(self):
        self.assertEqual(self.check("   ").reason, "empty_url")
        self.assertFalse(self.check("https://example.com/" + "a" * 3000).ok)

    def test_summarize_url_is_checked_too(self):
        self.assertFalse(self.check("file:///etc/passwd", "summarize_url").ok)

    def test_url_rule_only_applies_to_url_intents(self):
        self.assertTrue(validate_args("typing_text", {"url": "javascript:x"}).ok)


class NameAndKeyTests(unittest.TestCase):
    def test_app_names(self):
        self.assertTrue(validate_args("open_app", {"target": "Google Chrome"}).ok)
        self.assertTrue(validate_args("open_app", {"target": "visual studio code"}).ok)
        for bad in ("calc & del /q *", "a | b", "x; y", "cmd `whoami`", "a$(b)", "%appdata%", "a\nb"):
            with self.subTest(bad=bad):
                self.assertEqual(
                    validate_args("open_app", {"target": bad}).reason, "bad_app_name"
                )
        self.assertFalse(validate_args("close_app", {"target": "x" * 200}).ok)

    def test_service_names(self):
        self.assertTrue(validate_args("start_service", {"service": "Spooler"}).ok)
        self.assertTrue(validate_args("stop_service", {"service": "wuauserv"}).ok)
        self.assertEqual(
            validate_args("stop_service", {"service": "x & shutdown /s"}).reason,
            "bad_service_name",
        )

    def test_blocked_key_combos(self):
        for combo in (
            "alt+f4", "Alt + F4", "ctrl+alt+delete", "ctrl+alt+del", "win+r",
            "windows+x", "win", "super+r", "ctrl+shift+esc", "ctrl+escape",
        ):
            with self.subTest(combo=combo):
                self.assertEqual(
                    validate_args("press_key", {"key": combo}).reason, "blocked_key_combo"
                )
        self.assertEqual(
            validate_args("press_key", {"keys": ["alt", "f4"]}).reason, "blocked_key_combo"
        )

    def test_normal_keys_allowed(self):
        for combo in ("enter", "ctrl+c", "ctrl+shift+t", "tab", "ctrl+-", "f5"):
            with self.subTest(combo=combo):
                self.assertTrue(validate_args("press_key", {"key": combo}).ok)

    def test_text_length_limits(self):
        self.assertTrue(validate_args("typing_text", {"text": "a" * 1000}).ok)
        self.assertEqual(validate_args("typing_text", {"text": "a" * 1001}).reason, "arg_too_long")
        self.assertEqual(validate_args("calendar_create", {"title": "a" * 201}).reason, "arg_too_long")


class GenericArgTests(unittest.TestCase):
    def test_none_and_empty_are_fine(self):
        self.assertTrue(validate_args("weather", None).ok)
        self.assertEqual(validate_args("weather", {}).args, {})

    def test_non_object_args(self):
        for bad in ("text", 5, ["a"]):
            self.assertEqual(validate_args("weather", bad).reason, "arg_not_object")

    def test_control_characters(self):
        self.assertEqual(validate_args("take_note", {"text": "a\x00b"}).reason, "arg_control_chars")
        self.assertEqual(validate_args("take_note", {"text": "a\x1bb"}).reason, "arg_control_chars")
        self.assertTrue(validate_args("take_note", {"text": "line1\nline2\tx\r\n"}).ok)

    def test_oversized_and_nested(self):
        self.assertEqual(validate_args("take_note", {"text": "a" * 4001}).reason, "arg_too_long")
        self.assertEqual(
            validate_args("take_note", {"x": ["ok", {"y": "a\x00"}]}).reason, "arg_control_chars"
        )
        deep = cur = {}
        for _ in range(8):
            cur["k"] = {}
            cur = cur["k"]
        self.assertEqual(validate_args("take_note", deep).reason, "arg_bad_type")

    def test_bad_types(self):
        self.assertEqual(validate_args("take_note", {"x": object()}).reason, "arg_bad_type")
        self.assertEqual(validate_args("take_note", {1: "x"}).reason, "arg_bad_type")

    def test_input_is_not_mutated(self):
        original = {"url": " https://example.com "}
        validate_args("open_url", original)
        self.assertEqual(original, {"url": " https://example.com "})

    def test_hindi_text_is_fine(self):
        self.assertTrue(validate_args("take_note", {"text": "कल सुबह नोट्स पढ़ने हैं"}).ok)


class SensitivePathTests(unittest.TestCase):
    def test_sensitive_paths(self):
        for path in (
            r"C:\Users\me\.ssh\id_rsa",
            "C:/Users/me/.aws/credentials",
            "/home/me/.gnupg/pubring.kbx",
            r"D:\project\.env",
            "project/.env.local",
            r"C:\Users\me\AppData\Local\Google\Chrome\User Data\Default\Login Data",
            r"C:\Users\me\AppData\Roaming\Mozilla\Firefox\Profiles\x.default\logins.json",
            r"C:\Windows\System32\config\SAM",
            "/etc/shadow",
            "backup/server.pem",
            "certs/private.KEY",
            "vault.kdbx",
            "credentials.json",
            "token.json",
            r"C:\Users\me\documents\..\.ssh\id_ed25519",
            r"C:\Users\me\.ssh.\config",
            "passwords.xlsx",
            "id_rsa.",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(is_sensitive_path(path))

    def test_ordinary_paths(self):
        for path in (
            r"C:\Users\me\Documents\report.docx",
            "notes/physics.md",
            "photos/sam.jpg",
            "environment.txt",
            "my cookies recipe.docx",
            "keynote.pptx",
            "C:/Users/me/Downloads",
            "",
            "   ",
        ):
            with self.subTest(path=path):
                self.assertIsNone(is_sensitive_path(path))

    def test_non_strings(self):
        self.assertIsNone(is_sensitive_path(None))
        self.assertIsNone(is_sensitive_path(123))

    def test_label_does_not_leak_the_path(self):
        self.assertNotIn("id_rsa", is_sensitive_path(r"C:\Users\me\.ssh\id_rsa"))

    def test_path_keys_are_checked(self):
        for args in (
            {"path": r"C:\Users\me\.ssh\id_rsa"},
            {"file": ".env"},
            {"directory": "/home/me/.aws"},
            {"paths": "x", "folder": "C:/Users/me/.gnupg"},
        ):
            with self.subTest(args=args):
                self.assertEqual(validate_args("open_downloads", args).reason, "sensitive_path")

    def test_find_file_query_is_checked(self):
        self.assertEqual(validate_args("find_file", {"query": "id_rsa"}).reason, "sensitive_path")
        self.assertEqual(validate_args("find_file", {"query": "*.pem"}).reason, "sensitive_path")
        self.assertEqual(validate_args("find_file", {"query": ".env"}).reason, "sensitive_path")
        self.assertTrue(validate_args("find_file", {"query": "physics notes"}).ok)
        self.assertTrue(validate_args("find_file", {"query": "sam"}).ok)

    def test_query_key_is_not_a_path_for_other_intents(self):
        self.assertTrue(validate_args("web_search", {"query": "how to use id_rsa with ssh"}).ok)

    def test_decide_denies_sensitive_path_for_read_only_intent(self):
        d = decide("find_file", {"query": "credentials.json"},
                   tainted=False, flagged=False, cfg=_cfg())
        self.assertEqual(d.action, DENY)
        self.assertEqual(d.reason, "invalid_args:sensitive_path")


class MessageTests(unittest.TestCase):
    def test_allow_has_no_message(self):
        d = decide(_T0_INTENT, None, tainted=False, flagged=False, cfg=_cfg())
        self.assertEqual(user_message(d), "")

    def test_deny_messages(self):
        d = decide("find_file", {"query": ".env"}, tainted=False, flagged=False, cfg=_cfg())
        self.assertIn("sensitive", user_message(d))
        self.assertIn("sensitive", user_message(d, "hinglish"))

    def test_flagged_message(self):
        d = decide(_T2_INTENT, None, tainted=True, flagged=True, cfg=_cfg())
        self.assertIn("instructions aimed at me", user_message(d))
        self.assertIn("instructions", user_message(d, "hinglish"))

    def test_confirm_message(self):
        d = decide(_T2_INTENT, None, tainted=True, flagged=False, cfg=_cfg())
        self.assertEqual(d.action, CONFIRM)
        self.assertIn("go ahead", user_message(d))

    def test_every_message_is_non_empty_in_both_languages(self):
        for lang in ("english", "hinglish"):
            for mode in ("standard", "strict"):
                for tainted, flagged in ((False, False), (True, False), (True, True)):
                    for intent in (_T0_INTENT, _T1_INTENT, _T2_INTENT, _T3_INTENT):
                        d = decide(intent, None, tainted=tainted, flagged=flagged, cfg=_cfg(mode))
                        if d.action != ALLOW:
                            self.assertTrue(user_message(d, lang), (lang, mode, intent, d.reason))


if __name__ == "__main__":
    unittest.main()
