# SARA AI — Repair Programme Progress

Working log for the audit and repair engagement. Updated as each batch lands.

- **Baseline accepted:** Phase 0 + Phase 1 audit report (2026-10-26)
- **Pristine checkpoint:** `da343da` in the audit workspace mirror
- **Target tree:** `C:\project`
- **Last updated:** 2026-10-26

## Headline

| Metric | Before | After |
| --- | --- | --- |
| pytest | 2009 passed, **19 failed**, 5 skipped | **2044 passed, 0 failed**, 24 skipped |
| pytest wall time | 40 s | 33 s |
| pyflakes "real issues" (CI gate) | 0 | 0 |
| pyflakes unused-import warnings | 31 | 10 (all documented re-exports) |
| Node JS tests | 57 pass, 1 fail | 76 pass, 1 fail *(same pre-existing failure)* |
| Dead front-end assets | 37,111 bytes | 0 referenced |

## Status key

| Mark | Meaning |
| --- | --- |
| DONE | Implemented and verified by a test run |
| PARTIAL | Implemented, verification limited (see note) |
| BLOCKED | Cannot be completed from this environment |
| TODO | Not started |

## Batches

| # | Batch | Status | Verified by |
| --- | --- | --- | --- |
| 0 | Safety checkpoint + write-back proof | PARTIAL | Write-back round-tripped and byte-checked. Git checkpoint **not** creatable from here — see Blockers |
| 1 | Mock fallback returns explicit failure; mock out of production | DONE | 19 new node tests + 7 new pytest tests |
| 2 | `toggle_media_mute` contract fix + regression tests | PARTIAL | Contract verified red→green in pytest. **COM/pycaw behaviour not verified — needs Windows** |
| 3 | Platform-guard Windows-only tests; remove dead imports | DONE | pytest 0 failed; pyflakes 31→10 |
| 4 | CSS consolidation reviewed, then removed | DONE | Headless-Chromium computed-style diff: 0 differences |
| 5 | Gemini planner/executor tool-calling tests | DONE | 17 new tests, mutation-verified |
| 6 | Remaining audit / architecture / GUI / security / performance | TODO | Scope undefined — instruction doc truncated at line 109 |

## What changed, by batch

### Batch 1 — the mock fallback (audit finding D2)
- `js/core.js`: added `apiIsBound()`. `window.pywebview.api` is injected as an
  **empty** `{}` and filled in one shot by `_createApi()`, so an empty api now
  means "still binding" (retry), not "connected".
- A connected bridge missing the requested name now returns
  `{ok: false, error: 'unknown_method'}`, logs, and emits `api:missing` —
  instead of silently falling through to the preview mock.
- A throwing backend method returns `{ok: false, error: 'call_failed'}`.
- `js/mock.js` removed from `index.html`; `core.js` fetches it on demand only
  when there is no bridge at all.
- `mock.js`'s `default: return ok()` replaced with an explicit failure.

### Batch 2 — the Music mute button (D3)
- Added `ApiMediaMixin.toggle_media_mute()`, the missing companion to
  `get_media_volume()` / `set_media_volume()`. `js/music.js:414` had always
  called it; it never existed.
- Registered in `tests/test_api_surface.py` (92 → 93 expected methods).

### Batch 3 — platform guards and dead imports (D5, D7)
- `tests/test_open_app_search_fallback.py` holds **48** tests, not 19. Only the
  7 functions (19 cases) that reach `_ensure_windows()` carry the new
  `@requires_windows` marker; the 29 cross-platform schema tests still run.
- 21 genuinely dead imports removed. **4 load-bearing re-exports kept** and
  marked `# noqa: F401` — dropping `_handle_command` is the documented root
  cause of the old "preview mode, no backend connected" bug.

### Batch 4 — CSS consolidation (D6)
- Reviewed first: `motion-pack.css` GROUP `"au"` is a faithful minified copy of
  all 176 `aurum.css` selectors; all 30 apparent differences are minifier
  artifacts and CSS-equivalent.
- `aurum.css` loaded **after** `motion-pack.css`, and `:where()` has zero
  specificity — so the unscoped copy won every conflict, which meant the
  user-facing **"Detail effects" toggle did nothing at all**.
- Unlinked `aurum.css`; removed `aurum.css` and `luxe.css` from the tree.

### Batch 5 — Gemini planning coverage (D4) + test isolation
- Added `FakeGeminiClient`, mode-for-mode identical to `FakeOllamaClient`.
- 17 new tests for the default `generate_content` → `function_calls` path,
  including cross-backend agreement tests.
- Fixed two **pre-existing, order-dependent** test bugs (see
  VALIDATION_REPORT.md): a shut-down shared thread pool, and a lazy
  orchestrator import landing inside a plan's timeout budget.

## Blockers

1. **No Git repository in `C:\project`** — still unresolved. This session has
   file access but no shell on that machine, so `git init` cannot be run from
   here. Mitigations in place: a real Git history in the audit mirror
   (`da343da` → `fadcedf`), pristine per-batch backups under
   `docs/audit/backups/`, and cumulative patches under `docs/audit/`.
2. **Instruction document truncated at line 109**, mid-sentence inside Phase 1.
   Phases 2+ never arrived, so batch 6's scope and acceptance criteria are
   undefined.

## Outstanding

| Item | Why it is still open |
| --- | --- |
| Delete `sara/gui/style/aurum.css` and `luxe.css` from `C:\project` | No file-delete capability from this session. Both are inert (nothing links them); `_PENDING_DELETION` in `tests/test_gui_assets.py` keeps the suite green either way |
| `ui_prefs_theme.test.js` theme-order failure | **Pre-existing**, needs a one-line product decision — see VALIDATION_REPORT.md |
| Run the node JS suites in CI | They are not in `ci.yml` at all, which is why the theme failure went unnoticed. Held until the theme decision above |
| Windows verification of `toggle_media_mute` | Needs a run on the target machine |
| `audioop` removed in Python 3.13 | `sara/audio/stt/helpers.py:75`; blocks a future interpreter upgrade |
| Batch 6 scope | Awaiting the rest of the instruction document |
