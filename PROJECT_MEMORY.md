# PROJECT_MEMORY.md — Sara AI

Living reference for the current state of the project. Read this first
in any new conversation about Sara AI, alongside `CHANGELOG.md` (what
happened, in order) and `NEXT_STEPS.md` (what's pending).

## What this project is

Python desktop AI assistant (JARVIS-style). Wake-word activated, bilingual
(English/Hindi), fully local-first (Ollama + faster-whisper + Kokoro
ONNX TTS), with a pywebview glassmorphic GUI. Portfolio project for
AI/ML and Data Science placements at product companies.

**Constraint that shapes every design decision here: fast, free,
low-latency.** Avoid paid APIs and heavy/slow dependencies unless
explicitly opted into (e.g. Gemini is an optional LLM backend, not the
default).

## Current state (as of this restructure)

The project just went through a **full structural refactor** — every
monolithic file (600–1700+ lines) was split into small, focused
packages, with **zero features removed or behavior changed**. This was
verified, not assumed: every package was tested with a real Python
import (hardware/API dependencies stubbed out) before being considered
done, and one real bug introduced during the split (a broken
`HTML_PATH` resolution after `sara/gui/app.py` became a package) was
caught by that testing and fixed.

### Architecture (see README.md "Architecture" section for the full tree)

```
sara/
├── audio/
│   ├── stt/          (was 1265-line stt.py)   helpers.py, buffers.py, engine.py
│   ├── tts/          (was 1057-line tts.py)   voice_params.py, text_prep.py, cache.py, synth.py, player.py, engine.py
│   └── aec.py        (unchanged — already reasonably sized)
├── core/
│   ├── llm/           (was 1080-line llm.py)  prompt.py, streaming.py, clients.py, engine.py
│   ├── intent/          (was 921-line intent.py) patterns.py (data table), engine.py (matching logic)
│   ├── memory.py          (merged — see "Duplicate PreferencesDB" below)
│   └── rag.py               (unchanged)
├── tools/
│   ├── system/         (was 1576-line system.py) 13 category files + dispatch.py (SIMPLE_ACTIONS table)
│   ├── web.py, reminders.py, clipboard.py, vision.py  (unchanged)
├── gui/
│   └── app/              (was 1354-line app.py) events.py, helpers.py, core.py, reminders.py,
│                            settings.py, notes.py, media.py, engine.py (mixin composition), bootstrap.py
└── orchestrator/         (NEW — was the bulk of 1729-line gui_main.py)
    lazy.py, state.py, ollama_manager.py, ui_bridge.py, tts_worker.py, db_writer.py,
    calc_utils.py, network_utils.py, text_utils.py, history.py, intent_handlers.py, core_wiring.py

main.py    ← renamed from gui_main.py (thin entry point only; orchestration logic moved to sara/orchestrator/)
```

Every package's `__init__.py` re-exports the same public names the
original single file exposed, so **all external call sites needed zero
changes** — `from sara.audio.tts import TextToSpeech`,
`from sara.gui.app import main as webview_main`, etc. all still work.

### Duplicate `PreferencesDB` (fixed)

`sara/core/memory.py` and `sara/tools/database.py` both defined a
near-identical `PreferencesDB` class. The `tools/database.py` version
was the one actually in use (imported by `gui_main.py`); `core/memory.py`
was a stale duplicate. Resolved by making `core/memory.py` the single
canonical version (content = the in-use version) and deleting
`tools/database.py`. One import updated in `main.py` accordingly.

### GitHub-readiness additions

- `LICENSE` (MIT) — was missing; README license badge/section said "TBD", now fixed
- `.env.example` — generated from every `os.getenv(...)` call found across
  the codebase (81 env vars), categorized and commented; previously didn't exist
- `BUILD.md`, `sara_ai.spec`, `requirements-build.txt` — README referenced
  a PyInstaller build flow that had no backing files; all three now exist
  and are documented
- `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/workflows/ci.yml` — compile + pyflakes check on `windows-latest`
  (project is Windows-only, so CI runs there, not ubuntu)

## Key architectural facts to remember

- **Stack**: Ollama (default) / Gemini (optional) for LLM, faster-whisper
  for STT, Kokoro ONNX for TTS, pywebview for GUI, WAL-mode SQLite for
  persistence, optional RAG (embeddings) for long-term memory
- **`sara.core.tool_router`** is referenced (try/except-gated, LLM
  tool-calling fallback) but does **not exist** in the codebase — this
  was true in the original project too, not something broken by the
  restructure. The app runs fine without it (feature silently disables).
- **Language**: `LanguageState` class provides thread-safe EN/HI toggle
  synced to TTS/STT, driven from the GUI
- **DB path**: single-writer, WAL-mode SQLite — do not reintroduce a
  second writer or duplicate DB class (see "Duplicate PreferencesDB" above)
- **Standing workflow rules** (from Manav): always give FULL complete
  file content for any change, never diffs; step-by-step setup
  instructions per file; keep the project fast/free/low-latency; regenerate
  this file + CHANGELOG.md + NEXT_STEPS.md after every major task or on
  "UPDATE MEMORY"

## Verification method used for the restructure (repeat this pattern for future large edits)

1. `python3 -m py_compile` on every touched file (syntax)
2. `python3 -m pyflakes .` on the whole repo (undefined names, unused imports)
3. AST-based static check that every `from .module import name` and
   `from sara.x.y import name` actually resolves to something defined in
   the target file (catches the "moved code, forgot the import" class of
   bug that compile+pyflakes alone can miss across package boundaries)
4. **Real Python import** of every new package, with third-party
   hardware/API libraries stubbed via `sys.modules` (numpy, sounddevice,
   pygame, faster_whisper, onnxruntime, kokoro_onnx, webview, psutil,
   google.genai, win32*, pycaw, mss, etc.) — this is what caught the
   `HTML_PATH` bug; static checks alone did not
