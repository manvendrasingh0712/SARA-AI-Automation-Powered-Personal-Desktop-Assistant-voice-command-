"""
Self-Echo Guard (sara/audio/echo_guard.py) — transcript-level fallback for
TTS self-hearing, on top of sara/audio/aec.py's real-time AEC. Covers what
the task actually asked to protect: SARA's own recent speech (including a
delayed tail) is rejected, while genuine user speech — even speech similar
to what SARA just said, or spoken right after TTS ends — is kept.
"""
import time

from sara.audio.echo_guard import SelfEchoGuard


def _guard():
    return SelfEchoGuard()  # fresh instance per test, not the process-wide singleton


def test_self_echo_is_rejected_right_after_playback():
    g = _guard()
    now = time.monotonic()
    g.record("The weather in Mumbai is twenty eight degrees and clear", now - 1.0, now)
    assert g.is_echo("the weather in mumbai is twenty eight degrees and clear")


def test_delayed_echo_tail_is_rejected():
    """Only the tail of what SARA said gets transcribed a moment after playback stopped."""
    g = _guard()
    now = time.monotonic()
    g.record("Sure, I have added milk and eggs to your shopping list", now - 2.0, now - 0.3)
    assert g.is_echo("eggs to your shopping list")  # substring of the recent phrase, within the AEC tail window


def test_genuine_user_speech_during_tts_is_kept():
    g = _guard()
    now = time.monotonic()
    g.record("Checking the weather for you now, one moment please", now - 0.5, now)
    assert not g.is_echo("stop talking")
    assert not g.is_echo("what time is it")


def test_genuine_speech_right_after_tts_is_kept():
    g = _guard()
    now = time.monotonic()
    g.record("Done, the alarm is set for seven a m", now - 1.0, now)
    assert not g.is_echo("thank you")
    assert not g.is_echo("can you also play some music")


def test_similar_but_independently_spoken_sentence_is_kept():
    """A real user command that happens to share a few words with a recent TTS
    phrase must not be discarded on weak partial overlap (forbidden: exact-text-
    only suppression is wrong in the other direction too — fuzzy must still
    require real similarity, not just 'shares some words')."""
    g = _guard()
    now = time.monotonic()
    g.record("I have set a reminder for tomorrow at five p m", now - 1.0, now)
    assert not g.is_echo("remind me to call the doctor tomorrow")


def test_genuine_speech_well_after_the_tail_window_is_kept():
    g = _guard()
    now = time.monotonic()
    g.record("Playing your focus playlist now", now - 5.0, now - 4.0)
    assert not g.is_echo("playing your focus playlist now")  # same words, but long after the tail window closed


def test_short_phrases_need_a_near_exact_match():
    """Short recent TTS text ('Yes?', 'Okay.') must not fuzzy-match an unrelated
    short reply just because both are short — only a close/exact match counts."""
    g = _guard()
    now = time.monotonic()
    g.record("Okay.", now - 0.2, now)
    assert not g.is_echo("No.")
    assert g.is_echo("okay")


def test_confirmation_pending_case_is_the_caller_responsibility():
    """The guard itself is content-only; core_wiring.py's call site is what
    skips the check while a confirmation is pending (mirrors _is_ack_echo's
    own confirm_pending bypass) -- verified here at the guard level: even a
    perfect textual match is still reported as an echo, so the caller must
    gate it, which sara/orchestrator/core_wiring.py already does."""
    g = _guard()
    now = time.monotonic()
    g.record("Do you want me to delete it", now - 0.5, now)
    assert g.is_echo("do you want me to delete it")


def test_streaming_style_multiple_chunks_recorded_as_one_phrase():
    """speak_stream() records the whole streamed reply once it finishes (see
    sara/audio/tts/engine.py) -- simulate that here with several record()
    calls (as cancellation/retries could produce) and confirm each is
    independently matchable."""
    g = _guard()
    now = time.monotonic()
    g.record("First the oven preheats to three fifty", now - 3.0, now - 2.0)
    g.record("then bake for twenty minutes", now - 1.0, now)
    assert g.is_echo("then bake for twenty minutes")
    assert not g.is_echo("call mom")


def test_cancelled_playback_still_closes_its_history_entry():
    """If TTS is interrupted mid-sentence, record() is still called with the
    real (short) start/end — it must not stay 'open' forever and keep
    matching long after the fact."""
    g = _guard()
    now = time.monotonic()
    g.record("Turning off the li", now - 10.0, now - 9.5)  # cut short by Stop, 9.5s ago
    assert not g.is_echo("turning off the li")


def test_fallback_without_aec_uses_a_longer_tail_but_still_expires():
    g = _guard()
    now = time.monotonic()
    g.record("Setting a timer for ten minutes", now - 1.5, now - 1.5)
    assert g.is_echo("setting a timer for ten minutes", aec_enabled=False)
    g2 = _guard()
    g2.record("Setting a timer for ten minutes", now - 3.0, now - 3.0)
    assert not g2.is_echo("setting a timer for ten minutes", aec_enabled=False)  # past even the no-AEC tail


def test_empty_and_whitespace_are_never_flagged():
    g = _guard()
    now = time.monotonic()
    g.record("Hello there", now - 0.1, now)
    assert not g.is_echo("")
    assert not g.is_echo("   ")


