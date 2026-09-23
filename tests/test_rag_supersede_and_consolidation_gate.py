"""
tests/test_rag_supersede_and_consolidation_gate.py

Focused regression tests for two recent changes:

  1. memory_consolidation._consolidation_loop()'s reachability gate now
     reflects brain.is_usable() (the real, current circuit-breaker-aware
     backend-health state exposed by SaraLLM) instead of a hardcoded
     Gemini-only probe.

  2. rag.py's LongTermMemory.add_memory() now supersedes (deletes +
     replaces) an existing highly-similar fact/consolidation-sourced
     memory instead of piling up contradicting duplicates, while
     leaving raw conversation-sourced memories completely untouched.

Follows the exact same conventions as tests/test_sara_smoke.py: plain
unittest.TestCase, temp sqlite files cleaned up in try/finally or
tearDown, hand-written fake classes instead of unittest.mock, and real
background threads run for a short, bounded window rather than mocked
away (see test_reminder_manager_shutdown() in that file for the same
pattern applied to a different background thread).
"""

import contextlib
import os
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

# See tests/test_sara_smoke.py for why this is needed: running this file
# directly (`python tests/test_rag_supersede_and_consolidation_gate.py`)
# only puts tests/ on sys.path, not the project root where `config.py`
# and `sara/` live.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config

import sara.core.memory_consolidation as memory_consolidation
from sara.core.rag import LongTermMemory


@contextlib.contextmanager
def _temporarily_set_config_attr(name, value):
    """
    Sets Config.<name> = value for the duration of the `with` block,
    then restores it to whatever it was before -- or removes it
    entirely if it didn't exist as a class attribute at all.

    Several of the attributes touched by these tests
    (MEMORY_CONSOLIDATION_ENABLED, MEMORY_CONSOLIDATION_INTERVAL_S,
    RAG_ENABLED) are only ever read via getattr(..., default) in the
    production code and may not be defined on Config at all in a given
    environment. A plain "save the old value, restore it in finally"
    (the pattern test_sara_smoke.py's test_notes_qa_sync_and_
    skip_unchanged() already uses for NOTES_FOLDER, which IS always
    defined) would silently leave a brand-new attribute permanently
    stuck on the shared Config class after the test ran if the
    attribute didn't already exist -- this context manager avoids that.
    """
    _MISSING = object()
    original = getattr(Config, name, _MISSING)
    setattr(Config, name, value)
    try:
        yield
    finally:
        if original is _MISSING:
            delattr(Config, name)
        else:
            setattr(Config, name, original)


