# NEXT_STEPS.md — Sara AI

## Immediately after receiving this package

1. **Replace the two temporary scaffolding scripts** — none remain in
   the final zip (`_splitter.py`, `_split_helper.py`, `_extract_app.py`
   and their intermediate outputs were all deleted before packaging),
   but if you re-run any part of this restructure manually, don't
   commit those helper scripts.
2. **Extract the zip over a fresh `git init`** (or into a new empty
   repo folder) rather than on top of your old repo, so the file
   deletions (`gui_main.py`, `sara/gui/app.py`, `sara/tools/system.py`,
   `sara/audio/stt.py`, `sara/core/llm.py`, `sara/audio/tts.py`,
   `sara/core/intent.py`, `sara/tools/database.py`) are captured
   correctly in your first commit, rather than leaving orphaned old
   files sitting next to the new packages.
3. Update the GitHub URL placeholder in `README.md`
   (`git clone https://github.com/<your-username>/sara-ai.git`) and in
   `CONTRIBUTING.md` with your actual repo URL before publishing.
4. Run `python main.py` on your actual Windows machine (with real mic/
   models/Ollama) to confirm end-to-end behavior — everything here was
   verified via real Python imports with hardware/API libraries stubbed
   out (see PROJECT_MEMORY.md "Verification method"), which is strong
   evidence nothing is structurally broken, but it is **not** a
   substitute for one real run with actual hardware.

## Pending from the original roadmap (unchanged by this restructure)

From `README.md`'s Roadmap section:
- [ ] Custom-trained wake-word model (currently STT-fallback based)
- [ ] Cross-platform support (currently Windows-only)
- [ ] Proactive suggestions (calendar-aware, battery/system-state-aware)

## Open items from this restructuring session

- `sara.core.tool_router` is imported (try/except-gated) but does not
  exist anywhere in the codebase — this was already true before the
  restructure. If the LLM tool-calling fallback feature was intended to
  exist, it needs to be written; otherwise, consider removing the dead
  import + its `except` handler from `sara/orchestrator/core_wiring.py`
  to reduce noise (currently prints a startup warning every launch).
- `sara_ai.spec` has an icon line commented out
  (`# icon="assets/icon.ico"`) — add an `.ico` file under `assets/` and
  uncomment it if you want the built `.exe` to have a custom icon.
- CI (`.github/workflows/ci.yml`) currently only does compile + lint
  checks, not a real functional test (not feasible without audio
  hardware/GPU/Ollama in a CI runner). If you want deeper CI coverage
  later, consider adding unit tests for the pure-logic pieces that
  don't need hardware — `sara/orchestrator/calc_utils.py`
  (`_safe_calc`, `_parse_duration_to_seconds`), `sara/core/intent/`
  (`detect_intent` against known phrases), `sara/orchestrator/text_utils.py`
  are good first candidates since they're already isolated, pure
  functions after this restructure.
- `pyflakes` config note: two pre-existing warnings
  (`gui_main.py`/now `main.py`'s unused `global _ollama_process`,
  `llm.py`'s unused `last_exc` in two retry-loop branches) were
  confirmed present in the original code and intentionally left as-is
  in this restructure (out of scope — behavior-preserving refactor
  only). Worth a small follow-up cleanup pass if you want a fully clean
  lint report.

## Structural conventions to follow going forward

Documented in `CONTRIBUTING.md`, repeated here for quick reference: new
code should slot into the existing package split
(`sara/audio/{stt,tts}/`, `sara/core/{llm,intent}/`, `sara/tools/system/`,
`sara/gui/app/`, `sara/orchestrator/`) rather than growing any single
file back toward the 600+ line range that prompted this restructure in
the first place. If a new file starts approaching ~300–400 lines and
covers more than one clear concern, that's the signal to split it
before it becomes another monolith.
