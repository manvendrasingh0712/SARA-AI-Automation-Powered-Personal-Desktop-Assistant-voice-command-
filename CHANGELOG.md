# CHANGELOG.md — Sara AI

## [Unreleased] — Enterprise-level structural restructure

Full split of every monolithic file into small, focused packages, plus
GitHub-readiness additions. No feature was removed; no behavior was
intentionally changed. One real bug was introduced by the split and
caught/fixed during verification (see below).

### Restructured (no behavior change, verified via real Python import)

- `gui_main.py` (1729 lines) → renamed to `main.py` (thin entry point)
  + new `sara/orchestrator/` package (12 files: `lazy.py`, `state.py`,
  `ollama_manager.py`, `ui_bridge.py`, `tts_worker.py`, `db_writer.py`,
  `calc_utils.py`, `network_utils.py`, `text_utils.py`, `history.py`,
  `intent_handlers.py`, `core_wiring.py`)
- `sara/gui/app.py` (1354 lines) → `sara/gui/app/` package (9 files:
  `events.py`, `helpers.py`, `core.py`, `reminders.py`, `settings.py`,
  `notes.py`, `media.py`, `engine.py`, `bootstrap.py`) — the single `Api`
  class is now composed from 5 focused mixins
- `sara/tools/system.py` (1576 lines) → `sara/tools/system/` package
  (13 files, grouped by category: apps, audio_display, power,
  window_mgmt, media_keys, shortcuts, connectivity, files_notes, timers,
  folders, settings_pages, system_info, dispatch)
- `sara/audio/stt.py` (1265 lines) → `sara/audio/stt/` package
  (`helpers.py`, `buffers.py`, `engine.py`)
- `sara/core/llm.py` (1080 lines) → `sara/core/llm/` package
  (`prompt.py`, `streaming.py`, `clients.py`, `engine.py`)
- `sara/audio/tts.py` (1057 lines) → `sara/audio/tts/` package
  (`voice_params.py`, `text_prep.py`, `cache.py`, `synth.py`, `player.py`,
  `engine.py`)
- `sara/core/intent.py` (921 lines) → `sara/core/intent/` package
  (`patterns.py` — the regex data table, `engine.py` — matching logic)

Every package's `__init__.py` re-exports the exact same public names
the original single file exposed. All external call sites
(`from sara.audio.tts import TextToSpeech`, etc.) needed **zero**
changes.

### Fixed

- **Duplicate `PreferencesDB` class**: `sara/core/memory.py` and
  `sara/tools/database.py` both defined near-identical
  `PreferencesDB` classes. The `tools/database.py` version was the one
  actually imported by the app; `core/memory.py` was a stale, unused
  duplicate. Consolidated into a single canonical `sara/core/memory.py`
  (content = the in-use version); `sara/tools/database.py` deleted;
  the one import site updated.
- **Broken `HTML_PATH` resolution** (introduced by this restructure,
  caught during verification, not present in the original codebase):
  when `sara/gui/app.py` became the package `sara/gui/app/`, the new
  `bootstrap.py` lives one directory deeper than the original file did.
  Its `BASE_DIR = os.path.dirname(os.path.abspath(__file__))` therefore
  pointed at `sara/gui/app/` instead of `sara/gui/` — `index.html` (which
  did not move) would have failed to be found at runtime. Fixed by
  adding one extra `os.path.dirname(...)` level, with a comment
  explaining why.
- Missing `Dict` import in the new `sara/tools/system/shortcuts.py`
  after `_KEY_ALIASES`'s type annotation moved there from the original
  `system.py` (caught by `pyflakes`, not by `py_compile` alone, since
  `Dict` was only used in a type annotation).
- License badge/section in `README.md` said "TBD" despite an MIT license
  being the clear intent — added `LICENSE` (MIT) and fixed both references.

### Added

- `LICENSE` (MIT)
- `.env.example` — generated from every `os.getenv(...)` call found
  across the entire codebase (81 environment variables), grouped by
  category (LLM backend, STT, TTS, wake word, AEC/barge-in, RAG, tool-calling,
  vision, assistant identity, storage, performance, debugging) with comments
- `BUILD.md` — full PyInstaller packaging guide (previously referenced
  by `README.md` but did not exist)
- `sara_ai.spec` — PyInstaller spec (previously referenced but missing)
- `requirements-build.txt` — build-only deps (`pyinstaller`, `pywin32`),
  separate from runtime `requirements.txt` (previously referenced but missing)
- `CONTRIBUTING.md`
- `.github/ISSUE_TEMPLATE/bug_report.md`, `.github/ISSUE_TEMPLATE/feature_request.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/workflows/ci.yml` — compile + pyflakes check on every push/PR
  (runs on `windows-latest` since the project's dependencies, e.g.
  `pywin32`/`pycaw`/`comtypes`, are Windows-only)
- `PROJECT_MEMORY.md`, this `CHANGELOG.md`, `NEXT_STEPS.md`

### Updated

- `README.md` — "Architecture" section rewritten to reflect the new
  package structure; run instructions changed from `python gui_main.py`
  to `python main.py`; license badge/section fixed from "TBD" to MIT

---

## Verification performed for this restructure

1. `py_compile` on every file (syntax correctness)
2. `pyflakes` on the whole repository (undefined names, unused imports) —
   zero real issues after fixes (two pre-existing lint warnings in
   `gui_main.py`/`llm.py`, unrelated to the restructure, were confirmed
   present in the original code and left as-is)
3. AST-based static check that every relative and absolute
   `sara.*` import resolves to a name actually defined in its target file
4. **Real Python import** of `main.py` and every new package with
   third-party hardware/API libraries stubbed via `sys.modules`
   (numpy, sounddevice, pygame, faster_whisper, onnxruntime, kokoro_onnx,
   webview, psutil, google.genai, win32*, pycaw, mss, etc.) — this is
   what caught the `HTML_PATH` bug above; static checks alone did not
