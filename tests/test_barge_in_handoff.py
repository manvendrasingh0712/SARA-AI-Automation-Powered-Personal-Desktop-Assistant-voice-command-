import os
import sys
import threading
import time
import unittest

# See tests/test_sara_smoke.py for why this is needed: running this file
# directly (`python tests/test_barge_in_handoff.py`) only puts tests/ on
# sys.path, not the project root where `config.py` and `sara/` live.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ----------------------------------------------------------------------------
# Fakes for the parts of SpeechToText that are NOT under test here.
#
# SpeechToText.__init__() opens a real mic stream and loads the
# faster-whisper model (see engine.py) -- far too heavy/hardware-dependent
# for a unit test, and irrelevant to the barge-in handoff logic we're
# verifying. So instead of instantiating via __init__, every test below
# builds a "bare" instance with SpeechToText.__new__() and hand-wires only
# the handful of attributes that mark_tts_stopped(), wait_settle(),
# capture_barge_in_audio(), and _collect_speech()'s backlog-handoff logic
# actually touch -- using tiny Fake* stand-ins for everything else, the
# same convention test_sara_smoke.py already uses (_FakeTTS, _FakeBrain,
# _FakeRagMemory) for stubbing dependencies while exercising real code.
#
# The one piece deliberately kept REAL is self._ring (an actual
# _RingBuffer) -- that's the object whose clear-vs-preserve behavior is
# what this whole change is about, so faking it would test nothing.
# ----------------------------------------------------------------------------


class _FakePreBuffer:
    """Stands in for _PreBuffer. drain() returns None (no pre-speech
    padding to prepend), matching the "if pre" falsy branch in
    _collect_speech(); clear() is a no-op."""

    def clear(self):
        pass

    def drain(self):
        return None


class _FakeSilenceGate:
    """Stands in for _SilenceGate. Only .record() is ever called on it
    from within _collect_speech()."""

    def record(self, duration):
        pass


class _FakeVAD:
    """Stands in for _VADFilter. Always reports "speech" so our synthetic
    chunks behave deterministically without needing to satisfy
    webrtcvad's real fixed-frame-size requirements (10/20/30ms @ 16kHz)
    -- that constraint is orthogonal to what's being tested here."""

    def is_speech(self, chunk):
        return True


def _make_bare_stt():
    """Build a minimal SpeechToText instance for exercising the barge-in
    handoff logic in isolation, without running the real (heavy,
    hardware-dependent) __init__."""
    from sara.audio.stt.engine import SpeechToText
    from sara.audio.stt.buffers import _RingBuffer

    stt = SpeechToText.__new__(SpeechToText)
    stt._closed = False

    # The real thing -- this is what's under test.
    stt._ring = _RingBuffer(maxlen=300)

    # TTS-transition state that mark_tts_stopped()/wait_settle()/
    # capture_barge_in_audio() coordinate through.
    stt._tts_state_lock = threading.Lock()
    stt._tts_stopped_at = 0.0
    stt._barge_in_handoff = []
    stt._aec = None  # wait_settle()'s AEC-aware min_gap fallback path

    # Only needed by the _collect_speech() integration test below, but
    # cheap to always set up so every test gets the same fixture.
    stt._pre_buf = _FakePreBuffer()
    stt._silence_gate = _FakeSilenceGate()
    stt._vad = _FakeVAD()
    stt._is_listening = threading.Event()
    stt._preview_generation = 0
    stt._threshold_lock = threading.Lock()
    stt._energy_threshold = 500.0

    return stt


# One real mic chunk's worth of bytes (CHUNK_SIZE=512 samples *
# SAMPLE_WIDTH=2 bytes), matching what _ingest_processed_chunk() actually
# writes into self._ring, so _rms()/is_speech() see realistic input.
_CHUNK_BYTES = 512 * 2


def _tone(tag: bytes) -> bytes:
    """A single fake mic chunk, `tag`-repeated so different chunks/groups
    are trivially distinguishable in assertions (e.g. b"A" vs b"B")."""
    return (tag * _CHUNK_BYTES)[:_CHUNK_BYTES]


