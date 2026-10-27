# SARA AI — Findings Register

Every finding carries: severity, evidence, affected files, root cause, proposed
correction, implementation status, verification result.

**Severity scale** — *Critical*: data loss, security, or the app not starting ·
*High*: a user-visible feature is broken or silently lies · *Medium*: real
defect with a workaround, or a gap that hides defects · *Low*: hygiene.

**Status** — `FIXED` (applied and verified) · `BLOCKED` (cannot be done from
this session) · `OPEN` (not yet addressed) · `WONTFIX` (deliberate).

Last updated: 2026-10-28 · repo HEAD `2ac8bbe` · mirror `c1d48d9`

---

## Summary

| ID | Severity | Finding | Status |
| --- | --- | --- | --- |
| D1 | Critical | No version control | FIXED (by user) |
| D2 | High | Mock fallback fabricated success in production | FIXED |
| D3 | High | Music mute button called a nonexistent method | FIXED |
| D4 | Medium | Default Gemini planning path had no test coverage | FIXED |
| D5 | Medium | 19 tests had no platform guard | FIXED |
| D6 | Low→High | CSS duplicate; **also a dead user-facing toggle** | FIXED |
| D7 | Low | 21 unused imports | FIXED |
| D8 | Medium | Shared thread pools destroyed by a test | FIXED |
| D9 | Medium | Lazy import inside a plan's timeout budget | FIXED |
| D10 | Medium | Theme list declared 3× and had drifted | FIXED |
| D11 | Medium | **~3.5 s of startup spent recompiling regexes** | FIXED |
| D12 | Medium | CI ran none of the 77 front-end tests | CONFIGURED (run unconfirmed) |
| D13 | Low | `audioop` removed in Python 3.13 | OPEN |
| D14 | Low | 285 files with trailing whitespace | OPEN |
| D15 | Low | `bench/bench/` stray nested duplicate | OPEN |
| D16 | Medium | **Shutdown: one failing step silently lost the conversation log** | FIXED |
| D17 | Medium | **Security policy did not treat `<name>.env` as a secret** | FIXED |
| D18 | Low | Plan-executed steps are absent from the action_log audit trail | OPEN |

Details for D1–D9 are in VALIDATION_REPORT.md §3–§4. D10 is in §3.7. The rest
are below.

---

## D11 — ~3.5 s of startup spent recompiling the same regexes

**Severity:** Medium (no incorrect behaviour; it is pure latency, paid on every
launch of a voice assistant where startup time is the first thing a user feels).

**Affected files:** `sara/core/intent/engine.py` (fix),
`sara/skills/__init__.py` (trigger), `sara/orchestrator/intent_handlers.py:343`
(why it was hard to see).

**Evidence.** Cold import cost, Linux / Python 3.11, minimum of 5 subprocess
runs, net of the interpreter floor (17.6 ms):

| Module | Before | After |
| --- | --- | --- |
| `sara.skills` | 4,968 ms | **1,467 ms** |
| `sara.orchestrator` | 4,595 ms | **1,486 ms** |
| `sara.gui.app` | 5,147 ms | **1,627 ms** |

`re.compile` call counts during `import sara.skills`: **12,703 → 586** (−95%).
Cache after a full startup: 2,051 hits / 154 misses.

`cProfile` on `import sara.skills` put the time squarely in the regex engine —
`re/_parser.py:_parse` 313,075 calls, `re/_compiler.py:_compile` 392,410 calls.

**Root cause.** `engine._rebuild_routes()` recompiles *every* intent's patterns,
and `register_intent()` calls it on every registration. Loading `sara/skills/`
fires 14 registrations, so the whole table — 140 intents, 733 patterns — was
rebuilt 14 times over.

It was hard to spot because `-X importtime` reported 3,378 ms of **self** time
on `sara.orchestrator.intent_handlers`, which looked like that module doing
heavy work. It is not: line 343 does a *dynamic*
`importlib.import_module("sara.skills")`, and importtime cannot attribute a
dynamic import to a child, so the entire skills-discovery cost was folded into
the parent's self time.

**Correction.** `_merge_groupless()` is pure, so its compile step is memoised on
the pattern tuple (`_merge_groupless_cached`, `lru_cache(1024)`). Rebuilds
2..N become near-free. The public helper still returns a **fresh list** over
shared immutable compiled objects, so callers may own their result.

**Status:** FIXED (mirror `c2a4f83`).

**Verification.** Equivalence was *proven*, not assumed: the live route table
was compared against the original uncached algorithm entry by entry on
`(pattern, flags, groups)` for all 154 intents — identical. `_ROUTES` stays in
step. `tests/test_intent_engine_rebuild_cost.py` (9 tests) guards it by
asserting **compile counts** rather than wall-clock, so it cannot go flaky: 10
warm rebuilds of 733 patterns must cost 0 compilations. Verified red by
removing the memoisation, green after. Full suite 2056 passed, 0 failed.

**Not verified on Windows.** These are Linux import-time numbers. The relative
saving should carry, but the absolute startup figure on the target machine is
unmeasured.

---

