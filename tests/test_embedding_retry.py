import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from sara.core.llm import clients
from sara.core.rag import LongTermMemory


class FakeAPIError(Exception):
    def __init__(self, code, message="boom"):
        super().__init__(message)
        self.code = code


def make_cfg(max_retries=2):
    return SimpleNamespace(
        EMBEDDING_BACKEND="gemini",
        EMBEDDING_MODEL="gemini-embedding-001",
        EMBEDDING_TIMEOUT_S=4.0,
        EMBEDDING_MAX_RETRIES=max_retries,
        EMBEDDING_RETRY_BASE_DELAY_S=0.01,
        GEMINI_API_KEY="test",
    )


def ok_result(values=(0.1, 0.2, 0.3)):
    return SimpleNamespace(embeddings=[SimpleNamespace(values=list(values))])


class EmbeddingRetryTests(unittest.TestCase):
    def _run(self, side_effect, max_retries=2):
        fake_client = mock.Mock()
        fake_client.models.embed_content.side_effect = side_effect
        with mock.patch.object(clients, "_get_gemini_client", return_value=fake_client), \
             mock.patch.object(clients.time, "sleep") as sleep_mock:
            out = clients._get_embedding_vector(make_cfg(max_retries), "hello world")
        return out, fake_client.models.embed_content.call_count, sleep_mock.call_count

    def test_success_first_try(self):
        out, calls, sleeps = self._run([ok_result()])
        self.assertEqual(out, [0.1, 0.2, 0.3])
        self.assertEqual(calls, 1)
        self.assertEqual(sleeps, 0)

    def test_api_500_then_success(self):
        out, calls, sleeps = self._run([FakeAPIError(500), ok_result()])
        self.assertEqual(out, [0.1, 0.2, 0.3])
        self.assertEqual(calls, 2)
        self.assertEqual(sleeps, 1)

    def test_retry_exhaustion_logs_error_and_returns_none(self):
        with self.assertLogs("sara.core.llm.clients", level="ERROR") as cm:
            out, calls, sleeps = self._run(
                [FakeAPIError(500), FakeAPIError(500), FakeAPIError(500)]
            )
        self.assertIsNone(out)
        self.assertEqual(calls, 3)
        self.assertEqual(sleeps, 2)
        self.assertTrue(any("FAILED after 3 attempt" in m for m in cm.output))

    def test_client_error_is_not_retried(self):
        with self.assertLogs("sara.core.llm.clients", level="ERROR"):
            out, calls, sleeps = self._run([FakeAPIError(400)])
        self.assertIsNone(out)
        self.assertEqual(calls, 1)
        self.assertEqual(sleeps, 0)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.mem = LongTermMemory(db_path=self.tmp.name + "/test.db")

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_embedding_unavailable_logs_warning_and_stores_nothing(self):
        with mock.patch.object(self.mem, "_get_embedding", return_value=None), \
             self.assertLogs("sara.core.rag", level="WARNING") as cm:
            self.mem._write_one("my dog is Max", "conversation", "2026-10-01T00:00:00")
        self.assertTrue(any("Memory NOT stored" in m for m in cm.output))
        self.assertEqual(self.mem.memory_count(), 0)

    def test_storage_failure_is_logged_and_does_not_raise(self):
        real_conn = self.mem._conn
        bad_conn = mock.Mock()
        bad_conn.execute.side_effect = sqlite3.OperationalError("disk I/O error")
        self.mem._conn = bad_conn
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        try:
            with mock.patch.object(self.mem, "_get_embedding", return_value=vec), \
                 self.assertLogs("sara.core.rag", level="ERROR") as cm:
                self.mem._write_one("my dog is Max", "conversation", "2026-10-01T00:00:00")
        finally:
            self.mem._conn = real_conn
        self.assertTrue(any("DB insert failed" in m for m in cm.output))
        self.assertEqual(self.mem.memory_count(), 0)

    def test_successful_store_keeps_float32_blob_compatible(self):
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        with mock.patch.object(self.mem, "_get_embedding", return_value=vec):
            self.mem._write_one("my dog is Max", "conversation", "2026-10-01T00:00:00")
        self.assertEqual(self.mem.memory_count(), 1)
        row = self.mem._conn.execute(
            "SELECT embedding FROM long_term_memory"
        ).fetchone()
        stored = np.frombuffer(row[0], dtype=np.float32)
        np.testing.assert_allclose(stored, vec)


if __name__ == "__main__":
    unittest.main()