def test_non_blocking_ack_race_is_still_caught():
    """tts_worker.py's speak(..., block=False) calls record_start() BEFORE the
    job is queued/played -- so even if the orchestrator calls is_echo() before
    record_end() ever runs, the (still-open) entry is caught."""
    g = _guard()
    g.record_start("Yes?")
    assert g.is_echo("yes")  # echo of the open (not-yet-finished) ack
    assert not g.is_echo("open chrome")


def test_record_end_closes_the_open_entry_and_tail_then_applies():
    g = _guard()
    now = time.monotonic()
    g.record_start("Got it, done", started_at=now - 0.4)
    g.record_end("Got it, done", ended_at=now)
    assert g.is_echo("got it done")
    g2 = _guard()
    g2.record_start("Got it, done", started_at=now - 3.0)
    g2.record_end("Got it, done", ended_at=now - 3.0)
    assert not g2.is_echo("got it done")  # closed long enough ago, past the tail


def test_superseded_job_still_closes_its_open_entry():
    """A job cancelled before it ever played (epoch bump / Stop) must not
    leave a permanently-open, always-matching entry behind."""
    g = _guard()
    now = time.monotonic()
    g.record_start("This never actually played", started_at=now - 5.0)
    g.record_end("This never actually played", ended_at=now - 5.0)
    assert not g.is_echo("this never actually played", aec_enabled=True)


def test_record_end_reports_whether_it_closed_something():
    g = _guard()
    assert g.record_end("nothing was ever opened") is False
    g.record_start("Hello")
    assert g.record_end("Hello") is True
    assert g.record_end("Hello") is False  # already closed -- no open entry left to match


def test_worker_then_engine_dedup_pattern_creates_only_one_entry():
    """Mirrors the actual call sequence: tts_worker.py's speak() opens the
    entry before queuing (record_start); engine.py's speak() closes that
    SAME entry on success instead of adding a second one (record_end first,
    record() only as a fallback when nothing was open) -- must never end up
    with two entries for one playback."""
    g = _guard()
    g.record_start("Setting a timer for five minutes")
    if not g.record_end("Setting a timer for five minutes"):
        g.record("Setting a timer for five minutes", time.monotonic(), time.monotonic())
    assert len(g._history) == 1
    assert g.is_echo("setting a timer for five minutes")


def test_direct_engine_only_caller_still_gets_recorded():
    """A caller that never went through tts_worker.py's record_start() (no
    open entry exists) must still fall back to record() and be recorded."""
    g = _guard()
    if not g.record_end("Direct call, no worker"):
        g.record("Direct call, no worker", time.monotonic() - 0.2, time.monotonic())
    assert len(g._history) == 1
    assert g.is_echo("direct call no worker")


def test_word_order_jitter_is_still_caught():
    """STT can transpose/garble word order on a genuine echo; the word-set
    (Jaccard) signal catches this even when the char-ratio alone would not."""
    g = _guard()
    now = time.monotonic()
    g.record("please remind me to call the dentist tomorrow afternoon", now - 1.0, now)
    assert g.is_echo("remind me please call dentist the tomorrow afternoon to")


def test_suffix_match_catches_a_garbled_lead_in_on_the_tail():
    """Only the tail of what SARA said is caught, with a little STT noise
    before it -- not a clean substring, but the trailing words line up."""
    g = _guard()
    now = time.monotonic()
    g.record("okay i have added eggs milk and bread to your shopping list", now - 1.5, now - 0.2)
    assert g.is_echo("uh eggs milk and bread to your shopping list")


def test_low_confidence_lowers_the_bar_for_an_already_similar_transcript():
    g = _guard()
    now = time.monotonic()
    g.record("the meeting with the design team starts at three thirty", now - 1.0, now)
    borderline = "the meeting with design starts three"  # scores ~0.79: below 0.82, above 0.72
    assert not g.is_echo(borderline)  # confident (no confidence passed = normal floor): kept
    assert g.is_echo(borderline, confidence=0.1)  # STT itself unsure: lower floor catches it


def test_high_confidence_never_lowers_protection_for_genuine_speech():
    g = _guard()
    now = time.monotonic()
    g.record("the meeting with the design team starts at three thirty", now - 1.0, now)
    assert not g.is_echo("call my brother about the car", confidence=1.0)
    assert not g.is_echo("call my brother about the car", confidence=0.0)  # unrelated text: confidence can't invent a match


def test_config_overrides_are_honoured():
    g = SelfEchoGuard(sim_threshold=0.5, tail_with_aec_s=5.0)
    now = time.monotonic()
    g.record("turn off the kitchen lights please", now - 4.0, now - 4.0)
    assert g.is_echo("turn off kitchen lights", aec_enabled=True)  # loose threshold + long tail


def test_best_match_reports_a_score_and_none_when_empty():
    g = _guard()
    assert g.best_match("anything") is None
    now = time.monotonic()
    g.record("Hello there, how can I help", now - 0.2, now)
    score = g.best_match("hello there how can i help")
    assert score is not None and score > 0.9


def test_history_is_time_bounded_not_unbounded():
    g = _guard()
    t0 = time.monotonic() - 100.0
    for i in range(50):
        g.record(f"old phrase number {i}", t0 + i * 0.01, t0 + i * 0.01)
    assert len(g._history) <= 12  # _HISTORY_MAX_ENTRIES / age pruning, whichever bites first