## D16 — one failing shutdown step silently lost the conversation log

**Severity:** Medium — data loss, on an error path, with no error surfaced.

**Affected files:** `sara/orchestrator/core_wiring.py` (fix),
`sara/orchestrator/db_writer.py` (the victim), `sara/orchestrator/tts_worker.py`
and `sara/audio/tts/engine.py` (the realistic trigger).

**Root cause.** `run_sara_logic()`'s `finally:` block tore down six components
in sequence but wrapped only `reminders.shutdown()`. The other five ran
unguarded, so one raising step skipped every step after it —
and `db_writer.shutdown()` sat second-to-last. That call is what flushes queued
conversation-log writes: `AsyncDBWriter` drains a `queue.SimpleQueue` on a
**daemon** thread, so `_flush_pending()` at shutdown is the only thing that
gets the queue's tail into the DB.

The trigger is the step immediately before: `tts.shutdown()` reaches
`_release_duck()`, the chunker/synth pools and the sounddevice stream, and
`TextToSpeech.__del__` wraps its own call in try/except *because it can raise*.

**Correction.** Extracted to `shutdown_logic_components()` — every step
independently guarded, failures logged and returned rather than swallowed,
producers stopped before drainers so the flush cannot race new writes, all
components optional.

**Status:** FIXED (mirror `6eb36c2`).

**Verification.** `tests/test_shutdown_lifecycle.py`, 15 tests; the central one
is parametrised over all five earlier steps. Verified **red** against the
pre-fix semantics (10 failed / 5 passed), green after. Full suite 2071 passed,
0 failed.

**Not verified on Windows** — the raising paths (pycaw, sounddevice, COM)
cannot be exercised here; the guarantee is structural plus fault injection.

See `ARCHITECTURE_REVIEW.md` for the full concurrency inventory.

---

## D12 — CI ran none of the 77 front-end tests

**Severity:** Medium — it is the reason D10 sat unnoticed.

**Affected files:** `.github/workflows/ci.yml`.

**Root cause.** The three node suites under `sara/gui/js/tests/` were executed
by nothing in CI. They are dependency-free Node scripts that already exit
non-zero on a failed assertion, so they were ready to be a gate and simply
never were.

**Correction.** An additive `js-test` job.

**Status: CONFIGURED LOCALLY — pipeline execution NOT yet confirmed.**

This session could not write the file (`.github/workflows/ci.yml` is a
protected path); the user applied it and committed it as **`2ac8bbe`,
"Add JavaScript test suite to CI"**, parent `144787c`.

Reviewed as committed:

| Check | Result |
| --- | --- |
| `ci.yml` parses | yes — jobs `lint, test, js-test, type-check, bench` |
| `js-test` is a real gate | yes — `continue-on-error` unset |
| Existing gates weakened? | no — `lint` and `test` still `continue-on-error: false` |
| `bench` / `type-check` | advisory (`true`) — **pre-existing**, not changed by this job |
| Runner | `ubuntu-latest`, 3 steps |

**Two things that are NOT verified:**

1. **No GitHub Actions run has been observed.** "Configured" is not "passing".
   The job is correct by inspection and the loop it runs was executed locally,
   but whether the workflow actually succeeds on GitHub — checkout, runner
   availability, `actions/setup-node@v4` resolving the requested Node — is
   unconfirmed until a run is seen.
2. **The committed job pins `node-version: "24"`; local verification used
   Node v22.23.4.** Only Node 22 is available in this environment, so the
   suites have not been executed on Node 24. They are dependency-free vanilla
   scripts using only `fs`, `path`, `vm` and `setTimeout`, so the risk is low
   — but low is not zero, and it is unverified.

**Local result (Node v22.23.4):** `bridge_contract` 19/0, `chat_state` 11/0,
`ui_prefs_theme` 47/0.

## D17 — the security policy did not treat `<name>.env` as a secret

**Severity:** Medium — a real secrets file in this repository was readable
through the assistant's own file tools.

**Affected files:** `sara/core/security/policy_rules.py` (fix),
`sara/core/security/policy.py` (the two layers that missed it).

**Root cause.** `is_sensitive_parts()` matched env files by exact name:

```python
if name == ".env" or name.startswith(".env."):
```

so `.env` and `.env.local` were secret but the common `<name>.env` convention
was not. This repo ships exactly such a file — **`ll.env`**, listed in
`.gitignore` precisely because it holds real configuration.

Two layers both missed it. `_looks_like_path("ll.env")` is `False` (no drive
letter, no separator, no `..`), so a bare filename was never treated as a path
at all; and when a full path *was* given, `sensitive_path()` returned `False`
because no pattern matched.

**Evidence** — through the real validator, before the fix:

| Call | Result |
| --- | --- |
| `find_file query=".env"` | blocked, `sensitive_path` |
| `find_file query="ll.env"` | **ALLOWED** |
| `open_file path="C:\project\.env"` | blocked, `sensitive_path` |
| `open_file path="C:\project\ll.env"` | **ALLOWED** |

