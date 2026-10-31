# Proposed split of the giant files and functions (audit Task 6.2 / 6.3) — NOT implemented, needs owner approval

Measured on this snapshot (lines): `sara/audio/stt/engine.py` 3317, `sara/core/llm/engine.py` 2201, `config.py` 2073.
Functions: `config.validate` 675, `dispatcher._handle_command` 630, `core_wiring.run_sara_logic` 602,
`planning/executor.execute_plan` 423, `stt/engine._collect_speech` 336, `llm/engine.generate_response_stream` 291,
`llm/engine._stream_generic` 271.

## Why nothing was split in this pass
Each of these functions carries many fixes that exist only as comments and tests ("D52", "E2E 2026-10-30" ...). A split without a
characterization test per branch risks undoing them silently, and several tests read these functions' *source* (for example
`tests/test_exit_words_are_whole_utterances.py` inspects `_handle_command`; `tests/test_negated_action_remainder.py` walks the AST of
`detect_intent`). A split therefore has to move those tests with it, one function per commit.

## Files
| Today | Proposed modules | Notes |
|---|---|---|
| `stt/engine.py` | `stt/wake.py` (wake word + wake Whisper), `stt/vad.py` (WebRTC / endpointing), `stt/transcribe.py` (`_transcribe`, Hinglish-first), `stt/barge_in.py`, `stt/engine.py` (the `SpeechToText` facade) | `SpeechToText` keeps every public method; the new modules take it as an argument or are mixins. |
| `llm/engine.py` | `llm/history.py` (trim window, anchor), `llm/providers.py` (`_open_ollama_stream`, `_open_gemini_stream`), `llm/gloss.py` (Hinglish gloss), `llm/streaming_loop.py` (`_stream_generic`), `llm/engine.py` (`SaraLLM`) | `prompt.py`, `streaming.py`, `clients.py` already exist. |
| `config.py` | `config/` package: `base.py` (`_int/_float/_bool`), `llm.py`, `audio.py`, `gui.py`, `security.py`, `validate.py`; `from config import Config` stays | `validate()` becomes a table of `(attr, min, max)` rules plus a few custom checks. |

## Functions
1. `config.validate` -> table-driven clamps (`CLAMPS = {name: (lo, hi)}`) + `_validate_<section>()` helpers. Safest first step: ~600 of the 675 lines are `x = max(lo, min(hi, x))`.
2. `_stream_generic` -> `_run_stage()` (one backend, retries) + `_emit_chunks()` (buffering / first-chunk cut) + `_finish()` (history append).
3. `generate_response_stream` -> `_prepare_turn()` (history, RAG, gloss), `_choose_stages()` (router, cascade, fallback), the streaming call.
4. `_handle_command` -> `_session_controls()`, `_pending_confirmation()`, `_route_intent()`, `_chat_fallback()`; keep the name and signature.
5. `run_sara_logic` -> `_wake_phase()`, `_listen_phase()`, `_dispatch_phase()`, `_after_reply_phase()`.
6. `execute_plan` and `_collect_speech`: split last; they are the most timing-sensitive.

Order: 1, 2, 3 (pure helpers, easiest to characterize), then 4-6 once a golden-turn test exists for each.