class ConsolidationGateTests(unittest.TestCase):
    """
    Verifies _consolidation_loop()'s reachability gate genuinely reflects
    brain.is_usable() -- the real, current circuit-breaker-aware backend
    health state -- rather than the old hardcoded Gemini-only probe.
    Runs the loop on a real daemon thread for a short, bounded window
    (same approach as test_reminder_manager_shutdown() in
    test_sara_smoke.py) instead of calling the intentionally-infinite
    loop function directly.
    """

    @staticmethod
    def _run_loop_and_collect_ticks(brain, run_for=0.25, interval=0.02):
        """
        Runs _consolidation_loop() against a fake, always-enabled
        rag_memory and the given fake `brain` for `run_for` seconds,
        with _consolidation_tick() swapped out for a simple recorder --
        so this only ever exercises the GATING logic, never the real
        extraction/storage path (that's covered separately by
        RagSupersedeTests below). Returns the list of recorded ticks.
        """

        class _FakeRagMemoryEnabled:
            enabled = True

        tick_calls = []
        original_tick = memory_consolidation._consolidation_tick
        memory_consolidation._consolidation_tick = (
            lambda db, brain, rag_memory: tick_calls.append(1)
        )

        stop_event = threading.Event()
        try:
            with _temporarily_set_config_attr(
                "MEMORY_CONSOLIDATION_ENABLED", True
            ), _temporarily_set_config_attr(
                "MEMORY_CONSOLIDATION_INTERVAL_S", interval
            ):
                thread = threading.Thread(
                    target=memory_consolidation._consolidation_loop,
                    args=(None, brain, _FakeRagMemoryEnabled(), stop_event),
                    name="test-consolidation-loop",
                    daemon=True,
                )
                thread.start()
                time.sleep(run_for)
                stop_event.set()
                thread.join(timeout=2.0)
        finally:
            # Always restore the real tick function, even if the loop
            # itself somehow raised -- a leaked monkeypatch here would
            # silently break every OTHER test file that imports this
            # module afterward in the same test run.
            memory_consolidation._consolidation_tick = original_tick

        return tick_calls

    def test_gate_runs_ticks_when_brain_is_usable(self):
        class _FakeBrainUsable:
            def is_usable(self):
                return True

        tick_calls = self._run_loop_and_collect_ticks(_FakeBrainUsable())
        self.assertGreaterEqual(
            len(tick_calls),
            1,
            "consolidation should tick at least once while the brain "
            "reports itself usable",
        )

    def test_gate_skips_ticks_when_brain_is_not_usable(self):
        class _FakeBrainUnusable:
            def is_usable(self):
                return False

            def generate_response(self, prompt):
                # If this were ever reached, the gate failed to skip the
                # tick before calling into the brain -- fail loudly
                # rather than silently.
                raise AssertionError(
                    "generate_response() must never be reached while "
                    "is_usable() is False"
                )

        tick_calls = self._run_loop_and_collect_ticks(_FakeBrainUnusable())
        self.assertEqual(
            tick_calls,
            [],
            "consolidation must not tick at all while the brain reports "
            "itself unusable -- regardless of whether Gemini specifically "
            "is configured or reachable",
        )