**Correction.** Widened `_SECRET_FILE_PATTERNS`. The justification is evidence
rather than taste: **the repo's own `.gitignore` is the best available
statement of what this project treats as secret, and the policy list was
strictly narrower than it.** Added `*.env`; `*.jks`, `*.keystore`,
`*.cookies`, `cookies*.txt`, `session*.json` (all already gitignored);
`.git-credentials`, `.netrc`, `_netrc`, `.npmrc`, `.pypirc`; and
`id_ecdsa*`, `id_dsa*`.

A file or directory named plainly `env`, and names like `environment.txt`, are
deliberately **not** matched.

**Status:** FIXED (mirror `c1d48d9`).

**Verification.** `tests/test_security_secret_files.py`, 39 tests: positive
classification, an explicit over-match guard list covering every entry in the
existing `NOT_SENSITIVE` set, end-to-end refusal through `validate_args()`,
and a test that keeps the policy list and `.gitignore` honest about each
other. Verified **red** against the old pattern tuple (23 failed / 16 passed),
green after. Full suite 2110 passed, 0 failed; security suite 371 passed.

---

## D18 — plan-executed steps are missing from the audit trail

**Severity:** Low — an audit-trail gap, not a privilege gap.

**Affected files:** `sara/orchestrator/dispatcher.py` (`_build_plan_dispatch_fn`).

Steps executed through the planner's dispatch callback are **not** written to
the `action_log` table, so a multi-step plan's actions do not appear in the
"what did Sara just do" history that single commands produce. The authors
document this themselves as a known scope limit.

It is not a privilege hole — those steps are fully guarded (see §Checked and
found sound) — but it means the audit log under-reports.

**Status:** OPEN. Fixing it means deciding whether a plan logs one entry or one
per step, which is a product question.

---

## D13 — `audioop` is removed in Python 3.13

**Severity:** Low today, blocking later.

**Affected files:** `sara/audio/stt/helpers.py:75`.

**Root cause.** `audioop` was deprecated in 3.11 and **removed** in 3.13. The
project pins 3.11, so nothing is broken now, but any interpreter upgrade turns
this into a hard `ImportError`, not a warning.

**Correction (proposed, not applied).** Replace the few `audioop` calls with
`numpy` equivalents (numpy is already a dependency), or vendor the handful of
operations used. Needs an audio-behaviour check on Windows, so it is not a safe
blind change from here.

**Status:** OPEN.

---

## D14 — Trailing whitespace in 285 files

**Severity:** Low.

**Evidence.** 285 tracked text files contain trailing whitespace; worst are
`sara/core/llm/engine.py` (48 lines), `README.md` (35), `tests/test_planning.py`
(33). Line endings are clean — **0 CRLF files**, all LF.

**Status:** OPEN, deliberately. Stripping it would touch 285 files and bury
every future `git blame` and review diff under cosmetic noise. Better done as
one isolated commit when convenient, ideally with a pre-commit hook to stop it
recurring.

---

## D15 — `bench/bench/` stray nested duplicate

**Severity:** Low.

**Evidence.** `bench/bench/results/latest.json` (~195 KB) duplicates
`bench/results/latest.json`. Both are committed in `144787c`.

**Correction.** Delete `bench/bench/` after confirming nothing references it.

**Status:** OPEN — this session has no delete capability on the target machine.

---

## Checked and found sound

Recorded so the same ground is not re-ploughed. Full detail in the Phase 0/1
report; each was verified, not assumed.

| Area | Verdict |
| --- | --- |
| Placeholder/stub implementations in Python | 0 TODO, FIXME, XXX, HACK, `NotImplementedError`, pass-only or `...`-body functions across 304 files |
| Unsafe command execution | 0 `shell=True`, 0 `os.system`, 0 `exec()`; processes spawned in list form only |
| Unsafe deserialization | 0 `pickle.load`, 0 `yaml.load` |
| `eval()` injection | All 4 grep hits are substring matches; the calculator is a whitelisted AST walker with depth, length, exponent and digit limits |
| Duplicate event listeners | 0 cases of one element + one event bound in more than one file |
| Circular imports | `dispatcher ↔ route_chat` is real but deliberately broken by a call-time import with an explanatory comment. `router.policy ↔ router.features` was a **false positive** — `TYPE_CHECKING` only |
| Dead front-end assets | 0 — 36 stylesheets linked == 36 on disk; 0 broken includes |
| FX pack toggles | Guarded by `if (!packLink(...)) return;`, so toggles for removed stylesheets are filtered at render time |
| `_ApiProxy` bridge shape | Sound; verified against pywebview's own `get_functions()` introspection |
| Privilege decision matrix | Fail-safe: an unknown origin degrades to `ORIGIN_LLM_TOOL`, and an internal error denies tier ≥ 2 |
| Assistant-generated tool execution | Guarded. Planner steps pass through `_guard_check(..., ORIGIN_LLM_TOOL, ...)` in `_build_plan_dispatch_fn` **before** dispatch, plus a second `_plan_step_confirmation_needed()` gate. No unguarded plan path exists |
| Markup hygiene | No inline `<script>`, `<style>` or `on*=` handlers in `index.html` |
