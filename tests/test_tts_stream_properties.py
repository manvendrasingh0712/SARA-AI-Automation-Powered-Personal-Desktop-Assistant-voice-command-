"""speak_stream properties: double buffering, bounded lookahead, interrupt."""
import time

import pytest

from sara.audio.tts import cache as cache_mod
from sara.audio.tts import engine
from sara.audio.tts.text_prep import clean_for_tts
from sara.audio.voice_flags import reset_flags, set_flag

try:
    from tests.tts_stream_fakes import (
        FakePlayer, Synth, TIMEOUT, drive, letters, make_tts, stream_in_thread,
        wait_for, wait_settled,
    )
except ImportError:  # tests/ is not a package here
    from tts_stream_fakes import (
        FakePlayer, Synth, TIMEOUT, drive, letters, make_tts, stream_in_thread,
        wait_for, wait_settled,
    )


@pytest.fixture(autouse=True)
def _clean():
    reset_flags()
    with cache_mod._phrase_cache_lock:
        cache_mod._phrase_cache.clear()
    cache_mod.unpin_all()
    yield
    reset_flags()


@pytest.fixture
def synth(monkeypatch):
    fake = Synth()
    monkeypatch.setattr(engine, "_synth_kokoro", fake)
    return fake


@pytest.fixture
def pieces():
    return [f"Sentence number {word} is here now." for word in
            ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")]


# ── double buffering ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("v2", [False, True])
def test_chunks_are_enqueued_in_order_exactly_once(synth, pieces, v2):
    set_flag("VOICE_CHUNKER_V2", v2)
    player = FakePlayer()
    tts = make_tts(player)
    thread, result = stream_in_thread(tts, pieces)
    assert drive(player, thread)
    assert result["value"] is False
    assert player.enqueued == list(range(1, len(synth.texts) + 1))
    assert player.played == player.enqueued
    assert letters(synth.texts) == letters(clean_for_tts(p) for p in pieces)
    if v2:
        assert synth.texts == [clean_for_tts(p) for p in pieces]


def test_next_chunk_is_synthesised_while_current_chunk_plays(synth, pieces):
    player = FakePlayer()
    tts = make_tts(player)
    thread, _ = stream_in_thread(tts, pieces)
    assert wait_for(lambda: len(player.enqueued) >= 1)
    # Nothing has "finished playing" yet (we never completed chunk 1), but the
    # pipeline must already have synthesised and enqueued the following chunks.
    assert wait_for(lambda: len(synth.texts) >= 3)
    assert player.played == []
    player.finish_all()
    assert drive(player, thread)
    assert len(player.played) == len(player.enqueued) >= 3


def test_lookahead_is_bounded_by_the_queues(monkeypatch, synth):
    monkeypatch.setattr(engine, "_PLAY_QUEUE_SIZE", 2)
    monkeypatch.setattr(engine, "_SYNTH_QUEUE_SIZE", 2)
    player = FakePlayer(block_enqueue=True)  # the device never accepts audio
    tts = make_tts(player)
    chunks = [f"Bounded sentence number {i} here." for i in range(30)]
    thread, _ = stream_in_thread(tts, chunks)
    time.sleep(0.6)
    assert len(synth.texts) <= engine._PLAY_QUEUE_SIZE + 3
    assert len(synth.texts) < len(chunks)
    tts.stop()
    thread.join(timeout=TIMEOUT)
    assert not thread.is_alive()


def test_on_playback_start_fires_per_chunk_in_order(synth, pieces):
    set_flag("VOICE_CHUNKER_V2", True)
    player = FakePlayer()
    tts = make_tts(player)
    started = []
    thread, _ = stream_in_thread(tts, pieces, on_playback_start=started.append)
    assert wait_for(lambda: len(player.enqueued) == len(pieces))
    assert wait_for(lambda: len(started) == len(pieces))
    assert drive(player, thread)
    assert started == [clean_for_tts(p) for p in pieces]


def test_v2_flag_streams_the_same_text_in_order(synth, pieces):
    set_flag("VOICE_CHUNKER_V2", True)
    player = FakePlayer()
    tts = make_tts(player)
    thread, _ = stream_in_thread(tts, pieces)
    assert drive(player, thread)
    assert " ".join(synth.texts).split() == " ".join(clean_for_tts(p) for p in pieces).split()


# ── interrupt ────────────────────────────────────────────────────────────────

def test_interrupt_drops_everything_and_nothing_plays_later(synth, pieces):
    player = FakePlayer(block_enqueue=True)
    tts = make_tts(player)
    thread, result = stream_in_thread(tts, pieces * 3)
    assert wait_for(lambda: len(synth.texts) >= 1)
    time.sleep(0.1)
    tts.stop()
    thread.join(timeout=TIMEOUT)
    assert not thread.is_alive()
    assert result["value"] is True
    # The segment that was being handed to the device when stop() ran must not
    # be left queued behind the clear().
    assert player.queued == []
    assert player.clear_calls >= 1
    seen = len(synth.texts)
    time.sleep(0.3)
    assert len(synth.texts) == seen
    assert player.queued == []
    assert tts.is_interrupted() is True
    assert tts._speaking.is_set() is False


def test_stale_audio_enqueued_after_stop_is_cleared(synth, pieces):
    player = FakePlayer(block_enqueue=True)
    tts = make_tts(player)
    thread, _ = stream_in_thread(tts, pieces)
    assert wait_for(lambda: len(synth.texts) >= 1)
    time.sleep(0.1)
    tts.stop()  # releases the blocked enqueue AFTER the first clear()
    thread.join(timeout=TIMEOUT)
    assert player.queued == []
    assert player.clear_calls >= 2


def test_interrupted_latch_blocks_new_streams(synth, pieces):
    player = FakePlayer()
    tts = make_tts(player)
    tts.stop()
    assert tts.speak_stream(iter(pieces)) is True
    assert synth.texts == []
    assert player.enqueued == []
    tts.clear_interrupt()
    thread, result = stream_in_thread(tts, pieces[:2])
    assert drive(player, thread)
    assert result["value"] is False
    assert len(player.enqueued) >= 1


def test_no_stale_audio_from_a_stopped_stream_reaches_the_next_one(synth, pieces):
    player = FakePlayer(block_enqueue=True)
    tts = make_tts(player)
    thread, _ = stream_in_thread(tts, pieces)
    assert wait_for(lambda: len(synth.texts) >= 1)
    tts.stop()
    # Assert the join rather than ignoring it. If the FIRST stream has not
    # unwound, the failure belongs here and should say so -- otherwise the
    # test fails further down on the second stream, which is confusing and
    # is how this one was misdiagnosed once already.
    thread.join(timeout=TIMEOUT)
    assert not thread.is_alive(), (
        "the stopped stream did not unwind within TIMEOUT; the second "
        "stream below would then compete with it for the max_workers=1 "
        "chunker/synth pools"
    )
    tts.clear_interrupt()
    player.gate.set()
    # WHY THIS WAIT EXISTS (measured, not guessed).
    #
    # This test was intermittently red: 3 of 25 runs under deliberate CPU
    # saturation, and the failing assertion was `drive(player, thread2)`
    # below -- the SECOND stream never finishing.
    #
    # Two things conspire. FakePlayer.enqueue() parks on `self.gate` BEFORE
    # appending, and player.clear() (which tts.stop() triggers) sets that
    # gate -- so the stopped stream's enqueue is released but runs on the
    # player worker, which thread.join() above does not cover. And
    # make_tts() gives the instance ThreadPoolExecutors with
    # max_workers=1, shared across both streams. So starting the second
    # stream while the first is still draining leaves it waiting for a
    # worker it cannot get, and `drive` times out.
    #
    # Waiting for the player to go quiet covers both: the stale append has
    # landed (so `before` is a real snapshot) and the first stream's single
    # worker is free. Verified: 0 of 25 failures under the same saturation.
    # Raising TIMEOUT would only have widened the window.
    wait_settled(lambda: len(player.enqueued))
    before = list(player.enqueued)
    thread2, _ = stream_in_thread(tts, ["Fresh sentence after the stop."])
    assert drive(player, thread2)
    assert len(player.enqueued) == len(before) + 1
    assert player.played == [player.enqueued[-1]]