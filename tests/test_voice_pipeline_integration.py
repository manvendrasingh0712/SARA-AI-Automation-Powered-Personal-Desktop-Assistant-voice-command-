"""Integration coverage for voice-session coordination and cleanup.

The orchestration test runs the real wake/session loop and real intent
dispatcher. Its scripted ears and TTS adapter stand in only for microphone,
STT model, and speaker boundaries; command routing is not mocked.
"""
from __future__ import annotations

import threading
import time

from config import Config
from sara.audio.echo_guard import get_default_guard
from sara.audio.stt.engine import SpeechToText
from sara.orchestrator import core_wiring
from sara.orchestrator.state import VOICE_SESSION_CANCEL
from sara.orchestrator.tts_worker import TTSWorker


class _BlockingVoice:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.spoken = []
        self.shutdown_calls = 0

    def speak(self, text, fast=False):
        self.spoken.append(text)
        self.started.set()
        self.release.wait(2.0)

    def stop(self):
        self.release.set()

    def shutdown(self):
        self.shutdown_calls += 1
        self.release.set()

    def set_language(self, _lang):
        pass

    def is_speaking(self):
        return self.started.is_set() and not self.release.is_set()


class _Ears:
    def __init__(self):
        self.active = False

    def set_tts_active(self, value):
        self.active = bool(value)

    def mark_tts_stopped(self):
        pass

    def is_user_speaking(self, duration=0.3):
        return False


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def test_tts_worker_shutdown_unblocks_queued_speech_and_rejects_late_calls(monkeypatch):
    monkeypatch.setattr(Config, "BARGE_IN_ENABLED", False, raising=False)
    guard = get_default_guard()
    guard.reset()
    voice = _BlockingVoice()
    worker = TTSWorker(voice, _Ears())
    first = threading.Thread(target=worker.speak, args=("First reply",), kwargs={"block": True})
    queued = threading.Thread(target=worker.speak, args=("Queued reply",), kwargs={"block": True})
    first.start()
    assert voice.started.wait(1.0)
    queued.start()
    assert _wait_for(lambda: worker._q.qsize() == 1)

    worker.shutdown()
    first.join(timeout=1.0)
    queued.join(timeout=1.0)
    assert not first.is_alive() and not queued.is_alive()
    assert not worker._thread.is_alive() and not worker._watch_thread.is_alive()
    assert all(entry.ended_at is not None for entry in guard._history)

    started = time.monotonic()
    worker.speak("After shutdown", block=True)
    assert time.monotonic() - started < 0.1
    assert "After shutdown" not in voice.spoken
    worker.shutdown()
    assert voice.shutdown_calls == 1


def test_stt_capture_cancellation_returns_without_transcribing(monkeypatch):
    class _Ring:
        def get_all(self, clear=False):
            return []

        def wait(self, timeout=None):
            cancel.wait(timeout)

    monkeypatch.setattr(Config, "VOICE_ENDPOINT_ADAPTIVE", False, raising=False)
    stt = SpeechToText.__new__(SpeechToText)
    stt._closed = False
    stt._threshold_lock = threading.Lock()
    stt._energy_threshold = 100.0
    stt._ring = _Ring()
    stt._tts_state_lock = threading.Lock()
    stt._barge_in_handoff = []
    stt._is_listening = threading.Event()
    stt._preview_generation = 0
    cancel = threading.Event()
    result = {}

    capture = threading.Thread(
        target=lambda: result.setdefault(
            "audio",
            SpeechToText._collect_speech(
                stt,
                timeout=10.0,
                max_duration=12.0,
                silence_limit=0.8,
                cancel_event=cancel,
            ),
        )
    )
    capture.start()
    assert _wait_for(stt._is_listening.is_set)
    cancel.set()
    capture.join(timeout=0.5)
    assert not capture.is_alive()
    assert result["audio"] == b""
    assert not stt._is_listening.is_set()


def test_closed_stt_discards_an_inflight_partial_preview():
    started = threading.Event()
    release = threading.Event()
    partials = []

    class _Gate:
        silence_limit = 0.8

    stt = SpeechToText.__new__(SpeechToText)
    stt._wake_whisper_model = object()
    stt._preview_generation = 4
    stt._preview_busy = threading.Event()
    stt._closed = False
    stt._silence_gate = _Gate()
    stt._recalib_event = threading.Event()
    stt._watchdog_event = threading.Event()
    stt._worker_threads = []
    stt._stream = None
    stt._pa = None

    def transcribe(*_args, **_kwargs):
        started.set()
        release.wait(2.0)
        return "stale preview"

    stt._transcribe = transcribe
    SpeechToText._spawn_preview_transcribe(stt, b"audio", partials.append, 4)
    assert started.wait(1.0)
    SpeechToText.close(stt)
    release.set()
    assert _wait_for(lambda: not stt._preview_busy.is_set())
    assert partials == []


class _FakeProactive:
    def __init__(self, **_kwargs):
        self.stopped = False

    def start(self):
        pass

    def shutdown(self):
        self.stopped = True


