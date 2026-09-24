"""
tests/e2e/test_voice_e2e_smoke.py
Starter E2E smoke test (doc item 18) -- proves the fake-adapter harness
shape works end-to-end for ONE scenario: a normal voice command that
resolves to a real tool via sara.core.tool_router.resolve_tool_call().

SCOPE NOTE: this is explicitly a STARTER scaffold, not the full
8-scenario E2E acceptance list from the original plan (normal command,
no-speech, barge-in, Stop-during-LLM, Stop-during-TTS, tool failure,
planner confirmation, rapid commands). Those are a follow-up once this
scaffold has been reviewed.

WIRING NOTE: the real end-to-end entry point for a voice turn lives in
sara/orchestrator/core_wiring.py's run_sara_logic(), which needs the
full CoreObjects bundle (db, reminders, tts, ears, LLM engine, etc.) --
none of which was provided for this task. So instead of faking that
entire pipeline, this test exercises the smallest REAL function we do
have from the given files: sara.core.tool_router.resolve_tool_call(),
which is the actual intent/tool-resolution step a real voice command
passes through. FakeMicrophone/FakeSTT stand in for everything upstream
of that call (audio capture + transcription); FakeTTS stands in for
whatever would speak the resulting reply. Wiring this into the real
run_sara_logic() pipeline is a follow-up once core_wiring.py and the
rest of the orchestrator files are available.

Runs fully offline: TOOL_CALLING_MODE="heuristic" on the fake config
below skips resolve_tool_call()'s LLM round-trip entirely, so no
network/model call happens anywhere in this test.
"""

import unittest

from sara.core.tool_router import resolve_tool_call

from tests.e2e.fake_adapters import FakeMicrophone, FakeSTT, FakeTTS


class _FakeConfig:
    """Minimal cfg stand-in. TOOL_CALLING_MODE="heuristic" forces the
    pure keyword-heuristic path in resolve_tool_call(), so this test
    never attempts a real LLM/network call."""

    TOOL_CALLING_MODE = "heuristic"
    DEBUG_MODE = False


class VoiceE2ESmokeTest(unittest.TestCase):
    def test_normal_command_resolves_weather_tool_and_speaks_reply(self):
        # Perceive: FakeMicrophone "hears" a scripted utterance, FakeSTT
        # "transcribes" it -- both fully in-memory, no real hardware.
        mic = FakeMicrophone(scripted_utterance="what's the weather in Jaipur")
        stt = FakeSTT()
        tts = FakeTTS()

        audio = mic.listen()
        transcript = stt.transcribe(audio)

        # Route: the REAL tool_router.resolve_tool_call() resolves the
        # transcript to a tool call, via the offline heuristic path.
        result = resolve_tool_call(
            transcript, model_name="unused-in-heuristic-mode", cfg=_FakeConfig
        )

        self.assertEqual(result["name"], "weather")
        self.assertIn("jaipur", result["arguments"]["location"].lower())

        # Act: what the real pipeline would say next gets spoken via
        # FakeTTS instead of a real TTS engine.
        reply_text = f"Here's the weather for {result['arguments']['location']}."
        tts.speak(reply_text)

        self.assertEqual(mic.listen_calls, 1)
        self.assertEqual(len(stt.transcribe_calls), 1)
        self.assertEqual(len(tts.spoken), 1)
        self.assertIn("Jaipur", tts.spoken[0]["text"])


if __name__ == "__main__":
    unittest.main()
