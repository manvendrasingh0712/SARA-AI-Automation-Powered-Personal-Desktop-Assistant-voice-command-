"""Shared fakes for the speak_stream property tests (no audio hardware)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from sara.audio.tts import engine

TIMEOUT = 10.0


class FakePlayer:
    """Records enqueue order; completion is controlled by the test."""

    def __init__(self, block_enqueue=False, fail_on_call=None, auto_complete=False):
        self.auto_complete = auto_complete
        self.gate = threading.Event()
        if not block_enqueue:
            self.gate.set()
        self.fail_on_call = fail_on_call
        self.enqueued = []
        self.queued = []
        self.played = []
        self.clear_calls = 0
        self.whole_plays = []
        self._lock = threading.Lock()
        self.enqueue_calls = 0

    def enqueue(self, pcm, volume, on_complete=None, on_start=None):
        with self._lock:
            self.enqueue_calls += 1
            n = self.enqueue_calls
        if self.fail_on_call == n:
            raise RuntimeError("device exploded")
        self.gate.wait(timeout=TIMEOUT)
        label = int(pcm[0])
        with self._lock:
            self.enqueued.append(label)
            self.queued.append((label, on_complete))
        if on_start is not None:
            on_start.set()
        if self.auto_complete:
            self.finish_all()

    def finish_all(self):
        with self._lock:
            items, self.queued = self.queued, []
        for label, done in items:
            self.played.append(label)
            if done is not None:
                done.set()

    def clear(self):
        with self._lock:
            self.clear_calls += 1
            self.queued = []
        self.gate.set()

    def play_and_wait(self, pcm, stop, volume):
        self.whole_plays.append(np.array(pcm))


class Synth:
    def __init__(self, delay=0.0):
        self.texts = []
        self.delay = delay
        self.lock = threading.Lock()

    def __call__(self, text, kokoro, params, lock):
        with self.lock:
            self.texts.append(text)
            label = len(self.texts)
        if self.delay:
            time.sleep(self.delay)
        return np.full(240, label, dtype=np.float32)


def make_tts(player):
    tts = engine.TextToSpeech.__new__(engine.TextToSpeech)
    tts._shutdown_lock = threading.Lock()
    tts._shutdown_done = False
    tts._stop = threading.Event()
    tts._speaking = threading.Event()
    tts._interrupted = threading.Event()
    tts._lock = threading.Lock()
    tts._synth_lock = threading.Lock()
    tts._speed_lock = threading.Lock()
    tts._speed_override = None
    tts._forced_lang = None
    tts._disabled = False
    tts._kokoro = object()
    tts._volume = 1.0
    tts._warmup_done = threading.Event()
    tts._warmup_done.set()
    tts._player = player
    tts._chunker_pool = ThreadPoolExecutor(max_workers=1)
    tts._synth_pool = ThreadPoolExecutor(max_workers=1)
    return tts


def stream_in_thread(tts, chunks, **kw):
    result = {}

    def run():
        result["value"] = tts.speak_stream(iter(chunks), **kw)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def drive(player, thread, timeout=TIMEOUT):
    """Let the fake device finish whatever is queued until the stream returns."""
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        player.finish_all()
        time.sleep(0.005)
    return not thread.is_alive()


def letters(texts):
    return "".join(t.replace(" ", "") for t in texts)


def wait_settled(measure, quiet_s=0.05, timeout=TIMEOUT):
    """
    Wait until `measure()` stops changing for `quiet_s`, then return it.

    Needed because FakePlayer.enqueue() parks on `self.gate` BEFORE it
    appends, and player.clear() (which tts.stop() triggers) sets that gate.
    So a stopped stream's enqueue is released but runs on the player worker
    rather than the stream thread -- thread.join() does not cover it, and a
    snapshot taken straight afterwards races the append.

    It also serves a second purpose that turned out to be the one that
    actually mattered: make_tts() hands out ThreadPoolExecutors with
    max_workers=1, shared across streams, so starting a new stream while a
    stopped one is still draining leaves the new one waiting for a worker
    it cannot get. Waiting for the player to go quiet means the previous
    stream's worker is free.

    Measured on test_no_stale_audio_from_a_stopped_stream_reaches_the_next_one:
    3 of 25 runs failed under deliberate CPU saturation without this wait,
    0 of 25 with it. The failing assertion was the SECOND stream never
    finishing -- not the snapshot -- which is why the first explanation
    written here was wrong. Raising TIMEOUT would only have widened the
    window; waiting for quiet is deterministic.
    """
    deadline = time.monotonic() + timeout
    last = measure()
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.005)
        now = measure()
        if now != last:
            last = now
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= quiet_s:
            return now
    return last


def wait_for(predicate, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False