class BargeInHandoffTests(unittest.TestCase):
    """
    Covers the barge-in audio handoff added to
    sara/audio/stt/engine.py (SpeechToText.capture_barge_in_audio(),
    mark_tts_stopped(), wait_settle(), _collect_speech()) and wired up in
    sara/orchestrator/tts_worker.py (TTSWorker._watch_loop()).
    """

    def test_capture_barge_in_audio_copies_ring_without_draining_it(self):
        """capture_barge_in_audio() must COPY self._ring's contents into
        the handoff buffer, not drain it -- self._ring needs to keep
        accumulating normally for the rest of the TTS-stopping window
        (is_user_speaking() etc. still reads off it)."""
        stt = _make_bare_stt()
        chunk_a1, chunk_a2 = _tone(b"A"), _tone(b"A")
        stt._ring.put(chunk_a1)
        stt._ring.put(chunk_a2)

        stt.capture_barge_in_audio()

        self.assertEqual(stt._barge_in_handoff, [chunk_a1, chunk_a2])
        # Non-destructive: the ring itself still has both chunks.
        self.assertEqual(stt._ring.get_all(clear=False), [chunk_a1, chunk_a2])

    def test_capture_barge_in_audio_on_empty_ring_is_a_no_op(self):
        """No barge-in audio actually accumulated (e.g. is_user_speaking()
        fired on a borderline reading right as TTS finished on its own) ->
        nothing to hand off, and no crash."""
        stt = _make_bare_stt()
        stt.capture_barge_in_audio()
        self.assertEqual(stt._barge_in_handoff, [])

    def test_handoff_survives_mark_tts_stopped_and_wait_settle(self):
        """This is the core regression this whole change exists to fix:
        previously mark_tts_stopped()/wait_settle() unconditionally wiped
        self._ring right after a barge-in, throwing away the exact audio
        that triggered the interruption. Both must now leave the
        already-captured handoff buffer alone, while still clearing
        self._ring itself exactly as before."""
        stt = _make_bare_stt()
        chunk = _tone(b"A")
        stt._ring.put(chunk)
        stt.capture_barge_in_audio()
        self.assertEqual(stt._barge_in_handoff, [chunk])  # sanity check

        # mark_tts_stopped(): ring cleared, handoff buffer untouched.
        stt.mark_tts_stopped()
        self.assertEqual(stt._ring.get_all(clear=False), [])
        self.assertEqual(stt._barge_in_handoff, [chunk])

        # Some more (unrelated, e.g. echo-tail) audio trickles in before
        # the settle window elapses -- wait_settle() must clear THAT, but
        # still must not touch the handoff buffer. (wait_settle() no-ops
        # entirely while self._tts_stopped_at <= 0 -- its "no TTS-stop
        # pending" sentinel -- so give it a real past stop time, same as
        # a real post-barge-in call would see, to actually exercise the
        # clear.)
        stt._ring.put(_tone(b"echo"))
        stt._tts_stopped_at = time.monotonic() - 10.0
        stt.wait_settle(min_gap=0.01)
        self.assertEqual(stt._ring.get_all(clear=False), [])
        self.assertEqual(stt._barge_in_handoff, [chunk])

    def test_no_barge_in_path_still_fully_clears_ring(self):
        """Normal (no barge-in) wake -> listen flow: self._barge_in_handoff
        is never populated (capture_barge_in_audio() is only ever called
        from TTSWorker._watch_loop()'s barge-in branch), so
        mark_tts_stopped()/wait_settle() must behave exactly as before --
        an unconditional, full clear of self._ring."""
        stt = _make_bare_stt()
        self.assertEqual(stt._barge_in_handoff, [])  # never touched

        stt._ring.put(_tone(b"normal"))
        stt.mark_tts_stopped()
        self.assertEqual(stt._ring.get_all(clear=False), [])
        self.assertEqual(stt._barge_in_handoff, [])

        stt._ring.put(_tone(b"normal"))
        stt._tts_stopped_at = time.monotonic() - 10.0
        stt.wait_settle(min_gap=0.01)
        self.assertEqual(stt._ring.get_all(clear=False), [])
        self.assertEqual(stt._barge_in_handoff, [])

    def test_handoff_consumed_exactly_once_no_leak_into_next_capture(self):
        """End-to-end through the REAL consumer: the next _collect_speech()
        call must (a) actually receive the captured barge-in audio as the
        start of its collected speech, and (b) clear the handoff buffer
        once read, so a later, unrelated barge-in doesn't accidentally
        inherit stale audio from this one."""
        stt = _make_bare_stt()
        chunk_a1, chunk_a2 = _tone(b"A"), _tone(b"A")
        stt._ring.put(chunk_a1)
        stt._ring.put(chunk_a2)
        stt.capture_barge_in_audio()
        self.assertEqual(stt._barge_in_handoff, [chunk_a1, chunk_a2])

        # max_duration kept short so the SPEAKING state's "keep waiting
        # for more ring data" polling (self._ring.wait(timeout=0.05))
        # exits promptly once our fake VAD's always-speech reading means
        # silence-based early exit never fires -- see _FakeVAD above.
        result = stt._collect_speech(timeout=2.0, max_duration=0.15, silence_limit=0.3)

        # The handoff audio became the start (here, the entirety) of the
        # collected utterance -- exactly the point of this change.
        self.assertEqual(result, chunk_a1 + chunk_a2)
        # And it's gone -- consumed exactly once.
        self.assertEqual(stt._barge_in_handoff, [])

        # A second, unrelated barge-in must not see any leftover from the
        # first one.
        chunk_b = _tone(b"B")
        stt._ring.put(chunk_b)
        stt.capture_barge_in_audio()
        self.assertEqual(stt._barge_in_handoff, [chunk_b])


if __name__ == "__main__":
    unittest.main()