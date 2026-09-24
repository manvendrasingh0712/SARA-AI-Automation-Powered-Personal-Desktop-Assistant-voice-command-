"""
tests/e2e/fake_adapters.py
Minimal fake stand-ins for the microphone / STT / TTS hardware boundary,
for use in a voice-loop E2E smoke test that must run WITHOUT real audio
hardware, a real STT engine, or network access.

INTERFACE-SHAPE NOTE (please verify against the real classes):
None of the files given for this task (proactive.py, tool_router.py,
planner.py, config.py, clients.py) contain the real Microphone or STT
class definitions, so the exact real method names/signatures for
"listen for an utterance" and "transcribe audio" are NOT confirmed here
-- FakeMicrophone.listen() and FakeSTT.transcribe() below are a
reasonable, clearly-flagged GUESS at the real shape, not a verified
interface. Please check them against the real classes (likely
somewhere under sara/orchestrator/ or a dedicated sara/core/speech.py-
style module, none of which was provided for this task) and adjust the
method names here if they differ.

The ONE real, CONFIRMED interface used below is `tts.speak(text,
fast=...)`, taken directly from sara/orchestrator/proactive.py's
`self._tts.speak(text, fast=True)` call.
"""

from __future__ import annotations

from typing import Dict, List, Optional


class FakeMicrophone:
    """
    Stand-in for the real microphone/audio-capture component. Instead of
    capturing real audio, it is pre-loaded with a single scripted
    "utterance" that `listen()` returns immediately, so tests never
    touch real hardware.

    GUESSED INTERFACE: `listen()` returning an opaque "audio" payload
    (here just the plain text the test wants STT to eventually produce)
    mirrors the general shape of a capture step that feeds an STT
    engine. NOT confirmed against a real Microphone/"ears" class -- none
    was in the files given for this task.
    """

    def __init__(self, scripted_utterance: str = "") -> None:
        self._scripted_utterance = scripted_utterance
        self.listen_calls = 0

    def listen(self) -> str:
        """Returns the pre-scripted "audio" instead of recording real audio."""
        self.listen_calls += 1
        return self._scripted_utterance


class FakeSTT:
    """
    Stand-in for the real speech-to-text engine. Returns a pre-scripted
    transcript string instead of running any real STT model.

    GUESSED INTERFACE: `transcribe(audio) -> str`. NOT confirmed against
    a real STT class -- none was in the files given for this task.
    """

    def __init__(self, scripted_transcript: Optional[str] = None) -> None:
        self._scripted_transcript = scripted_transcript
        self.transcribe_calls: List[str] = []

    def transcribe(self, audio: str) -> str:
        self.transcribe_calls.append(audio)
        # If no override transcript was scripted, echo the "audio" straight
        # through -- FakeMicrophone already hands us plain text, not real
        # audio bytes, so there's nothing to actually transcribe here.
        if self._scripted_transcript is not None:
            return self._scripted_transcript
        return audio


class FakeTTS:
    """
    Stand-in for the real TTS engine. Records every `speak()` call
    instead of producing real audio, so a test can assert on what would
    have been spoken.

    CONFIRMED INTERFACE: `speak(text, fast=False)` -- taken directly from
    sara/orchestrator/proactive.py's `self._tts.speak(text, fast=True)`
    call, so this one method signature IS verified against real usage in
    the given files.
    """

    def __init__(self) -> None:
        self.spoken: List[Dict[str, object]] = []

    def speak(self, text: str, fast: bool = False) -> None:
        self.spoken.append({"text": text, "fast": fast})