# Deterministic stand-in vectors for the fake embedder below. Any two
# vectors that share a dominant "direction" are cosine-similar; ones
# that don't are (close to) orthogonal. The exact values are hand-picked
# only to land clearly above or below the 0.92 supersede threshold --
# nothing about their magnitude is otherwise meaningful.
_VEC_DELHI = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_VEC_JAIPUR = [0.99, 0.14, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # cos-sim vs Delhi ~= 0.99
_VEC_COLOR = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # orthogonal to both above


def _make_fake_embedder():
    """
    Returns a stand-in for LongTermMemory._get_embedding() that maps
    text to one of the fixed vectors above via a case-insensitive
    keyword match (checked in order -- first match wins), so cosine
    similarity between any two test memories is fully deterministic and
    never depends on a real (networked) Gemini embedding call.
    """
    vector_by_keyword = {
        "jaipur": _VEC_JAIPUR,
        "delhi": _VEC_DELHI,
        "color": _VEC_COLOR,
    }

    def _fake_get_embedding(text):
        lowered = (text or "").lower()
        for keyword, vector in vector_by_keyword.items():
            if keyword in lowered:
                return np.array(vector, dtype=np.float32)
        return np.array([0.0] * 8, dtype=np.float32)

    return _fake_get_embedding


class RagSupersedeTests(unittest.TestCase):
    """
    Verifies LongTermMemory.add_memory()'s supersede logic: a new
    fact/consolidation-sourced memory that closely matches an existing
    one replaces it instead of piling up as a contradicting duplicate,
    an unrelated one is stored normally, a near-identical re-extraction
    is skipped rather than duplicated, and raw conversation-sourced
    memories are never touched by any of this.

    Uses a REAL LongTermMemory backed by a temp sqlite file (same
    approach as test_preferences_db() in test_sara_smoke.py) with only
    the embedding call replaced by the deterministic fake above --
    everything else (the background writer thread, the real DB writes,
    search()'s real cosine-similarity ranking) runs exactly as it does
    in production.
    """

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp.close()
        self._db_path = tmp.name
        self._rag = self._make_test_rag(self._db_path)

    def tearDown(self):
        self._rag.close()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    @staticmethod
    def _make_test_rag(db_path):
        with _temporarily_set_config_attr("RAG_ENABLED", True):
            rag = LongTermMemory(db_path=db_path)
        # Pin every threshold this feature depends on to a known value,
        # regardless of whatever the real Config module happens to
        # define -- so this test's behavior can never silently drift if
        # someone tunes the real Config later.
        rag._min_similarity = 0.40
        rag._fact_min_similarity = 0.30
        rag._fact_supersede_min_similarity = 0.92
        # Instance-level override: shadows the embedding method for this
        # one object only, so add_memory()/search() never make a real
        # (networked) Gemini embedding call during this test.
        rag._get_embedding = _make_fake_embedder()
        return rag

    def _wait_until(self, condition_fn, timeout=3.0, interval=0.02):
        """
        Polls `condition_fn` until it returns truthy or `timeout`
        elapses. Needed because add_memory() is fire-and-forget -- the
        actual write/supersede happens on the background writer thread,
        not synchronously in the calling test. Exceptions from
        condition_fn (e.g. indexing list_memories()[0] on a momentarily
        empty list mid-write) are treated as "not yet" and retried
        rather than failing the test outright.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if condition_fn():
                    return
            except Exception:
                pass
            time.sleep(interval)
        self.fail(f"Condition not met within {timeout}s.")

    def test_close_fact_match_is_superseded(self):
        self._rag.add_memory("The user lives in Delhi.", source="fact")
        self._wait_until(lambda: self._rag.memory_count() == 1)

        self._rag.add_memory("The user lives in Jaipur.", source="fact")
        # Superseding is TWO async writer-thread jobs (store the new
        # fact, then delete the old one) -- wait for the final, settled
        # state rather than just the count, since the count alone can't
        # distinguish "old deleted" from "old still there, new not yet
        # written".
        self._wait_until(
            lambda: [m["text"] for m in self._rag.list_memories()]
            == ["The user lives in Jaipur."]
        )

    def test_unrelated_fact_is_not_superseded(self):
        self._rag.add_memory("The user lives in Delhi.", source="fact")
        self._wait_until(lambda: self._rag.memory_count() == 1)

        self._rag.add_memory(
            "The user's favorite color is blue.", source="consolidation"
        )
        self._wait_until(lambda: self._rag.memory_count() == 2)

        texts = {m["text"] for m in self._rag.list_memories()}
        self.assertEqual(
            texts,
            {"The user lives in Delhi.", "The user's favorite color is blue."},
        )

    def test_near_identical_duplicate_is_skipped_not_re_added(self):
        self._rag.add_memory("The user lives in Delhi.", source="fact")
        self._wait_until(lambda: self._rag.memory_count() == 1)

        # Same fact, re-extracted with different casing/punctuation --
        # e.g. maybe_extract_fact() and the consolidation daemon both
        # independently noticed the same underlying statement.
        self._rag.add_memory("the user lives in delhi.", source="fact")

        # A fixed sleep, NOT a "wait until count == 1" -- that would
        # trivially pass even if the write hadn't run yet at all. This
        # gives a real chance for a wrongly-implemented version to
        # (incorrectly) store a second row before we assert it didn't.
        time.sleep(0.3)

        self.assertEqual(self._rag.memory_count(), 1)
        self.assertEqual(
            self._rag.list_memories()[0]["text"], "The user lives in Delhi."
        )

    def test_conversation_source_is_never_superseded(self):
        self._rag.add_memory(
            "User said: I live in Delhi\nSara replied: Got it.",
            source="conversation",
        )
        self._wait_until(lambda: self._rag.memory_count() == 1)

        # Deliberately near-identical to the entry above (same "Delhi"
        # keyword -> same fake embedding vector, so this WOULD trigger
        # the supersede path if it were mistakenly applied to
        # conversation-sourced memories). It must not be -- both entries
        # should survive untouched.
        self._rag.add_memory(
            "User said: I live in Delhi again\nSara replied: Noted.",
            source="conversation",
        )
        self._wait_until(lambda: self._rag.memory_count() == 2)

        self.assertEqual(len(self._rag.list_memories()), 2)


if __name__ == "__main__":
    unittest.main()