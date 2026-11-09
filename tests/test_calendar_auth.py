import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from sara.tools import calendar


class CalendarAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.credentials_path = os.path.join(self.temp.name, "credentials.json")
        with open(self.credentials_path, "w", encoding="utf-8") as credentials:
            credentials.write("{}")
        self.patches = [
            mock.patch.object(calendar, "_GOOGLE_LIBS_AVAILABLE", True),
            mock.patch.object(calendar, "_creds_path", return_value=self.credentials_path),
            mock.patch.object(calendar, "_token_path", return_value=""),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_read_only_status_and_event_checks_do_not_start_oauth(self):
        flow_factory = mock.Mock()
        with mock.patch.object(calendar, "InstalledAppFlow", flow_factory):
            self.assertEqual(calendar.get_calendar_status(), {"connected": False, "email": None})
            self.assertEqual(calendar.get_today_events(), [])
            self.assertEqual(calendar.get_upcoming_events(days=1), [])

        flow_factory.from_client_secrets_file.assert_not_called()

    def test_explicit_calendar_request_can_start_oauth(self):
        credentials = SimpleNamespace(valid=True, to_json=lambda: "{}")
        flow = mock.Mock()
        flow.run_local_server.return_value = credentials
        flow_factory = mock.Mock()
        flow_factory.from_client_secrets_file.return_value = flow

        with mock.patch.object(calendar, "InstalledAppFlow", flow_factory), mock.patch.object(
            calendar, "build", return_value=object()
        ):
            self.assertIsNotNone(calendar.get_calendar_service(allow_interactive_auth=True))

        flow.run_local_server.assert_called_once_with(port=0)


if __name__ == "__main__":
    unittest.main()
