# SARA AI — Repair Programme Progress

Working log for the audit and repair engagement. Updated as each batch lands.

- **Baseline accepted:** Phase 0 + Phase 1 audit report (2026-10-26)
- **Repository checkpoint:** `144787c` "Checkpoint SARA AI after initial audit repairs" — 477 files, initial commit, verified on disk (`refs/heads/master`, reflog parent `0000…`)
- **CI commit:** `2ac8bbe` "Add JavaScript test suite to CI" (parent `144787c`)
- **Audit mirror:** `da343da` (pristine) → `7409bd8` (batch 6b(3) + docs)
- **Target tree:** `C:\project`
- **Uncommitted:** batches 6a, 6b(1), 6b(2), 6b(3) — awaiting the user's checkpoint
- **Last updated:** 2026-10-28

## Headline

| Metric | Before | After |
| --- | --- | --- |
| pytest | 2009 passed, **19 failed**, 5 skipped | **2115 passed, 0 failed**, 19 skipped (2134 collected) |
| pytest wall time | 40 s | 32.45 s |
| pyflakes "real issues" (CI gate) | 0 | 0 |
| pyflakes unused-import warnings | 31 | 10 (all documented re-exports) |
| Node JS tests | 57 pass, **1 fail** | **77 pass, 0 fail** — on Node 22 *and* Node 24 |
| Dead front-end assets | 37,111 bytes | 0 — files deleted |
| Front-end tests run by CI | **none** | `js-test` job committed as `2ac8bbe` — **pipeline run still not observed** |

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
| 6a | Theme order (I), asset-test cleanup (H), CI node job (J) | **DONE** | I and H applied and verified here. J applied by the user and committed as `2ac8bbe`; reviewed, but no pipeline run observed |
| 6b | Architecture / GUI / traceability / security / performance | **IN PROGRESS** | Performance done: see D11. Remainder outstanding |

### Batch 6a detail

Applied on top of checkpoint `144787c`. Every source file was verified
byte-identical to the checkpointed content before editing.

| Item | Status | Result |
| --- | --- | --- |
| **I** — `theme.js`: Lime after Oat, before Custom | **DONE** | `ui_prefs_theme.test.js` 46 passed/1 failed → **47 passed/0 failed**. All three declarations of the theme list now agree |
| **H** — `test_gui_assets.py`: drop obsolete exemptions | **DONE** | `_PENDING_DELETION` now empty; 36 stylesheets on disk == 36 linked |
| **J** — CI node job | **CONFIGURED** | Applied by the user and committed as `2ac8bbe`. Reviewed: real gate, no existing gate weakened. **GitHub Actions execution not yet confirmed.** Node 24 pin now exercised by the user (v24.22.1, 19/11/47 passing) — that earlier caveat is closed |

Three regression tests added for the three-way theme drift that caused item I
(`test_theme_list_matches_backend_preference_list`,
`test_every_theme_is_accepted_by_the_backend_validator`,
`test_lime_sits_between_oat_and_custom`). Verified red by restoring the old
order, green after.

**Divergence resolved:** the mirror's `ci.yml` has been synced to the user's
committed version (`2ac8bbe`), which differs from the original proposal only
in trimmed comments and `node-version: "24"` instead of `"20"`.

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

1. ~~No Git repository in `C:\project`~~ — **RESOLVED 2026-10-27.** Checkpoint
   `144787c` committed with a real author identity. Verified from `.git` on
   disk, not taken on trust.
2. ~~`.github/workflows/ci.yml` is a protected path~~ — **RESOLVED
   2026-10-27.** The user applied the job and committed it as `2ac8bbe`.
   This session still cannot write that path, so any future CI change must
   go through the user. **The pipeline has not been observed running**:
   configured is not the same as passing.
3. **No shell on the target machine.** All verification runs on a Linux
   mirror; nothing Windows-specific can be executed. See
   VALIDATION_REPORT.md §5.2.

## Outstanding

| Item | Why it is still open |
| --- | --- |
| ~~Delete `aurum.css` / `luxe.css`~~ | **DONE 2026-10-27** by the user. 36 on disk == 36 linked |
| ~~`ui_prefs_theme.test.js` theme-order failure~~ | **FIXED** in batch 6a item I |
| Confirm the `js-test` job actually passes on GitHub Actions | Committed as `2ac8bbe`, reviewed and sound by inspection, but **no run observed**. The Node 24 concern is closed: the user ran all three suites on Node v24.22.1 (19/11/47 passing). What remains unverified is the runner itself — `checkout@v4`, `setup-node@v4`, `ubuntu-latest`, the job's shell loop |
| Windows verification of `toggle_media_mute` | Needs a run on the target machine. Steps in VALIDATION_REPORT.md §3.2 |
| `audioop` removed in Python 3.13 | `sara/audio/stt/helpers.py:75`; blocks a future interpreter upgrade |
| Trailing whitespace in 285 files | Cosmetic; worst is `sara/core/llm/engine.py` (48 lines). Not touched — would bloat every diff |
| `bench/bench/results/latest.json` | Stray nested duplicate of `bench/results/latest.json` (~195 KB) |
| Batch 6b remainder | Architecture (D), GUI review (E), traceability (B), bridge sweep (C), security (F) still to do. Performance (G) done — D11 |


## Batch 6b — progress

| Item | Status | Result |
| --- | --- | --- |
| **G** — performance, measurable baselines | **DONE** | **D11**: ~3.5 s off startup. `import sara.orchestrator` 4,595 ms → 1,486 ms; `re.compile` during skill discovery 12,703 → 586. 9 regression tests asserting compile counts, not wall-clock |
| **B** — end-to-end feature traceability | TODO | |
| **C** — bridge contract / GUI wiring sweep | PARTIAL | Name-level contract enforced in CI since batch 1. Argument-arity checking not yet done |
| **D** — architecture, threading, lifecycle, shutdown | **DONE** | **D16** found and fixed: a failing teardown step silently lost the conversation log. Full concurrency inventory in `ARCHITECTURE_REVIEW.md` |
| **E** — GUI functionality, a11y, theme, responsiveness | TODO | → `GUI_REVIEW.md` |
| **F** — security and privacy | **DONE** | **D17** found and fixed: `<name>.env` (this repo's own `ll.env`) was not classified secret. Privilege matrix and assistant-generated tool execution reviewed and found sound. **D18** recorded: plan steps absent from the audit log |

Findings register: `docs/audit/FINDINGS.md`.
