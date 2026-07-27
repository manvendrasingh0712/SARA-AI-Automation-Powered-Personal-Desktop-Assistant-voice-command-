# CHANGELOG.md — Sara AI

## [Unreleased] — Feature merge: Proactive Engine, Skills system, Setup Wizard, Explainable AI + regression fixes + cleanup

This entry describes the CURRENT actual state of the codebase after
merging a "final" feature-update zip into the audited base project
(the restructure described in the entry below), plus fixing the
regressions that merge introduced. Source of truth: `CHANGES_SUMMARY.md`.

> **Correction to the entry below:** the previous restructure entry
> lists `LICENSE`, `.env.example`, `BUILD.md`, `sara_ai.spec`,
> `requirements-build.txt`, `CONTRIBUTING.md`, GitHub issue templates,
> a PR template, and `.github/workflows/ci.yml` as "Added." That entry
> is left unedited below as a historical record of what was worked on
> in that session, but as of the current repo state confirmed here:
> **`.github/workflows/ci.yml` and `sara_ai.spec` have been proposed
> multiple times but were never actually committed to the repo — do not
> treat them as existing.** **`.env.example` is intentionally not part
> of the current repo state** (the user maintains their own `.env`
> directly). Treat `PROJECT_MEMORY.md` and `NEXT_STEPS.md` as
> authoritative on this point going forward.

### Added

- **Proactive Engine** (`sara/orchestrator/proactive.py`) — background
  daemon thread (default 60s check interval) that lets Sara speak up
  without a wake word for: low battery, an upcoming-reminder heads-up,
  an idle/break nudge, and streak-milestone announcements. Per-trigger
  cooldown, silenceable via Focus Mode / Pause Listening / config,
  optional LLM rephrasing with template fallback. All nudges logged to
  a new `proactive_log` SQLite table.
- **Explainable AI** — new `why_proactive` intent (Hinglish-aware:
  "kyu bola", "kyu kaha") with handler `_h_why_proactive`, so the user
  can ask Sara why she spoke and get the logged reason back.
- **Skills plugin system** (`sara/skills/`) — auto-discovery package;
  any file defining `INTENT_NAME` + `PATTERNS` + `handle()` is wired up
  automatically at startup, no manual registration needed. Shipped
  skills: `daily_briefing.py`, `joke.py`, `notes_qa.py` (RAG Q&A over
  notes), `streak.py` (daily talk-streak tracking, milestones at
  3/7/14/30/50/100/200/365 days).
- **Setup Wizard** (`sara/gui/app/setup_wizard.py`,
  `ApiSetupWizardMixin`) — first-run onboarding that probes Ollama, the
  LLM model, the embedding model, and Kokoro TTS files, with one-click
  fixes. 4 new pywebview API methods added.
- New config keys: `PROACTIVE_ENABLED`, `PROACTIVE_CHECK_INTERVAL_S`,
  `PROACTIVE_BATTERY_LOW_PERCENT`, `PROACTIVE_REMINDER_LEAD_MINUTES`,
  `PROACTIVE_IDLE_BREAK_MINUTES`, `PROACTIVE_COOLDOWN_MINUTES`,
  `PROACTIVE_LLM_PHRASING`, `DAILY_BRIEFING_LOCATION`, `NOTES_FOLDER`,
  `NOTES_CHUNK_CHARS`, `NOTES_QA_TOP_K`, `NOTES_MAX_CHUNKS_PER_FILE`,
  `DIWALI_DATE`, `HOLI_DATE`.
- `sara/core/memory.py`: new `proactive_log` table plus
  `log_proactive_event()`, `get_recent_proactive_events()`,
  `get_last_proactive_event()`, `get_proactive_stats()`; new streak
  methods `record_interaction_day()`, `get_streak_count()`; new
  `get_conversation_stats()` (backs a "Shareable Moments" card).
- `sara/tools/reminders.py`: `get_upcoming(within_minutes)` for the
  Proactive Engine's reminder heads-up (purely additive; does not
  change the existing on-time alarm).
- `sara/tools/system/system_info.py`: `get_battery_raw()` (numeric
  percent/plugged tuple) for the Proactive Engine's battery-low check.

### Fixed — regressions introduced by the incoming feature-update zip

- `main.py` was missing the `build_core_objects` / `run_sara_logic` /
  `_handle_command` re-export imports (the explanatory comments had
  survived, the imports had not) — restored. Without them,
  `sara/gui/app/bootstrap.py` and `sara/gui/app/core.py` break and the
  app falls back to "preview mode, no backend connected."
- `requirements.txt`, `requirements-build.txt`, `.gitignore` had been
  reverted to an older state (missing `winsdk`, `pytz`, the
  `onnxruntime` CPU-vs-GPU ABI pin, `nvidia-cudnn-cu12`,
  `nvidia-cublas-cu12`, and a downgraded `pyinstaller`) — restored.
- `sara/gui/app/media.py`, `sara/gui/js/app.js`, `sara/gui/style/style.css`,
  `sara/gui/index.html` had lost album art extraction, the
  background-session-detect fix, and shuffle/repeat (backend methods
  and UI) entirely — restored.

### Fixed — pre-existing minor issues

- `sara/orchestrator/ollama_manager.py`: `_stop_ollama_background()` now
  resets the module-level `_ollama_process` to `None` after stopping.
- `sara/core/llm/engine.py`: `_summarize_ollama` / `_summarize_gemini`
  now log the final exception after all 3 retries are exhausted
  (previously silently swallowed outside `DEBUG_MODE`).

### Cleanup

- Removed ~300+ genuinely-unused imports across 44 files in
  `sara/orchestrator/`, `sara/audio/tts/`, `sara/audio/stt/`,
  `sara/tools/system/`, `sara/gui/app/`, `sara/core/llm/`. No logic
  touched — verified via `py_compile`, `pyflakes`, and a full recursive
  stubbed import of the `sara` package.

### Verification performed for this merge

1. `py_compile` on every touched file
2. `pyflakes` on the whole repository — zero issues outside the 3
   intentional re-export names in `main.py`
3. Full recursive Python import of every module in `sara/` with
   hardware/API libraries stubbed via `sys.modules`

### Reconfirmed unchanged (existed before this merge, briefly at risk, restored)

- Media player UI: album art, friendly app name, Shuffle/Repeat buttons,
  the background-music-detection fix. Not new — see Fixed section above.

### Not done / explicitly still open (see `NEXT_STEPS.md`)

- `register_intent()` and the `sara/skills/` auto-discovery mechanism
  have only been checked via static analysis and stubbed imports, not a
  real hardware/runtime run.
- `.github/workflows/` (CI) and `sara_ai.spec` (PyInstaller) — proposed
  repeatedly, still not actually in the repo.
- `.env.example` — intentionally excluded from this repo state.

---

## Previous entry: Enterprise-level structural restructure

Full split of every monolithic file into small, focused packages, plus
GitHub-readiness additions. No feature was removed; no behavior was
intentionally changed. One real bug was introduced by the split and
caught/fixed during verification (see below).

> **Note (added when this file was regenerated):** some of the
> "GitHub-readiness" items listed as Added below (`.env.example`,
> `.github/workflows/ci.yml`, `sara_ai.spec`) are **not** confirmed
> present in the current repo state — see the correction note in the
> entry above this one, and `PROJECT_MEMORY.md`/`NEXT_STEPS.md` for the
> current authoritative status of each.

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

## Verification performed for the restructure

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
