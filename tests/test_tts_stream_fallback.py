"""speak_stream failure handling: whole-reply fallback and interrupts."""
import pytest

from sara.audio.tts import cache as cache_mod
from sara.audio.tts import engine
from sara.audio.tts.text_prep import clean_for_tts
from sara.audio.voice_flags import reset_flags

try:
    from tests.tts_stream_fakes import (
        FakePlayer, Synth, drive, make_tts, stream_in_thread,
    )
except ImportError:  # tests/ is not a package here
    from tts_stream_fakes import (
        FakePlayer, Synth, drive, make_tts, stream_in_thread,
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


# ── failures -> whole-reply fallback ─────────────────────────────────────────

def test_player_exception_falls_back_to_whole_reply(synth, pieces, capsys):
    player = FakePlayer(fail_on_call=2, auto_complete=True)
    tts = make_tts(player)
    assert tts.speak_stream(iter(pieces)) is False
    whole = clean_for_tts(" ".join(pieces))
    assert synth.texts[-1] == whole
    assert len(player.whole_plays) == 1
    out = capsys.readouterr().out
    assert "falling back to whole-reply" in out
    assert "alpha" not in out
    assert tts._speaking.is_set() is False


def test_source_exception_falls_back_to_what_was_received(synth):
    def gen():
        yield "First part of the reply arrived fine."
        raise RuntimeError("llm stream broke")

    player = FakePlayer(auto_complete=True)
    tts = make_tts(player)
    assert tts.speak_stream(gen()) is False
    assert synth.texts[-1] == "First part of the reply arrived fine."
    assert len(player.whole_plays) == 1


def test_no_fallback_after_an_interrupt(synth, pieces):
    player = FakePlayer(fail_on_call=1, auto_complete=True)
    tts = make_tts(player)
    tts.stop()
    assert tts.speak_stream(iter(pieces)) is True
    assert player.whole_plays == []


def test_fallback_failure_never_raises(monkeypatch, synth, pieces):
    player = FakePlayer(fail_on_call=1, auto_complete=True)
    tts = make_tts(player)
    monkeypatch.setattr(tts, "speak", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tts down")))
    assert tts.speak_stream(iter(pieces)) is False


def test_healthy_stream_does_not_trigger_the_fallback(synth, pieces):
    player = FakePlayer()
    tts = make_tts(player)
    thread, result = stream_in_thread(tts, pieces)
    assert drive(player, thread)
    assert result["value"] is False
    assert player.whole_plays == []


def test_disabled_tts_still_drains_the_iterator(synth, pieces):
    tts = make_tts(FakePlayer())
    tts._disabled = True
    consumed = []

    def gen():
        for p in pieces:
            consumed.append(p)
            yield p

    assert tts.speak_stream(gen()) is False
    assert consumed == pieces