# NEXT_STEPS.md — Sara AI

## Known still-open items (from the latest feature merge — do NOT mark these as fixed)

- `sara/core/intent/engine.py`'s `register_intent()` and `sara/skills/`'s
  auto-discovery are new and **have not yet been tested against real
  hardware/runtime** (only static analysis + stubbed import tests so far).
  Run `python main.py` on the real Windows machine and actually trigger a
  skill (e.g. "tell me a joke", the daily briefing) before trusting it in
  production.
- `.github/workflows/` (CI) and `sara_ai.spec` (PyInstaller) have been
  proposed multiple times across past sessions but **never actually
  added to the repo** — do not claim they exist until they're actually
  created and confirmed present.
- `.env.example` is **intentionally not part of this repo state** — the
  user maintains their own `.env` directly. Don't regenerate one unless
  explicitly asked to.

## Immediately after receiving any future restructure/merge package

1. **Don't commit temporary scaffolding scripts** (splitter/helper
   scripts, intermediate outputs) if you re-run any part of a large
   restructure manually.
2. Extract onto a fresh `git init` (or a new empty repo folder) rather
   than on top of the old repo, so file deletions are captured correctly
   in the first commit rather than leaving orphaned old files behind.
3. Update the GitHub URL placeholder in `README.md`
   (`git clone https://github.com/<your-username>/sara-ai.git`) and in
   `CONTRIBUTING.md` with the actual repo URL before publishing.
4. Run `python main.py` on the actual Windows machine (with real mic/
   models/Ollama) to confirm end-to-end behavior — everything documented
   here was verified via real Python imports with hardware/API libraries
   stubbed out, which is strong evidence nothing is structurally broken,
   but it is **not** a substitute for one real run with actual hardware.
   This is especially important for the Proactive Engine, the skills
   auto-discovery system, and the Setup Wizard, none of which have had a
   real hardware run yet.

## Pending from the original roadmap (still open after the latest merge)

- [ ] Custom-trained wake-word model (currently STT-fallback based)
- [ ] Cross-platform support (currently Windows-only)

> Note: "Proactive suggestions (calendar-aware, battery/system-state-aware)"
> used to be listed here as pending — it is **no longer pending**, it now
> exists as the Proactive Engine (`sara/orchestrator/proactive.py`,
> battery + reminder + idle + streak triggers). Don't re-add it to the
> roadmap as an open item.

## Other open items carried forward from the structural restructure

- `sara.core.tool_router` **does exist** in the codebase and is wired in
  as the fallback layer between the fast regex intent router and the
  full LLM chat path (`TOOL_NAME_TO_INTENT` + `build_fake_match()`). An
  earlier revision of this file incorrectly implied it might be a dead
  import to remove — that was wrong; leave it in place.
- CI (`.github/workflows/ci.yml`, if/when it's actually added) should
  only be expected to do compile + lint checks, not a real functional
  test (not feasible without audio hardware/GPU/Ollama in a CI runner).
  If deeper CI coverage is wanted later, good first candidates for pure
  unit tests (no hardware needed) are `sara/orchestrator/calc_utils.py`
  (`_safe_calc`, `_parse_duration_to_seconds`), `sara/core/intent/`
  (`detect_intent` against known phrases), and
  `sara/orchestrator/text_utils.py`.
- `pyflakes` config note: two pre-existing warnings (`main.py`'s unused
  `global _ollama_process`, `llm/engine.py`'s unused `last_exc` in two
  retry-loop branches) were confirmed present in the original code and
  intentionally left as-is during the structural restructure. Worth a
  small follow-up cleanup pass if a fully clean lint report is wanted.
- `sara_ai.spec`, if/when it's actually created, should have its icon
  line uncommented (`icon="assets/icon.ico"`) once an `.ico` file exists
  under `assets/`, if a custom `.exe` icon is wanted.

## Structural conventions to follow going forward

Documented in `CONTRIBUTING.md`, repeated here for quick reference: new
code should slot into the existing package split
(`sara/audio/{stt,tts}/`, `sara/core/{llm,intent}/`, `sara/tools/system/`,
`sara/gui/app/`, `sara/orchestrator/`, `sara/skills/`) rather than
growing any single file back toward the 600+ line range that prompted
the original restructure. If a new file starts approaching ~300–400
lines and covers more than one clear concern, that's the signal to split
it before it becomes another monolith.

## Suggested enterprise-level improvements (not yet implemented — ideas only)

These are optimizations worth considering to push the project toward a
more "enterprise-grade" portfolio piece. None of these exist yet — do
not describe them as done anywhere else in the docs until they're
actually built and verified the same way everything else in this
project is (py_compile + pyflakes + stubbed real import, at minimum):

- **Actually add the CI workflow and PyInstaller spec** that have been
  proposed repeatedly but never committed (see "Known still-open items"
  above) — this is the single highest-value item since it's already
  been designed multiple times.
- **Automated tests beyond smoke tests** — `tests/test_sara_smoke.py`
  exists, but real `pytest`-based unit tests for the pure-logic modules
  listed above (`calc_utils`, `intent` matching, `text_utils`) would
  give CI something real to run without needing hardware.
- **Structured logging with correlation IDs** — tagging each voice-loop
  turn with a request ID across STT → intent/tool_router → LLM → TTS
  would make debugging a specific failed turn much easier from the
  rotating log files alone.
- **A documented rollback/versioning convention for `.env`** — since
  `.env.example` is deliberately excluded from the repo, consider at
  least documenting (in `CONTRIBUTING.md` or `BUILD.md`, once it exists)
  the full list of required vs. optional environment variables so a new
  contributor isn't blocked figuring out what to set.
- **Health-check endpoint/CLI flag** — `health_check.py` already runs at
  startup; exposing it as a standalone `python main.py --healthcheck`
  path (no GUI, no mic) would make it usable in a CI or monitoring
  context later, without needing the full app to boot.
- **Dependency pinning audit** — `requirements.txt` mixes pinned and
  unpinned versions in places (per the regression history in
  `CHANGELOG.md`, ABI-sensitive packages like `onnxruntime`/
  `nvidia-cudnn-cu12` have already caused real breakage once); a full
  pin audit with a `requirements.lock` or `pip-compile` output would
  prevent that class of regression from recurring.