class _FakeSupervisor:
    def __init__(self, **_kwargs):
        self.stopped = False

    def register(self, *_args, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        self.stopped = True


class _ScriptedEars:
    _aec = None

    def __init__(self, stop_event, *, fail=False):
        self.stop_event = stop_event
        self.fail = fail
        self.calls = 0
        self.closed = False
        self.cancel_event = None

    def is_wake_word_detected(self):
        return False

    def wait_settle(self, **_kwargs):
        pass

    def listen(self, *, mode, on_partial_transcript, cancel_event):
        assert mode == "command"
        self.cancel_event = cancel_event
        self.calls += 1
        if self.fail:
            raise RuntimeError("scripted capture failure")
        if self.calls == 1:
            on_partial_transcript("what time is it")
            return "what time is it"
        self.stop_event.set()
        return ""

    def get_detected_language(self):
        return "en"

    def close(self):
        self.closed = True


class _Brain:
    def wait_until_warm(self, timeout):
        return True

    def set_turn_language(self, _language):
        pass

    def get_language(self):
        return "english"

    def used_fallback_last_turn(self):
        return False

    def record_exchange(self, _user_input, _reply):
        pass


class _TTS:
    def __init__(self):
        self.spoken = []
        self.closed = False

    def speak(self, text, **_kwargs):
        self.spoken.append(text)

    def set_language(self, _lang):
        pass

    def clear_interrupt(self):
        pass

    def shutdown(self):
        self.closed = True


class _DB:
    def record_interaction_day(self):
        pass

    def get_preference(self, _key, default=None):
        return default


class _DBWriter:
    def __init__(self):
        self.messages = []
        self.closed = False

    def log_message(self, *message):
        self.messages.append(message)

    def shutdown(self):
        self.closed = True


class _Reminders:
    def shutdown(self):
        pass


def _run_orchestration(monkeypatch, *, fail=False):
    from sara.core import gc_tuning

    monkeypatch.setattr(core_wiring, "_wait_for_model", lambda *_args: True)
    monkeypatch.setattr(core_wiring, "ProactiveEngine", _FakeProactive)
    monkeypatch.setattr(core_wiring, "ThreadSupervisor", _FakeSupervisor)
    monkeypatch.setattr(core_wiring, "start_memory_consolidation", lambda *_args: None)
    monkeypatch.setattr(gc_tuning, "install_histogram", lambda: None)
    monkeypatch.setattr(gc_tuning, "start_reporter", lambda *_args: None)
    monkeypatch.setattr(core_wiring, "_shutdown_network_executor", lambda: None)
    for name, value in (
        ("GC_TUNING_ENABLED", False),
        ("POWER_THROTTLING_OPT_OUT", False),
        ("KEEP_WARM_INTERVAL_S", 0.0),
        ("APP_INDEX_ENABLED", False),
        ("NEWS_WARMER_ENABLED", False),
        ("STARTUP_WARM_WAIT_S", 0.0),
        ("BARGE_IN_ENABLED", False),
    ):
        monkeypatch.setattr(Config, name, value, raising=False)

    VOICE_SESSION_CANCEL.clear()
    stop_event = threading.Event()
    manual_wake = threading.Event()
    manual_wake.set()
    ears = _ScriptedEars(stop_event, fail=fail)
    tts = _TTS()
    writer = _DBWriter()
    updates = []
    watcher = {}
    original_watcher = core_wiring._WakeWatcher

    class _TrackingWatcher(original_watcher):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            watcher["value"] = self

    monkeypatch.setattr(core_wiring, "_WakeWatcher", _TrackingWatcher)
    core_wiring.run_sara_logic(
        lambda *event: updates.append(event),
        stop_event,
        _Brain(),
        tts,
        ears,
        _DB(),
        None,
        _Reminders(),
        writer,
        None,
        manual_wake_event=manual_wake,
    )
    return ears, tts, writer, updates, watcher["value"]


def test_real_voice_orchestration_routes_partial_and_final_transcript(monkeypatch):
    ears, tts, writer, updates, watcher = _run_orchestration(monkeypatch)
    try:
        kinds = [item[0] for item in updates]
        partial = kinds.index("transcript_partial")
        final_user = next(
            i for i, item in enumerate(updates)
            if item[0] == "transcript" and item[1] == "user"
        )
        assert partial < final_user
        assert writer.messages == [
            ("user", "what time is it"),
            ("assistant", next(text for text in tts.spoken if text.startswith("It's "))),
        ]
        assert sum(text.startswith("It's ") for text in tts.spoken) == 1
        assert ears.closed and tts.closed and writer.closed
        assert not watcher._thread.is_alive()
    finally:
        VOICE_SESSION_CANCEL.clear()


def test_orchestration_exception_still_closes_microphone_and_wake_worker(monkeypatch):
    ears, tts, writer, _updates, watcher = _run_orchestration(monkeypatch, fail=True)
    try:
        assert ears.closed and tts.closed and writer.closed
        assert not watcher._thread.is_alive()
    finally:
        VOICE_SESSION_CANCEL.clear()
