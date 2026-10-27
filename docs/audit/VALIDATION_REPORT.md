# SARA AI — Validation Report

What was actually verified, how, and what remains unverified. Written to be
read sceptically: a claim here should be checkable from the command given.

- **Date:** 2026-10-28 (batches 0–5 on 2026-10-26, 6a on 2026-10-27, 6b(1)–6b(3) on 2026-10-27/28)
- **Repository checkpoints:** `144787c` (initial, 477 files) → `2ac8bbe` ("Add
  JavaScript test suite to CI") in `C:\project` — both verified from `.git` on disk
- **Audit mirror:** `da343da` (pristine) → `7409bd8` (batch 6b(3) + docs)
- **Uncommitted at time of writing:** batches 6a, 6b(1), 6b(2), 6b(3) — 7 modified
  files, 5 new source/test files, held for the user's own checkpoint

---

## 1. Verification environment — and its limits

All verification ran on a **Linux mirror** of `C:\project`, Python 3.11 (the
version CI pins and the project's `.venv` uses).

| | |
| --- | --- |
| Interpreter | Python 3.11.17 |
| Installed for the run | `python-dotenv`, `numpy`, `dateparser`, `requests`, `psutil`, `pyperclip`, `mss`, `google-genai`, `keyboard`, `pytest`, `pyflakes` |
| Stubbed | `webview` (pywebview's `proxy_tools` dependency will not build here) |
| Absent | `winsdk`, `pycaw`, `pyaudio`, `faster-whisper`, `webrtcvad`, `openwakeword`, `sounddevice`, `kokoro-onnx`, `Pillow` |
| Browser | Chromium via Playwright (for the CSS computed-style diff) |
| Node | v22.23.4 *(this environment)*; **v24.22.1 run by the user on `C:\project`** — see §2.4 |

**`winsdk` publishes no Linux wheel**, which is exactly why the project's CI is
`windows-latest` only. Everything Windows-specific is therefore *unverified
here* and is marked as such below.

**Skip counts depend on which optional dependencies are installed.** The suite
totals **2134 collected tests** in every run below; how many of those *skip*
varies with the environment, because a number of tests are gated on an optional
import being present. The final run had `keyboard` installed where an earlier
run did not, which moved 5 tests from "skipped" to "passed" — the same tests,
simply now exercised. Read the **totals**, not the pass count alone.

---

## 2. Test results

### 2.1 Python — `python -m pytest -q`

| Run | Passed | Failed | Skipped | Collected |
| --- | --- | --- | --- | --- |
| Baseline (`da343da`) | 2009 | **19** | 5 | 2033 |
| After batch 3 | 2017 | 0 | 24 | 2041 |
| After batch 4 | 2027 | 0 | 24 | 2051 |
| After batch 5 | 2044 | 0 | 24 | 2068 |
| After batch 6a | 2047 | 0 | 24 | 2071 |
| After batch 6b(1) — D11, +9 | 2056 | 0 | 24 | 2080 |
| After batch 6b(2) — D16, +15 | 2071 | 0 | 24 | 2095 |
| **After batch 6b(3) — D17, +39** | **2115** | **0** | 19 | **2134** |

All 19 baseline failures came from a single cause: `_ensure_windows()` raising
`RuntimeError` in `sara/tools/system/_shared.py:38`. They were never logic
failures — they pass on Windows and are now explicitly skipped off it.

The final row's 2115/19 split rather than 2110/24 is the dependency effect noted
in §1: 2134 collected either way, with 5 optional-import-gated tests executing
instead of skipping. **0 failed in every run from batch 3 onward.**

Wall time: 40 s baseline → 33 s after batch 5 → **32.45 s** at head (see §4.2).

### 2.1a Per-batch targeted suites (final run)

| Suite | Finding | Result |
| --- | --- | --- |
| `tests/test_gui_assets.py` | 6a — asset wiring + theme drift | **13 passed** |
| `tests/test_intent_engine_rebuild_cost.py` | 6b(1) — D11 startup cost | **9 passed** |
| `tests/test_shutdown_lifecycle.py` | 6b(2) — D16 teardown | **15 passed** |
| `tests/test_security_secret_files.py` | 6b(3) — D17 secret files | **39 passed** |
| `-k "security or policy or taint"` | regression sweep | **395 passed**, 0 failed |

### 2.2 Order-independence

The suite previously had two order-dependent failures. Both are fixed:

```
pytest tests/test_planning.py tests/test_planning_gemini_backend.py   -> 111 passed
pytest tests/test_planning_gemini_backend.py tests/test_planning.py   -> 111 passed
```

### 2.3 pyflakes — the CI lint gate

CI fails only on non-unused-import findings. That count was **0 before and 0
after**. Total warnings dropped 31 → 10; every remaining one is a deliberate
re-export or side-effect import carrying a `# noqa: F401` and a reason.

### 2.4 Node JS suites

Run the way the CI job runs them — `node "$f"`, not `node --test`; the suites
are self-reporting scripts with their own runner.

| Suite | Node v22.23.4 *(this env)* | **Node v24.22.1 *(user, on `C:\project`)*** |
| --- | --- | --- |
| `bridge_contract.test.js` *(new)* | 19 passed, 0 failed | **19 passed** |
| `chat_state.test.js` | 11 passed, 0 failed | **11 passed** |
| `ui_prefs_theme.test.js` | 47 passed, 0 failed *(was 46/1 — fixed in 6a)* | **47 passed** |
| **Total** | **77 passed, 0 failed** | **77 passed, 0 failed** |

**The Node 24 gap is now closed.** The user ran all three suites on
**Node v24.22.1** against `C:\project` and reported 19, 11 and 47 passing —
identical to the Node v22.23.4 results here. The earlier caveat that "the job
pins Node 24 while all local runs used Node 22" is therefore **resolved**: the
pinned major version has been exercised, on the real repository, on Windows.

**What is still not verified** is the GitHub Actions run itself. Two local
interpreters agreeing is strong evidence the suites are sound, but it says
nothing about `actions/checkout@v4`, `actions/setup-node@v4`, the
`ubuntu-latest` runner, or the shell loop in the job definition. **Configured
and locally green is not the same as observed passing in CI.** See §5.1a.

---

## 3. Fixes applied, and the evidence for each

### 3.1 D2 — mock fallback fabricated success · **VERIFIED**

Evidence that the tests have teeth, not just that they pass:

- `tests/test_gui_bridge_contract.py` was written **before** the D3 fix and
  confirmed red, naming the exact call site:
  `AssertionError: 'toggle_media_mute' ... Called from: sara/gui/js/music.js:414`
- `bridge_contract.test.js` drives the real `core.js` in a Node `vm` and
  asserts a connected-but-missing name returns
  `{ok:false, error:'unknown_method'}` **and** that `js/mock.js` is never
  fetched while the bridge is connected.

The `apiIsBound()` change rests on a verified fact about pywebview, not a
guess: `webview/js/api.js:4` declares `api: {}`, and `webview/js/finish.js:1`
fills it later via `_createApi()`. An empty api is therefore genuinely
"still binding", and treating it as connected is what let `SARA.boot()`'s
300 ms safety net latch `realBooted` before any method existed.

### 3.2 D3 — Music mute button · **PARTIALLY VERIFIED**

- **Verified:** the contract. `toggle_media_mute` now exists on the Api; the
  static contract test is green; `test_api_surface.py` registers it.
- **NOT VERIFIED:** the method's actual behaviour. Its body uses `pycaw`,
  `_run_on_com_thread` and a Windows Core Audio session. None of that can run
  here. It is written to mirror `get_media_volume()` / `set_media_volume()`
  line for line, including the "confirm, don't assume" re-query, but
  **nobody has yet observed it mute anything.**

  To verify on Windows: play audio in a media app, open the Music page, click
  the volume button, confirm the app's volume slider in the Windows Volume
  Mixer shows muted, and that the returned `muted` matches.

### 3.3 D5 — platform-gated tests · **VERIFIED**

`tests/test_open_app_search_fallback.py` → 29 passed, 19 skipped on Linux.
They are skipped, not forced through with a patched `_IS_WINDOWS`: faking the
platform check would report a Windows launch path as verified on a machine
that cannot exercise it.

### 3.4 D7 — unused imports · **VERIFIED**

21 removed. Four were kept deliberately: `main.py` and
`sara/orchestrator/main.py` re-export `build_core_objects`, `run_sara_logic`
and `_handle_command`. The comment above the latter records that its absence
*is* the root cause of the old "preview mode, no backend connected" bug —
`sara/gui/app/core.py` reaches it as `self.gui_main._handle_command(...)`.
Removing them on a linter's say-so would have reintroduced a fixed bug.

### 3.5 D6 — CSS consolidation · **VERIFIED, including appearance**

Reviewed before removing, as instructed.

**Review:** `motion-pack.css` GROUP `"au"` (line 358) carries all 176
`aurum.css` selectors, re-scoped behind
`:where(html:not([data-fx-off~="au"]))`. All 30 apparent declaration
differences are minifier artifacts and CSS-equivalent:
`rgb(a / .4)` vs `rgb(a/.4)`, `content:''` vs `content:""`,
`translate:0 -1px` vs `translate:0-1px`.

**Appearance check** — both trees rendered in headless Chromium with
JavaScript disabled (for a deterministic DOM), comparing `getComputedStyle`
for 19 properties on every element plus its `::before` and `::after`:

```
elements: pristine=806 current=806
computed-style differences: 0
```

**Behaviour check** — the same comparison with `<html data-fx-off="au">`:

```
PRISTINE (aurum.css still linked): properties changed by data-fx-off="au" ->   0
CURRENT  (aurum.css removed)     : properties changed by data-fx-off="au" -> 140
```

That is the proof that the user-facing **"Detail effects" switch**
(`js/quality.js` FX registry, id `au`) was a dead control: it changed nothing,
because `aurum.css` re-applied the same rules unconditionally after
`motion-pack.css`. It now works, and default appearance is unchanged.

The asset test was verified red by re-linking `aurum.css`, then green again.

### 3.6 D4 — Gemini planning path · **VERIFIED**

`Config.LLM_BACKEND` defaults to `"gemini"` (`config.py:350`), and
`_select_llm_client()` also falls back to gemini (`clients.py:185`). The
default production path is therefore
`client.models.generate_content(...)` → `resp.function_calls[i].name/.args`.
Every pre-existing planning test used `FakeOllamaClient` with
`FakeConfig(LLM_BACKEND="ollama")`, exercising the other branch.

**Mutation test** — changing the Gemini branch's `arguments.get("steps")` to a
wrong key is caught by 3 of the 17 new tests. The new file also asserts the
fake really exposes the production shape (`.models.generate_content`,
`.function_calls`, `.name`/`.args` and *not* `.message`), so the tests cannot
pass vacuously against a drifted double.

### 3.7 Batch 6a — theme order, asset test, CI · **2 of 3 applied**

Pre-flight, before any edit: each of the three source files was hashed against
the checkpointed content in `C:\project`'s index — all three **CLEAN** (working
tree == index == `144787c`), and byte-identical to the audit mirror's
pre-change baseline. So the edits were applied to the current repository
content, not to a stale copy.

**Item I — theme ordering · VERIFIED.** The same list is declared in three
places, and they had drifted:

| Source | Position of `lime` |
| --- | --- |
| `sara/gui/js/theme.js` `LIST` | between `aurora` and `neon` ← the outlier |
| `sara/gui/app/ui_prefs.py:14` `_THEMES` | between `oat` and `custom` |
| `sara/gui/js/tests/ui_prefs_theme.test.js` | between `oat` and `custom` |

So the instruction to place Lime after Oat was *supported* by the repository,
not contradicted — two of three declarations already used that order. Nothing
was broken at runtime: `_THEMES` is only ever used as a membership test
(`text in _THEMES`, `ui_prefs.py:28`), never indexed, so no stored preference
was at risk. This was display order in the picker.

After the change all three agree exactly, and
`ui_prefs_theme.test.js` goes 46 passed/1 failed → **47 passed/0 failed**.

**Item H — asset test · VERIFIED.** `aurum.css` and `luxe.css` are gone from
disk, so `_PENDING_DELETION` is empty and `test_no_dead_stylesheets_on_disk`
now enforces the invariant with no exemptions. Asset wiring re-checked:
36 stylesheets linked == 36 on disk, 0 missing, 0 dead; 40 scripts linked,
41 on disk (the extra is `mock.js`, lazily loaded by design).

**Item J — CI node job · APPLIED BY THE USER, RUN NOT CONFIRMED.**

This session could not write `.github/workflows/ci.yml` — it is a protected
path and the write was refused:

```
rejected: C:\project\.github\workflows\ci.yml is a protected file
          and cannot be written via remote tools.
```

The user applied the prepared job and committed it as **`2ac8bbe`, "Add
JavaScript test suite to CI"** (parent `144787c`). Reviewed as committed:
`ci.yml` parses, jobs are `lint, test, js-test, type-check, bench`, `js-test`
is a real gate (`continue-on-error` unset), and `lint`/`test` remain hard
gates. `bench` and `type-check` are advisory — that was already true before
this change.

It differs from the proposal only in trimmed comments and
`node-version: "24"` instead of `"20"`.

**Still unverified: the GitHub Actions run.** No pipeline execution has been
observed. Correctness by inspection, plus green local runs, is not evidence that
the workflow succeeds on the runner.

**Resolved: the Node 24 pin.** This was previously flagged as a risk because
only Node v22.23.4 is available here. The user has since run all three suites on
**Node v24.22.1** on `C:\project` — 19, 11, 47 passing — so the pinned major
version is now exercised. See §2.4.

The mirror's `ci.yml` has been synced to the committed version, so the earlier
mirror/`C:\project` divergence is closed.

**New regression tests (3)**, guarding the drift that caused item I:
`test_theme_list_matches_backend_preference_list` (the two lists must match),
`test_every_theme_is_accepted_by_the_backend_validator` (runs every offered
theme through the real `normalize_ui_pref()`, so the picker can never show a
theme the backend rejects), and `test_lime_sits_between_oat_and_custom`.
Verified red by restoring the old order — 2 pytest failures plus the node
suite back to 46/1 — and green after.

### 3.8 D11 — ~3.5 s of redundant regex compilation at startup · **VERIFIED**

**The defect.** `_rebuild_routes()` recompiles *every* intent's patterns, and
`register_intent()` calls it once per registration. Loading `sara/skills/` fires
14 registrations, so the whole table — ~140 intents, 733 patterns — was rebuilt
14 times from scratch.

**Measured, not estimated:**

| | Before | After |
| --- | --- | --- |
| `re.compile()` calls during skill discovery | 12,703 | **586** |
| `import sara.orchestrator` wall time | 4,595 ms | **1,486 ms** |

**The fix.** `_merge_groupless()` is a pure function — the same pattern strings
always yield the same compiled objects — so its compile half was extracted into
`_merge_groupless_cached()` and memoised with `lru_cache(maxsize=1024)`.
Rebuilds 2…N become nearly free and the result is identical. Compiled pattern
objects are immutable, and the only caller copies the result into a fresh list,
so sharing them across rebuilds is safe. `from functools import lru_cache` was
already imported at `engine.py:11` — no new dependency.

**Why the tests assert compile counts, not wall-clock.** A timing assertion
would be flaky on shared CI runners. All 9 tests in
`tests/test_intent_engine_rebuild_cost.py` count `re.compile` invocations via a
patched counter, which is deterministic. They also assert that merged and
unmerged intents still produce byte-identical match results, so the cache cannot
pass by silently changing behaviour.

### 3.9 D16 — a failing teardown step silently lost the conversation log · **VERIFIED**

**The defect.** `run_sara_logic()`'s `finally:` block ran six teardown steps in
sequence with **only `reminders.shutdown()` wrapped** in try/except. Every other
step ran unguarded, so one raising step skipped all the steps after it.

That ordering mattered: `db_writer.shutdown()` sat second-to-last, and it is the
step that **flushes queued conversation-log writes** (`AsyncDBWriter._flush_pending`).
`db_writer` is a `queue.SimpleQueue` drained by a daemon thread — at interpreter
exit, anything still queued is simply lost. So a failure in, for example,
`tts.shutdown()` — which reaches `TTSWorker._release_duck()`, the chunker/synth
pools and the sounddevice output stream, and whose own `__del__` already wraps
it in try/except *precisely because it can raise* — meant **the user's last
messages were dropped with no error logged anywhere.**

**The fix.** The block was extracted into a module-level
`shutdown_logic_components(**kwargs) -> list`, where each step is independently
guarded by a `_step(name, fn)` helper that records `(name, exception)` pairs and
continues. It returns the failure list, so callers and tests can assert on it.

This mirrors the pattern `sara/gui/app/bootstrap.py` **already** uses for
process-level teardown, whose comment states the rule outright: *"each wrapped so
one failing cleanup step can never block the rest of shutdown"*. The logic
thread's teardown simply never got the same treatment.

**Ordering is unchanged, byte for byte.** This is worth stating explicitly
because an earlier draft of this fix *did* reorder two steps — swapping
`notes_memory.close()` and `tts.shutdown()` under a "stop producers before
draining" argument. **That was reverted during the diff review.** The argument
did not hold: `tts.shutdown()` already ran before `db_writer.shutdown()` in the
original sequence, so the swap bought nothing for the flush guarantee while
changing the order of proven-working teardown on no evidence. The committed
version preserves the original order exactly, and a test now pins it.

**Covering tests — `tests/test_shutdown_lifecycle.py`, 15 passed:**

| Test group | What it proves |
| --- | --- |
| Each step raising in turn (6 tests) | Every *later* step still runs. These are the red-green core |
| `db_writer` reachability | With `tts.shutdown()` raising, `db_writer.shutdown()` is **still called** — the exact data-loss path |
| Failure reporting | The returned list names the failed steps; a clean run returns `[]` |
| Order pinning | Asserts the original 7-step sequence, so a future reorder must be deliberate |
| `None` tolerance | Partially-constructed teardown (an early boot failure) does not raise |

**Red-green verified:** reverting to the unguarded `finally:` body fails 7 of
the 15. One test initially failed on a label format of my own making (expected
`"tts"`, implementation reports `"tts.shutdown"`) — matched on
`name.split('.')[0]`; the substantive assertion had passed all along.

### 3.10 D17 — `<name>.env` files were not classified secret · **VERIFIED**

**The defect.** `policy_rules.is_sensitive_parts()` special-cased env files by
exact name only:

```python
if name == ".env" or name.startswith(".env."):
```

So `.env` and `.env.local` were sensitive, but the equally common
`<name>.env` convention was **not**. This repository ships exactly such a file —
`ll.env`, which `.gitignore` lists precisely because it holds real
configuration. The security policy classified it as an ordinary document:

```
find_file  query=".env"                 -> blocked (sensitive_path)
find_file  query="ll.env"               -> ALLOWED
open_file  path="C:\project\ll.env"     -> ALLOWED
```

Two layers both missed it. `_looks_like_path("ll.env")` is `False` (no drive, no
separator, no `..`), so the generic check never treated the bare name as a path
at all; and when a full path *was* supplied, `sensitive_path()` returned `False`
because no pattern matched.

**The fix.** `_SECRET_FILE_PATTERNS` was widened by 13 patterns. The
justification is not my judgement of what looks secret — it is that **the repo's
own `.gitignore` was strictly broader than the runtime policy**, and `.gitignore`
is the best available statement of what this project considers secret. Each added
pattern closes a measured gap between the two, plus the standard
plaintext-credential files:

| Added | Why |
| --- | --- |
| `*.env` | The gap above. **`env` with no extension is deliberately *not* matched** — a directory called `env` is not a secret |
| `*.jks`, `*.keystore`, `*.cookies`, `cookies*.txt`, `session*.json` | Keystores, cookie jars, session dumps — all already gitignored |
| `.git-credentials`, `.netrc`, `_netrc`, `.npmrc`, `.pypirc` | Standard files holding plaintext tokens |
| `id_ecdsa*`, `id_dsa*` | SSH key types the existing `id_rsa*`/`id_ed25519*` entries missed |

**Covering tests — `tests/test_security_secret_files.py`, 39 passed:**

| Test group | Count | What it proves |
| --- | --- | --- |
| `test_secret_files_are_classified_sensitive` | 20 | Each newly-covered name *is* refused, including full Windows and POSIX paths |
| `test_still_not_sensitive` | 13 | **Over-matching guard.** `environment.txt`, `envelope.docx`, a directory named `env`, `sessions.md`, `my_cookies_recipe.txt`, `credentials.txt`, `tokenizer.py` all stay ordinary — widening a pattern list is exactly the change that starts refusing innocent user files |
| `test_env_files_are_refused_end_to_end` | 4 | Goes through `policy.validate_args("find_file", …)`, the layer the user actually feels, asserting `reason == "sensitive_path"` — not just the classifier in isolation |
| `test_full_paths_to_env_files_are_refused` | 1 | `open_file` with absolute paths |
| `test_policy_covers_everything_gitignore_calls_secret` | 1 | **Keeps the two lists honest about each other.** If someone adds a secret glob to `.gitignore`, this fails and names it, so the drift that caused D17 cannot recur silently |

**Regression sweep:** the full security/policy/taint selection runs **395
passed, 0 failed** — the widened list refuses nothing it previously allowed
except the intended targets.

**Not a secrets disclosure:** no value from `ll.env` or any other credential
file was read, logged, or reproduced at any point. The finding concerns *path
classification* only.

---

## 4. Defects found *during* the repair work

Neither was in the Phase 1 report; both surfaced while adding tests.

### 4.1 Shared thread pools destroyed by a test

`tests/test_planning.py::ConcurrencyAndTimingTests::test_shutdown_planning_executors_does_not_raise`
called `shutdown_planning_executors(wait=False)`, which shuts down
`planner._PLANNER_EXECUTOR` and `executor._CORRECTION_EXECUTOR`. Those are
**module-level pools shared by the entire pytest process**, and a
`ThreadPoolExecutor` cannot be restarted. Every later test that planned
anything died with:

```
RuntimeError: cannot schedule new futures after shutdown
```

It stayed green only by collection-order luck. Adding any test module after it
that plans exposed it — and the failure appeared in *that* module, far from its
cause. Fixed by restoring both pools via `addCleanup`.

### 4.2 A lazy import landing inside a timeout budget

`executor._audit_plan_results()` defers
`from sara.orchestrator.intent_handlers import _log_action` to call time, to
break the `dispatcher ↔ planning` import cycle. The first plan executed in a
process therefore imports all of `sara.orchestrator` — `core_wiring` → vision,
STT, TTS, and probes for ~7 optional native dependencies — **inside the plan's
total-timeout budget**. `test_execute_plan_all_succeed` failed with:

```
WARNING  Total plan timeout (6.0s) reached before step 1 of 2 could run.
```

purely because another module was collected after it.

**Production is unaffected** — the app imports `sara.orchestrator` during boot,
so the import is warm long before any plan runs. This is a test-determinism
problem, fixed with a session warm-up in the new `tests/conftest.py`. Side
benefit: `tests/test_planning.py` went from 7.6 s to 1.8 s, and the whole
suite from 40 s to 33 s.

---

## 5. Known-failing and unverified

### 5.1 ~~`ui_prefs_theme.test.js` — theme ordering~~ · **RESOLVED in batch 6a**

Fixed by moving `lime` in `theme.js`; see §3.7 item I. The node suite is now
47 passed / 0 failed, and three regression tests guard the three-way list.

### 5.1a CI front-end job · **CONFIGURED, RUN NOT CONFIRMED**

The `js-test` job was applied by the user and committed as `2ac8bbe`. Reviewed
as committed: `ci.yml` parses, jobs are `lint, test, js-test, type-check,
bench`, `js-test` is a real gate, and `lint`/`test` remain hard gates
(`bench`/`type-check` were already advisory before this change).

**One thing remains unverified**, and "configured" is not "passing":

1. **No GitHub Actions run has been observed.** Correctness by inspection is
   not evidence that the workflow succeeds on the runner. Nothing here
   exercises `actions/checkout@v4`, `actions/setup-node@v4`, the
   `ubuntu-latest` image, or the job's own shell loop.

The second item previously listed here — *"the job pins `node-version: "24"`;
all local runs used Node v22.23.4"* — is **resolved**. The user ran all three
suites on **Node v24.22.1** against `C:\project` (19, 11, 47 passing), matching
the Node 22 results exactly. The pinned major version is no longer untested.

### 5.2 Not verifiable from this environment

| Area | Why |
| --- | --- |
| `toggle_media_mute` runtime behaviour | pycaw / Core Audio / COM |
| WebView2 bridge binding on real Windows | needs the Windows renderer |
| Wake-word, STT, TTS pipelines | `faster-whisper`, `openwakeword`, `kokoro-onnx` absent |
| App launching / global hotkeys | Windows-only |
| Audio pipeline, model lifecycle, thread safety, memory leaks under load | not assessable by static analysis or unit tests |

No claim of Windows verification is made anywhere in this report.

### 5.3 Forward-compatibility

`sara/audio/stt/helpers.py:75` imports `audioop`, **removed** in Python 3.13.
Harmless on the pinned 3.11, but it will surface as a hard `ImportError` on any
interpreter upgrade, not a warning.

---

## 6. How to reproduce

These are the exact commands behind every number in §2.

```bash
# 1. Full Python suite
#    expect: 2134 collected, 0 failed
#    (2115 passed / 19 skipped here; 2110 / 24 if `keyboard` is absent -- see §1)
python -m pytest tests/ -q

# 2. CI lint gate -- expect: 0 real issues
python -m pyflakes sara/ tests/ main.py health_check.py \
  | grep -vE "imported but unused|unable to detect undefined names"

# 3. Front-end suites -- run them the way the CI job does, with `node <file>`.
#    `node --test` will NOT report these correctly: they are self-reporting
#    scripts with their own runner, not node:test suites.
for f in sara/gui/js/tests/*.test.js; do node "$f"; done
#    expect: 19 passed / 11 passed / 47 passed, 0 failed  (77 total)
#    confirmed on Node v22.23.4 (this env) and Node v24.22.1 (user, Windows)

# 4. Per-batch targeted suites
python -m pytest tests/test_gui_assets.py -q                  # 13 passed  (6a)
python -m pytest tests/test_intent_engine_rebuild_cost.py -q  #  9 passed  (6b1, D11)
python -m pytest tests/test_shutdown_lifecycle.py -q          # 15 passed  (6b2, D16)
python -m pytest tests/test_security_secret_files.py -q       # 39 passed  (6b3, D17)

# 5. Security regression sweep -- expect: 395 passed, 0 failed
python -m pytest tests/ -q -k "security or policy or taint"

# 6. Order-independence (the two bugs fixed in batch 5)
python -m pytest -q tests/test_planning.py tests/test_planning_gemini_backend.py
python -m pytest -q tests/test_planning_gemini_backend.py tests/test_planning.py
```

On Windows the 19 `@requires_windows` tests run instead of skipping, so expect
**19 fewer skips and 19 more passes** than the Linux figures above.

### 6.1 Dependencies needed to run the Python suite off Windows

Installed for the final run, on Python 3.11.17:

```bash
pip install pytest pyflakes dateparser python-dotenv rapidfuzz pyperclip \
            psutil requests beautifulsoup4 pytz numpy Pillow mss \
            google-genai ollama keyboard
```

`webview` is stubbed (pywebview's `proxy_tools` dependency will not build here).
`winsdk`, `pycaw`, `pyaudio`, `faster-whisper`, `webrtcvad`, `openwakeword`,
`sounddevice` and `kokoro-onnx` are absent — all Windows-only or native-audio,
and all import-guarded in the codebase.
