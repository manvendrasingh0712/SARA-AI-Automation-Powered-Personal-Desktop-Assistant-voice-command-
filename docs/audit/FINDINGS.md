# SARA AI — Findings Register

Every finding carries: severity, evidence, affected files, root cause, proposed
correction, implementation status, verification result.

**Severity scale** — *Critical*: data loss, security, or the app not starting ·
*High*: a user-visible feature is broken or silently lies · *Medium*: real
defect with a workaround, or a gap that hides defects · *Low*: hygiene.

**Status** — `FIXED` (applied and verified) · `BLOCKED` (cannot be done from
this session) · `OPEN` (not yet addressed) · `WONTFIX` (deliberate).

Last updated: 2026-10-29 · repo HEAD `2ac8bbe` · mirror `c1d48d9`

### Withdrawn figures — read this before quoting any number below

Two numbers recur in earlier sections and **both are wrong**. They are left
in place so the reasoning that produced them stays auditable, but nothing
should be decided from them.

| figure | appears as | the correction |
| --- | --- | --- |
| **288 ms per command** | the endpoint fast-path saving (D51, and quoted as motivation in D46/D52) | **withdrawn (D72).** A late partial *is* used, and against the base that repeated short commands produce the ceiling on the whole saving is **128 ms**, with 4 of 7 measured previews worth nothing |
| **base = 625 ms** | the endpoint's fallback deadline | **never occurs (D72).** `_endpoint_open()` passes a base from `_SilenceGate`: **800 ms** fresh, **450 ms** after short commands, 900 ms after dictation. 625 ms is the `base_ms=None` fallback only |

Both errors share one cause, and so does **D71**: a number read from a
default instead of from the running system. Where a figure here is load
bearing it now names its source.

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

## D19 — three unrelated intents all mis-route to `unmute`

**Severity:** Medium. Found during the voice-architecture research pass
(2026-10-28) while reading the existing benchmark's confusion matrix, not by
running anything new.

**Evidence.** `bench/results/latest.json` (590 rows, run 2026-10-10, recorded
in `metrics.confusions`):

```
bluetooth_on                -> unmute   (2)
open_airplane_mode_settings -> unmute   (2)
restore_windows             -> unmute   (2)
```

Three semantically distant intents collapsing onto the *same* wrong target is
not the shape of random classifier error; it is the shape of a pattern-overlap
bug in the intent table — most likely an `unmute` pattern broad enough to
swallow tokens the other three share.

A second, separate cluster falls back to `chat`: `open_app` (3), `calculator`
(2), `minimize_active_window` (2), `paste_clipboard` (2), `set_volume` (2),
`typing_text` (2). That one is a different failure — a gate declining rather
than a pattern colliding — and should be investigated independently.

**Why it matters beyond accuracy.** `unmute` is harmless, so the user-visible
symptom is mild. But the same overlap mechanism applied to a tier-escalated
intent would route a benign command to a consequential action. The 20.83%
`false_trigger_adversarial` rate in the same run suggests pattern breadth is a
systemic issue in the table, not a one-off.

**Correction.** Isolate the overlapping `unmute` pattern; add a red-green test
per confused pair; re-run `python -m bench.run_bench --mode router --compare
bench/baseline.json` and require accuracy ≥ 93.22% with the confusions gone.

**Root cause (batch C, 2026-10-28).** `sara/core/intent/patterns.py:322`:

```python
("unmute", [
    r"(?:unmute|restore|enable)(?: (?:the )?(?:volume|sound|audio|mic|microphone))?",
]),
```

The audio object was **optional for every alternative** (trailing `?`), so the
pattern reduced to a bare match on `unmute`, `restore` **or** `enable`. Both
`restore` and `enable` are generic verbs. Reproduced deterministically against
the real engine before the fix — all six golden utterances returned `unmute`.

**Fix applied.** Bare `unmute` kept (unambiguous); `restore`/`enable` now
require the audio object, which is the only reading in which they mean unmute.
Zero capturing groups preserved, so the pattern stays mergeable by
`engine._merge_groupless()` and the D11 startup win is unaffected.

**Verified.** `tests/test_intent_pattern_scoping.py` — red-green: 14 tests fail
on the original patterns, 42 pass after. On bench's 590 rows:

| metric | before | after |
| --- | --- | --- |
| accuracy | 93.22% | 94.58% |
| **false_trigger_adversarial** | **20.83%** | **12.50%** |
| hinglish / negative | 100% / 100% | 100% / 100% (held) |

The adversarial false-trigger halving is the important part: generic verbs are
exactly what adversarial input contains, so one over-broad pattern accounted for
roughly 40% of the worst safety number in the audit.

**Status:** **FIXED** in batch C. The separate `-> chat` cluster noted above is
a different defect class (missed commands, not collisions) and is split out as
**D21**.

---

## D20 — the wake acknowledgement blocks the loop, contrary to its own comment

**Severity:** Medium-High for *perceived* latency — it is paid on every single
wake cycle, before SARA starts listening. Found 2026-10-28 while tracing the
voice loop for `LIVE_ENGINE_ARCHITECTURE.md`.

**Evidence.** `sara/orchestrator/core_wiring.py:781–790`. The comment
immediately above the call states the intended behaviour and its value:

> When AEC is actively cancelling echo (`aec_active`, computed above), fire the
> ack asynchronously (`block=False`) so `ears.wait_settle()`/`listen()` below
> start immediately instead of waiting for playback to finish — **this is what
> actually removes the fixed 1-1.5s tax per wake cycle.**

The call does the opposite, unconditionally:

```python
ack = getattr(Config, "WAKE_ACK_PHRASE", "Yes?")
try:
    tts.speak(ack, fast=True, block=True)     # core_wiring.py:790
```

Three links verified, so this is not a misreading:

1. `tts` in `run_sara_logic` is the `TTSWorker` built at `core_wiring.py:249`
   (`tts = TTSWorker(voice, ears, db)`), not the raw `TextToSpeech`.
2. `TTSWorker.speak(self, text, fast=False, block=True, priority=1)` creates a
   `done_event` and, when `block`, calls `done_event.wait()`
   (`tts_worker.py:326–333`) — it waits for synthesis **and** playback.
3. `aec_active` **is** computed (`core_wiring.py:713`) and **is** used — but
   only to pick `post_tts_settle_s` (0.3 s with AEC vs 1.3 s without,
   `core_wiring.py:717–721`). It is never consulted for the ack.

So the designed optimisation exists only in the comment. Note that
`block=True` is also the parameter's default, so simply dropping the argument
would change nothing — the fix must pass `block=not aec_active`.

**Honest sizing.** The comment claims 1–1.5 s. The TTS phrase cache makes
synthesis of a repeated short phrase ≈ 0 after the first cycle, so the standing
cost is most likely playback of "Yes?" plus queue latency — estimated 0.4–0.6 s,
not 1–1.5 s. The comment may predate the cache. **This is an estimate, not a
measurement**; `telemetry`'s existing `ttfa_ms` captures exactly this interval,
so it can be measured on the target machine without new instrumentation.

**Why it matters.** A fixed delay *before listening begins* is felt on every
interaction, independent of model or network latency. It is a strong candidate
for part of the "feels slower than the reference assistant" complaint that
prompted the voice-architecture work, and it is independent of any migration.

**Correction.** Pass `block=not aec_active` at `core_wiring.py:790`, with a
test asserting the ack is non-blocking when AEC is active and blocking when it
is not (the no-AEC case must keep blocking, or the mic picks up the ack and STT
misfires on it — the reason the original blocking call was correct before AEC
existed). Measure `ttfa_p50` before and after.

**Fix applied (batch B, 2026-10-28).** `core_wiring.py` now passes
`block=not aec_active`, implementing what the comment always described. The
no-AEC branch still blocks, deliberately.

**Verified.** `tests/test_wake_ack_blocking.py`, 7 tests — red-green: 2 fail
with `block=True` restored, all 7 pass after. The tests pin the conditional, the
continued existence of `aec_active` in `run_sara_logic`'s scope, that
`TTSWorker.speak`'s `block` default is still `True` (which is why the argument
must be explicit), and `block=True`/`block=False` queue semantics driven against
a real `TTSWorker` with the queue consumed by the test — no audio device needed.

**NOT measured.** The latency saving needs real playback on Windows. Telemetry's
existing `ttfa_ms` covers exactly this interval; see `BASELINE_A.md` for how to
capture it. No timing claim is made.

**Status:** **FIXED** in batch B, pending on-device measurement of the saving.

---

## D21 — three commands missed because an optional group accepted only one word

**Severity:** Medium. Split out of D19's `-> chat` cluster (a different defect
class: missed commands, not collisions). Found 2026-10-28, fixed in batch C.

**Evidence.** Three patterns each allowed exactly one word where natural speech
supplies two, so the command fell through to `chat`. For all three
`has_probable_tool_intent()` is `False`, so **no later stage recovered them** —
unlike `calculator` and `screenshot_describe`, whose gate is `True` and which
the LLM tool-router rescues at the cost of a round trip (~922 ms p50, per
`BASELINE_A.md`).

| Intent | Original pattern | Missed |
| --- | --- | --- |
| `paste_clipboard` | `paste(?: (?:it\|this\|that\|here))?$` | "paste it here" — two of them |
| `minimize_active_window` | `minimize (?:this\|the\|current) window` | "minimize the current window" — determiner **and** qualifier |
| `typing_text` | `type (?:this )?(?:for me)?[:\s]+(.+)` | "type this: see you tomorrow" |

The `typing_text` case is the subtle one. The literal `type ` already consumes
the only space, so `[:\s]+` demanded a **second** separator; and `(?:this )?`
requires "this" followed by a space, while the text has `this:`. So it matched
empty and `[:\s]+` then failed on `t`. Only `"type: X"` or `"type  X"` (double
space) ever worked.

**Fix applied.** Determiner and qualifier split into independent optional
groups for the first two. For `typing_text` the colon is now **mandatory** —
matching bare `type <anything>` would capture the rest of an ordinary sentence
as dictation ("what **type** of file is this"), which would be a fresh D19. The
sibling `write` pattern already requires a colon for exactly this reason, and
its comment says so. `"type X"` without a colon therefore stays unmatched by
design.

**Verified.** Same test file, including a 9-case over-match guard pairing every
widening with the ordinary-chat sentences it could plausibly have swallowed, and
capture-group assertions (a pattern that matches but captures the wrong span
would type the command words too — worse than a miss, because it is silent).

Bench, 590 rows, cumulative with D19:

| metric | baseline | after B+C |
| --- | --- | --- |
| accuracy | 93.22% | **95.25%** |
| macro_f1 | 94.72% | **96.85%** |
| false_trigger_rate | 5.95% | **3.57%** |
| false_trigger_adversarial | 20.83% | **12.50%** |
| group_accuracy | 81.08% | **82.43%** |
| en / hi / hinglish | 89.32 / 95.70 / 100 | **92.88** / 95.70 / **100** |
| adversarial / negative | 67.50 / 100 | **72.50** / 100 |

No metric regressed.

**Status:** **FIXED** in batch C.

---

## D22 — the remaining bench misses are an ASR-robustness feature, not a bug

**Severity:** Low as a defect; **Medium as a product gap.** Recorded so the
remaining 28 mismatches are not mistaken for pattern bugs.

**Evidence.** After B+C, 28 of 590 rows still mis-route. Classified by reading
every one:

| Class | Count | Example | Nature |
| --- | --- | --- | --- |
| Simulated ASR noise / homophones | ~14 | "snap the window to the **write**", "**paws** the music", "paste it **hear**", "set brightness **two** 70" | The golden set labels these `"noisy from …"`. Fixing them means homophone normalisation |
| Interior filler words | 2 | "uh open **um** notepad" | Leading fillers are handled; interior ones are not |
| Mixed-script commands | 3 | "chrome खोलो", "क्रोम खोलो", "नोटपैड बंद करो" | English app name with Devanagari verb |
| Recovered by the tool router | 2 | "calculate 245 * 12", "take screenshot" | `detect_intent` → `chat`, but gate is `True`; works in production via an LLM round trip |
| Deliberate known misses | 1 | "volume up" — annotated `"known miss"` | Aspirational target in the set itself |
| Substring guards working | 3 | "**top** of the morning to you" → must stay chat | Already correct; these are the set testing its own guards |

**Why this is not a batch-C fix.** Each class needs a normalisation layer
(homophone map, filler stripping, script-aware verb matching), not a pattern
tweak. Widening patterns to absorb them is precisely how D19 was created. The
mixed-script class is the most interesting for the Hinglish goal and deserves
its own batch with its own over-match guard.

**Status:** OPEN, classified. The mixed-script class was taken out of this
finding and fixed as **D23**; the homophone and filler classes remain open.

---

## D23 — no generic Devanagari `open_app` / `close_app` existed

**Severity:** Medium-High for the stated Hinglish requirement. Taken out of
D22's mixed-script class and fixed 2026-10-28.

**Evidence.** `H_OPEN` and `H_CLOSE` were defined in
`patterns_multilingual.py:59–60` but wired **only to specific named targets** —
wifi, bluetooth, tabs, downloads, task manager, file explorer. There was no
generic Devanagari `open_app`/`close_app` at all, so all of these fell through
to `chat`:

```
chrome खोलो        (ASCII app name + Devanagari verb -- the common real case)
क्रोम खोलो
नोटपैड बंद करो
chrome खोल दो
```

while the romanised equivalents worked (`chrome kholo` → `open_app('chrome')`).
The cause: the romanised pattern's app-name capture is `[A-Za-z0-9...]`, ASCII
only, and its verb alternation contains no Devanagari.

**Fix applied.** Two `_hadd` patterns, deliberately mirroring the romanised
`_add("open_app")` shape rather than inventing a looser one: anchored `^…$` so
the whole utterance must be `<name> <verb>`; app name at most 3 tokens,
non-greedy; exactly one capturing group, which is the contract
`engine._captured_app_name_is_rejected()` depends on.

**And the layer-4 guard was extended first.** `_NON_APP_TOKENS` held only
**romanised** function words, so a Devanagari free capture would have been
unprotected. Devanagari equivalents (करना, है, कुछ, को, में, मेरा, काम, …) were
added to `patterns.py` before the patterns that need them.

**Ordering verified, not assumed:** `open_app` is index 138 and `close_app` 139
in `_INTENT_PATTERNS`, after every specific target (`close_active_window` 55,
`new_tab` 80, `close_tab` 81, `open_downloads` 96, `open_file_explorer` 104,
`open_task_manager` 106), so "टास्क मैनेजर खोलो" still resolves to
`open_task_manager`. A test pins that ordering directly.

**Verified.** `tests/test_hinglish_app_commands.py` — 8 positive cases with
capture assertions, 5 specific-target precedence cases, an ordering test, a
6-case ordinary-Devanagari-chat over-match guard, and a test that
`_NON_APP_TOKENS` covers Devanagari. Bench `hi` accuracy **95.70% → 98.92%**,
`hinglish` held at 100%, `negative` held at 100%.

**Status:** **FIXED.**

---

## D24 — `^spotify (.+)$` captured command verbs as track names

**Severity:** Medium-High — it produced a **wrong action**, not a wrong label.
Found while testing D23; pre-existing and script-independent.

**Evidence.** `patterns.py` `play_spotify` carried a bare catch-all, so anything
beginning `spotify ` had the remainder captured as a song title:

```
"spotify close karo"   -> play_spotify, track "close karo"
"spotify band kar do"  -> play_spotify, track "band kar do"
"spotify बंद करो"       -> play_spotify, track "बंद करो"
"spotify close karo"   (English too) -> play_spotify, track "close karo"
```

`play_spotify` is index 30 and `close_app` 139, so it always won. **Asking SARA
to close Spotify started music instead.**

This is the same defect shape the `press_key` entry in the same file already
records removing — "a bare catch-all with no end marker at all". There the
pattern was deleted; here the shorthand is worth keeping ("spotify bohemian
rhapsody" is a real phrasing), so a negative lookahead now refuses
window/app command verbs in English, romanised Hinglish and Devanagari.

**Verified.** Same test file: 6 cases asserting the command forms no longer
reach `play_spotify`, and 4 asserting the legitimate shorthand still plays with
the correct capture — because a fix that silently cost the shorthand would be a
regression of its own.

**Status:** **FIXED.**

---

## D26 — `scroll_top` / `scroll_bottom` / `time_query` fired on ordinary prose

**Severity:** High. These were **false triggers during normal conversation** —
SARA scrolled, or answered the clock, mid-sentence.

**Evidence.** Verb optional *and* object optional, so each reduced to a bare
keyword:

```
scroll_top     r"(?:go to |scroll to )?(?:the )?\btop\b(?: of (?:the )?page)?"
scroll_bottom  r"(?:go to |scroll to )?(?:the )?\bbottom\b(?: of (?:the )?page)?"
time_query     r"what(?:'?s| is) (?:the )?(?:current )?time"      # no end anchor
```

```
"bottom line of the report is we lost money" -> scroll_bottom
"top of the morning to you"                  -> scroll_top
"what is the time complexity of quicksort"   -> time_query
```

The golden set carried all three with notes stating they must not fire, so
this was known-wrong behaviour that had never been traced to a cause.

**Fix.** At least one of verb or object is now required for the two scroll
intents; `time_query` must end after "time" (optionally "now"). `"go two bottom
of the page"` — a to/two homophone row that previously passed on the bare match
— still passes on the object-bearing pattern.

**Verified.** `tests/test_intent_gates_and_normalization.py`. Fixing these took
**`false_trigger_adversarial` to 0.0000** (20.83% at audit start) and
**`false_trigger_rate` to 0.0000**.

**Status:** **FIXED.**

---

## D27 — a multilingual gate silently disabled the entire English calculator

**Severity:** High. A whole feature was dead in one language.

**Evidence.** `calculator` is **deliberately ungated** —
`engine.register_intent()`'s docstring says a gate may be omitted "only if no
safe substring gate exists for this intent (matches the existing `calculator`
convention in patterns.py — the one remaining intent with no gate)", because no
substring expresses "contains arithmetic".

`patterns_multilingual.apply()` then did:

```python
elif gate:
    intent_gates[intent] = tuple(g.lower() for g in gate)
```

so adding Hinglish rows with a `("kitna", "kya")` gate **created** a gate where
the design requires none:

```
"calculate 245 * 12" -> chat        "25 * 4 kitna hota hai" -> calculator
"what is 5 + 3"      -> chat        "5 + 3 kitna hai"       -> calculator
"compute 100 / 4"    -> chat
"solve 12 * 12"      -> chat
```

The regexes matched throughout — only the pre-filter blocked them, which is why
this presented as a pattern problem and was not one.

**Fix at the root cause**, with the blast radius measured first: an AST diff of
the base `_INTENT_GATES` literal against the post-merge dict showed
`calculator` is the **only** intent that gained a gate this way, so
`_BASE_INTENTS_WITHOUT_GATE` restores the documented convention and changes
nothing else.

**Verified.** 8 forms across English/Hinglish/Devanagari route correctly; 5
guards confirm the now-ungated patterns do not fire on ordinary number-bearing
text (`"room 12-15 is free"`, `"i work a 9-5 job"`, `"call me on 555-1234"`).

**Status:** **FIXED.**

---

## D28 — filler words

**Severity:** Medium. `_FILLER` carried politeness words but no speech
disfluencies, and was applied to only six intents.

**Evidence.** `"um restart chrome"` and `"uh switch to spotify"` missed while
`"please restart chrome"` worked. `"uh open um notepad"` matched but captured
`"um notepad"` as the app name. `"uh open pc"` and `"please what about
tomorrow"` missed because their intents never used `_FILLER` at all.

**Fix.** Disfluencies added to `_FILLER`; an optional post-verb slot added so an
interior filler cannot land in the app-name capture; `_FILLER` applied to the
two intents in the evidence. `open_this_pc` also needed its **gate** widened —
the same shape as D27, a gate narrower than its own patterns, so the widened
pattern was still never reached.

**Verified.** 8 capture-asserting cases, plus `"open umbrella"` to prove a real
app name beginning like a filler is not over-stripped, and
`"my computer is slow"` / `"this pc has a problem"` against the widened gate.

**Status:** **FIXED.**

---

## D29 — homophones and ASR-split compounds

**Severity:** Medium. The largest single remaining class in D22.

**Evidence.** `_correct_typos()`'s docstring already claims "typo/**homophone**
correction", but the mechanism cannot do it: `_TYPO_MIN_WORD_LEN = 5` skips
`two` (3), `hear` (4), `paws` (4), `no` (2) outright, and `difflib` compares
spelling while homophones differ in spelling by definition. So eight golden rows
failed:

```
"switch two study mode"  "set brightness two 70"  "snap window two the left"
"snap the window to the write"  "paste it hear"  "paws the music"
"forget everything you no about me"  "turn off wi fi"
```

**Fix.** An explicit map (`_HOMOPHONES`) plus whole-string repair for compounds
ASR splits (`_SPLIT_COMPOUNDS`), both applied inside `_correct_typos`.

**The safety property is structural, not a matter of the map being cautious.**
`_correct_typos` runs **only** as a second attempt after the exact-spelling pass
found no intent, so no command that works today can reach it. A test pins this
using `"write: hello there"` — `write` is both a homophone key and a command
word, and it must keep matching on the exact pass.

**Verified.** All 8 rescued; 8 ordinary-chat sentences containing those words
(`"i have two options"`, `"write me a poem"`, `"no thanks"`, …) stay `chat`.

**Status:** **FIXED.**

---

## D30 — no STT warm-up existed, so the first spoken command paid first-run cost

**Severity:** Medium-High for perceived latency, at the worst possible moment.

**Evidence.** `sara/audio/tts/prewarm.py` pre-synthesises TTS phrases. A grep
for `warm`/`prewarm` across `sara/audio/stt/engine.py` returned **nothing** —
there was no STT equivalent anywhere. Loading a model and running one are
different costs: `__init__` paid the load (three models, in parallel), but
CTranslate2's first-run kernel/graph bring-up was charged to whatever the user
said first.

The reference project measured this shape directly: model load 1.4 s, **first
transcription 8.5 s**, subsequent 1.1 s; one throwaway recognition on silence
took it to 1.16 s. Their note on why it matters is the right one — someone who
waits several seconds assumes it is broken and repeats themselves, and now two
turns overlap.

**Fix.** `_warm_recognisers()` runs one throwaway transcription per loaded
model. **Both** are warmed: `_whisper_model` runs the command, and the tiny
`_wake_whisper_model` serves the live-caption preview and the STT wake-word
fallback, so both are on the first command's path.

Two details that would each have made it a silent no-op or a hazard:

- `_transcribe()` discards clips under **0.20 s**, so the obvious 0.10 s of
  silence warms nothing. The clip is 0.25 s with a floor, and a test asserts it.
- **Placement is the safety argument.** It runs inside `__init__` after the
  models are assigned and *before* `_open_mic_stream()` / `_start_threads()`. At
  that point no caller holds a reference, so the models cannot be used
  concurrently — no lock needed, and a real command can never be delayed. A
  background thread would have raced the orchestrator's first `listen()` on the
  same model object, which faster-whisper does not promise is safe.

Config: `STT_WARMUP_ENABLED` / `STT_WARMUP_SECONDS`, declared as real `Config`
fields rather than `getattr`-with-default — the audit already recorded that
pattern as "looked configurable via .env but silently was not".

**NOT MEASURED.** faster-whisper cannot be installed in this environment, so the
saving is not measured and is **not claimed**. `telemetry`'s `stt_ms` on the
first turn of a session is the instrument; `python -m bench.voice_latency` reads
it. 9 contract tests assert wiring, the disable flag, the 0.20 s floor, both
models warmed, independent guarding, absent-model tolerance, and the ordering.

**Status:** **FIXED, saving unmeasured pending a run on the Windows machine.**

---

## D31 — the streaming first-chunk path was implemented, tested and switched off

**Severity:** Medium for latency, **Medium-High for intelligibility.** Fixed
2026-10-28 (batch D22-c).

**What `tts_start_ms` actually measures**, established before acting on it:

```python
tts_from     = first present of (llm_first_token, tool_end, route_done)
tts_start_ms = tts_first_audio − tts_from
```

So on a streamed reply it is **not synthesis time** — it includes waiting for
enough streamed text to form the first chunk. The telemetry reading of
**1250 ms p50 was n=5**, the bare minimum for a percentile, and is treated as
preliminary throughout.

**Evidence.** `VOICE_CHUNKER_V2` — label "Faster first words" — was
`default=False` in both `voice_flags.py` and `config.py`, while
`chunker.first_chunk_end()` and `tests/test_tts_chunker.py` were already
written. Each of 11 representative replies (English, Hindi, Hinglish, numbers,
abbreviations, URLs, lists) was streamed word by word through the **real**
decision code in `engine._chunker_worker`, counting characters consumed before
the first chunk reached the synthesis queue:

| | legacy | v2 |
| --- | --- | --- |
| mean chars → first audio | 51.0 | 46.5 |
| **no early audio at all** | **4 of 11** | **0 of 11** |
| **defective first chunk** | **3** | **0** |

**The mean is not the justification — 4.5 characters is noise.** The two real
findings are:

1. **Legacy produced no early audio at all for 4 of 11 replies.** Its rule needs
   a strong terminator, or a soft one only after 35 characters, and has **no
   word-boundary fallback** — so a reply without mid-text punctuation pushes
   nothing and falls through to `_split_adaptive`, which also pushes nothing
   until it finds more than one segment. The user hears silence until the whole
   reply has streamed. v2's word boundary at 60 chars bounds it: 131 → 60 on one
   sample, 87 → 60 on another, 82 → 21 on a list.
2. **Legacy's early flushes were sometimes defective — which is exactly why it
   looked faster on the mean.** It emitted `'Dr.'` as a complete first
   utterance, cut `'The total comes to 1,234.'` inside the number, and cut
   `'You can read it at https://example.'` inside the URL. v2 refuses all three.

Three of the four cases where v2 is *later* are those defects.

**The one honest regression:** Hinglish `"Haan bilkul, …"` flushes at 60 chars
instead of 38, because `chunker.CLAUSE_MIN_CHARS` is 25 and `"Haan bilkul,"` is
12. That knob is **deliberately untouched** — a 12-character first utterance
followed by a synthesis gap may sound worse than waiting, and that is a
judgement for the ear on real hardware, not a guess from a character count.

**`TTS_FIRST_CHUNK_MIN_CHARS` was investigated and deliberately not changed.**
With v2 on, `_chunker_worker`'s v2 branch either `continue`s or sets
`first_pushed` before the `_FIRST_TRIGGER` check is reached, so that value is
**bypassed for the first chunk entirely**. Tuning it would have changed nothing.
A test pins the ordering so the claim cannot go stale.

**Fix.** Three functional lines: the `Config` default, the registry default, and
the expected-defaults tripwire in `tests/test_voice_flags.py`. Both defaults had
to move together — `get_flag()` reads `Config` while `reset_flags()` writes the
registry value back, so a mismatch would make "reset to defaults" silently
change behaviour.

**Verified.** `tests/test_tts_first_chunk_latency.py`, 15 tests, red-green (3
fail with the flag reverted). The benchmark is encoded **as** the tests, so the
justification stays checkable: the no-early-audio samples, the three defective
cuts, mid-token guards for numbers/URLs/e-mail/virama, the `'Dr.'`
empty-speech guard, short replies not chopped, and — for item-4 safety — that
the v2 branch still honours `self._stop`, still routes through
`_stop_aware_put`, and still falls back to the legacy splitter if
`first_chunk_end()` raises.

**NOT MEASURED: milliseconds.** These are character counts. No audio was
synthesised (Kokoro is unavailable in this environment) and no latency figure is
claimed. Characters map to time only through the model's token rate, so the real
saving must be read from `tts_start_ms` via `python -m bench.voice_latency` on
the Windows machine.

**Status:** **FIXED, millisecond saving unmeasured.**

---

## D32 — the entire adaptive-endpoint subsystem is inert at runtime

**Severity:** High — it affects both end-of-utterance latency and Hinglish
accuracy. Found 2026-10-28 while scoping D31. **Not yet fixed**, because it is
STT-side and belongs in its own batch.

**Evidence.** `sara/audio/stt/engine.py:1271`:

```python
def _endpoint_open(self, ...) -> Optional[dict]:
    """Per-utterance adaptive-endpoint state, or None (flag off -> legacy rule)."""
    if not getattr(Config, "VOICE_ENDPOINT_ADAPTIVE", False):
        return None
```

`VOICE_ENDPOINT_ADAPTIVE` is `default=False`. Its label is "Smarter
end-of-speech detection — **answers sooner after a finished command** and waits
longer when you pause mid-sentence, in English, Hindi and Hinglish."

With it off, `_endpoint_open()` returns `None` and SARA falls back to the legacy
fixed-silence rule. That makes **all of the following dead code at runtime**:

- `sara/audio/stt/endpoint.py` (223 lines) — `decide_endpoint`,
  `required_silence_ms`, `EarlyCutGuard`, `is_complete_command`
- `sara/audio/stt/endpoint_cues.py` (83 lines) — including the romanized
  Hinglish continuation cues (`aur`, `phir`, `ke liye`, `matlab`, `toh`, …) and
  the Devanagari set
- `Config.ENDPOINT_MIN_MS` (350), `ENDPOINT_MAX_MS` (900),
  `ENDPOINT_CONTINUATION_EXTRA_MS` (400), `ENDPOINT_NOISE_EXTRA_MS` (150),
  `ENDPOINT_EARLY_CUT_WINDOW_MS`, `ENDPOINT_EARLY_CUT_BACKOFF_MS`

An earlier audit pass praised the cue-aware endpointer as more linguistically
informed than the reference project's pure-energy VAD. That assessment was of
code that **is not currently running**, and this finding corrects it.

**Why this is its own batch.** End-of-utterance detection is the single largest
irreducible cost in the voice path, so enabling it is high value — but the risk
is the opposite of D31's: an endpointer that fires too early **cuts the user off
mid-sentence**, which is worse than waiting. It needs its own investigation of
the legacy rule it replaces, its own over-cut guards, and separation from the
TTS change so a measured difference stays attributable to one cause.

**Status:** FIXED — closed by **D41**, which flipped
`VOICE_ENDPOINT_ADAPTIVE` to True once D39 made the endpoint reachable at
all. (This line read OPEN for a while after the work was done; see the
*Status reconciliation* section near the end.)

---

## D37 — the `Ready` line reported a hardcoded compute device

`_log_init` printed:

```python
f"FasterWhisper={'✓ (CPU INT8)' if self._whisper_model else '✗'} | "
```

`(CPU INT8)` was a **literal**, not an observation. It printed on every
successful load whatever device the model was on. On the target machine the
loader printed

```
[STT] ✅ Faster Whisper loaded on GPU (CUDA, int8_float16).
```

and two lines later the status line said `CPU INT8`. Both cannot be true,
and nothing in SARA's output distinguished them — so the question "is GPU
acceleration actually running" was unanswerable from the logs.

A grep for `_model_device|_compute_type` found only the literal
`device="cuda"` / `device="cpu"` call-site arguments: **the accepted device
was never recorded anywhere**. The load line was therefore the truthful one
and the `Ready` line was simply a wrong label. The model does load on GPU.

**Fix.** Both accepted-load sites record what they accepted —
`self._model_device = f"{model_size} on GPU (CUDA, int8_float16)"` in the
GPU branch, `f"{model_size} on CPU (int8, {cpu_threads} threads)"` in the
CPU fallback — and `_log_init` reports it via
`getattr(self, "_model_device", "loaded, device unknown")`. An unrecorded
device renders as `unknown` rather than as a plausible device name: an
absent measurement and a measured one must never look the same.

**Tests.** 4 in `tests/test_stt_model_load.py` — the literal cannot return,
both branches must record, the two strings must not be interchangeable, and
an unrecorded device must render as unknown.

**Status:** FIXED (mirror). Needs the Windows `Ready` line to confirm.

---

## D38 — the diagnostic could not see the stage the application fails in (HYPOTHESIS)

Stages 1–6 of `bench/stt_doctor.py` all passed on the target machine,
ending in `TRANSCRIPT: 'Can you hear me? Hello. Open Chrome.'` at 219 ms.
That does **not** prove the application works, and the reason is
structural: stage 6 fed a pre-captured buffer straight to `_transcribe()`.
The application calls `ears.listen(mode="command")`, which first runs
`_collect_speech()` — the energy gate, the rolling-average continuation
test, silence counting and the endpoint decision. The diagnostic bypassed
all of it.

The continuation test (`engine.py` ~1512) is:

```python
is_voice = self._vad.is_speech(chunk) or (
    sum(energy_window) / len(energy_window) > thr * 0.3
)
```

With `BARGE_IN_ENERGY_THRESHOLD = 600` the silence gate is **180**. Room
noise measured across the user's four doctor runs was RMS 77, 85, 127 and
**203**. At 203 the average never falls below 180, so `silence_count` never
increments (`engine.py:1698`), `ended = silence_count >= silence_limit_n`
(line 1701) never fires, and capture runs to `max_duration = 12.0 s` —
every time, on every command. The variance across runs predicts
*intermittent* 12-second hangs, which matches "SARA does not reliably hear
me" better than a hard failure does.

This is a hypothesis, not a finding. Per the instruction not to retune
thresholds without evidence, **no threshold was changed**. Instead the
diagnostic was extended to measure it:

* **stage 4** now prints the silence gate next to measured room noise and
  fails when noise ≥ gate.
* **stage 7** calls the real `ears.listen(mode="command")` and reports
  elapsed time; ≥ 11.5 s means the endpoint never fired.
* **stage 8** pushes the transcript through the real `detect_intent()`,
  closing P1's "do transcripts reach the router" question.

If stage 7 returns in ~2 s the hypothesis is wrong and the endpointing is
fine. If it returns at 12 s, the principled fix is **D32** (enable the
adaptive endpoint subsystem, which calibrates the gate to measured room
noise instead of a fixed constant) rather than hand-tuning
`BARGE_IN_ENERGY_THRESHOLD` — and that needs its own batch with over-cut
guards.

**Status:** CLOSED — the instrumentation ran and **disproved the room-noise
explanation above**. Stage 4 *passed* in the failing run (noise 125 against a
gate of 180, 1.4× clear) and stage 7 still hit the ceiling: `audio=12.26s`,
`listen()` 14.83 s, transcript `'こんにちは!'`, routed to `chat`. The energy
term was healthy and simply never got a vote. Real mechanism in **D39**; the
instrumentation stays, since it is what produced the measurement.

---

## D39 — a permissive VAD made end-of-speech detection structurally impossible

**The measurement that found it** (`bench/stt_doctor.py` stage 7, target
machine):

```
[STTDiag] listen completed | mode=command | audio=12.26s | text='こんにちは!'
listen() returned after 14.83s
[FAIL] end of speech detected -- ran all the way to the 12s ceiling
```

The user said "open chrome" and stopped. Capture ran the full
`max_duration = 12.0 s`, Whisper hallucinated Japanese out of eleven seconds
of room noise, and the result routed to `chat` instead of `open_app` — so the
command never executed. That is what "SARA doesn't hear me" looked like from
outside: not a deaf microphone, a capture that could not end.

**The mechanism.** `_collect_speech`'s continuation test was:

```python
is_voice = self._vad.is_speech(chunk) or (
    sum(energy_window) / len(energy_window) > thr * 0.3
)
```

webrtcvad at aggressiveness 2 calls a quiet room speech most of the time.
Measured with the same `_VADFilter` over the same 1024-byte chunks this loop
uses — `stt_doctor.py` stage 5 is literally this `is_speech()` call —
**74/123 and 112/123 chunks voiced on 4-second clips holding roughly one
second of speech.**

Under `or` that is fatal, and silent: `is_voice` never goes `False`, so
`silence_count += 1` is unreachable, so `silence_count >= silence_limit_n`
(`engine.py:1701`) is unreachable, and the only remaining exit is the 12 s
ceiling. The defect is not a mistuned threshold — the threshold was correct
and measured healthy in the failing run. It is a **correct signal placed
where a noisier one could always override it**.

**Fix.** Energy decides the continuation test; VAD is removed from it.
Justification, all measured rather than assumed:

* Energy separates speech from a quiet room here with 13× of headroom
  (speech RMS 2337–2660 against a 180 gate).
* Dropping VAD costs no sensitivity: the **start** gate is `thr * 0.35`
  (210) and continuation is `thr * 0.30` (180), so anything loud enough to
  begin a capture sustains one comfortably.
* Where webrtcvad is absent, `_VADFilter.is_speech()` already returned
  `False` unconditionally, so that configuration was energy-only before this
  change and is untouched by it.
* The **start** gate deliberately keeps its `or`. It is meant to be
  sensitive, and a false start is cheap again once a capture can end.

**Tests.** 9 new in `tests/test_endpoint_vad_veto.py`: the structural guards
(VAD must not return to the continuation test; the start gate must keep it;
continuation must stay the looser of the two thresholds) plus a replay of the
decision over the exact measured input, which asserts the old rule hangs and
the new one fires in ~0.9 s.

**Harness correction.** 8 tests in `tests/test_stt_endpoint_integration.py`
failed on this change, because that harness set `energy_threshold = 1e9` and
marked voice with a fake VAD — i.e. it encoded "VAD alone sustains a
capture" as intended behaviour. Those tests are about *when* the endpoint
fires, not about which signal detects voice, so the chunks now carry a
realistic amplitude (RMS 2500) against SARA's real default threshold of 600.
Every assertion keeps its original meaning. One arithmetic shift was needed
and is named `WINDOW_LAG` in that file: `energy_window` is a 3-chunk rolling
mean, so each capture runs 2 chunks (64 ms) past its silence budget — a
property of the energy path that was invisible while VAD decided.

**Status:** FIXED (mirror + `C:\project`). Needs one Windows stage-7 run to
confirm the elapsed time drops from ~12 s to ~2 s.

---

## D40 — `VAD_AGGRESSIVENESS` is not a Config field, so it was never tunable

Two comments assert it is. `buffers.py:149`:

> aggressiveness now defaults to `Config.VAD_AGGRESSIVENESS` (0-3, WebRTC
> VAD's own scale) instead of a fixed 2, so it's tunable via `.env` without a
> code edit

and `engine.py:323` says passing nothing "lets `Config.VAD_AGGRESSIVENESS`
(default 2, same as before) actually take effect".

A grep for `VAD_AGGRESSIVENESS` across all 304 Python files returns only
those comments and the `getattr(Config, "VAD_AGGRESSIVENESS", 2)` fallback
itself. **It is declared nowhere in `config.py`,** so the fallback always
returns 2 and the earlier "BUGFIX" replaced one hardcoded 2 with another.
Same shape as D31: configurable-looking, silently not.

Worth noting this was *also* the knob that would have let D39's symptom be
tuned away rather than diagnosed — and tuning it would have been the wrong
fix, since the `or` made the structure the problem.

**Status:** FIXED (29 Oct) — declared as a real `Config` field with
`default=2` (unchanged behaviour) and clamped to webrtcvad's own 0-3 range
in `validate()`, because `webrtcvad.Vad()` raises outside it and is
constructed during boot. 7 tests in
`tests/test_vad_aggressiveness_config.py`.

---

## D41 — the adaptive endpoint subsystem was switched off, and D39 was why it could not be switched on

`VOICE_ENDPOINT_ADAPTIVE` defaulted to `False`, so `sara/audio/stt/endpoint.py`
— a complete subsystem with 20 passing integration tests — was dead at
runtime. That was recorded as **D32** and deferred for lack of evidence.

The missing piece was the reason it could not be validated: **until D39 it
could not have fired even if enabled.** `_endpoint_reached()` is only
consulted on the `else` branch of `if is_voice:`, and a permissive webrtcvad
under `or` kept `is_voice` permanently `True`. The flag and the VAD defect
were one problem wearing two labels.

**What changes.** The legacy rule waits a fixed `silence_limit`
(`_SilenceGate._DEFAULT`, 0.8 s) after speech stops, whatever was said. The
adaptive rule reads the live partial transcript:

| situation | silence required |
| --- | --- |
| complete command (`"open chrome"`) | `ENDPOINT_MIN_MS` **350 ms** |
| mid-sentence (`"open chrome and…"`) | base + 400 ms, ≤ MAX |
| no partial available yet | base |
| noisy room (level > 0.5) | + `NOISE_EXTRA` × level |

**The trade, stated honestly.** −450 ms on the common path; **+100 ms worst
case**, because the adaptive ceiling is `ENDPOINT_MAX_MS` 900 ms against the
legacy fixed 800 ms. Bounded both ways by `decide_endpoint()`.

**Over-cut guards** (why this is not a blind latency trade): utterances
under 500 ms never go below `base`; `EarlyCutGuard` detects endpoints that
cut a sentence short and widens the wait; any exception falls back to the
legacy rule; `VOICE_ENDPOINT_ADAPTIVE=False` reverts without a code change.

**Not measured end-to-end.** The −450 ms is arithmetic from the rules, not a
Windows measurement — telemetry has had zero voice turns since 16 Oct
because no turn could complete. `stt_ms` / `ttfa_ms` on the target machine is
the instrument.

**Status:** FIXED — three defaults flipped in step (`config.py`,
`voice_flags.py`, and both tripwire tables).

---

## D42 — `aec_active` tested that an AEC object exists, not that it works

`core_wiring.run_sara_logic()`:

```python
aec_active = (
    bool(getattr(Config, "AEC_ENABLED", True))
    and getattr(ears, "_aec", None) is not None
)
```

`sara/audio/aec.py` catches its own `ImportError` at module level and sets
`_HAS_AEC_LIB = False`; `AECProcessor.__init__` then sets
`self.enabled = Config.AEC_ENABLED and _HAS_AEC_LIB`. **Construction still
succeeds.** So on a machine without the `aec-audio-processing` wheel, `_aec`
is a live object with `enabled=False`, this read `True`, and SARA took both
shortcuts that only working AEC makes safe:

* mic settle dropped from `STT_SETTLE_MIN_GAP_S` (1.3 s) to 0.3 s
* the wake ack was fired non-blocking, so `listen()` starts while SARA talks

With no echo cancellation running, SARA's own acknowledgement goes into the
microphone and is transcribed as the user's command.

`engine.py`'s own `Ready` line already checked `.enabled` correctly, so the
two could report opposite things about the same AEC. That line was the
correct one; `aec_active` now matches it.

**Not the target machine's current problem** — `aec_audio_processing 1.0.1`
is installed there, so `enabled` was already `True` and the old test
happened to agree. Latent, and the kind that only bites the machine without
the wheel.

**Tests.** 5 in `tests/test_aec_active_gate.py`, including one pinning the
two reports to agree.

**Status:** FIXED.

---

## D43 — hallucinations in an unsupported script reached the command router

Observed on the target machine:

```
[STTDiag] listen completed | mode=command | audio=12.26s | text='こんにちは!'
```

The user said "open chrome" and stopped. Neither existing filter caught it:
`_is_hallucinated_repetition()` only matches repeated loops, and
`_HALLUCINATION_PHRASES` is a list of English YouTube boilerplate. So the
phantom transcript reached `detect_intent()`, matched no pattern, and was
dispatched to the LLM as conversation. **The command was lost with nothing
logged** — the worst shape of failure.

`Config.validate()` clamps `SARA_LANGUAGE` to exactly
`("english", "hindi", "hinglish")`, so everything SARA is built to hear is
written in Latin or Devanagari. A transcript made predominantly of CJK,
Hangul, Arabic, Hebrew, Thai, Cyrillic or Greek letters is invented text by
construction — the same failure mode the phrase list exists for, in scripts
that list could never enumerate.

**Fix.** `_is_foreign_script_hallucination()` in `helpers.py`, called from
`_transcribe` after the other two filters. Safeguards:

* **inert whenever `STT_LANGUAGE` pins a language** — that field is
  deliberately free-form, and a user who selects Japanese means it
* letters only; digits, punctuation and spaces never decide it
* needs a 60 % majority, so one stray glyph cannot discard a real command
* a minimum letter count, so a 1-character transcript is not trivially
  "100 % foreign"

**Tests.** 32 in `tests/test_foreign_script_hallucination.py` — the exact
observed transcript, seven other scripts, and eleven real commands including
mixed-script Hinglish (`"Chrome खोल do"`) that must never be discarded.

**Status:** FIXED.

---

## D44 — the diagnostic always reported `AEC=✗` regardless of the real app

`bench/stt_doctor.py` constructs `SpeechToText()` with `aec=None`, so its
`[STT] Ready` line printed `AEC=✗` unconditionally and said nothing about
the application — where AEC decides the per-turn mic settle (0.3 s vs
1.3 s) and whether the wake ack blocks `listen()`. This cost real debugging
time during this batch: the `✗` was read as "AEC is off" when it only meant
"the doctor does not wire it".

Stage 1 now checks for the `aec-audio-processing` wheel directly and says
which of the two it is.

**Status:** FIXED.

---

## D45 — the capture START gate false-started on room noise (D39's reasoning was wrong)

**D39 fixed the continuation test and explicitly left the start gate alone,
arguing:**

> The START gate is deliberately left alone — it is meant to be sensitive,
> and a false start is cheap again once a capture can end.

**That was wrong, and the next run on the target machine proved it:**

```
3. CAPTURE   median block : RMS 29  (room noise)
5. VAD       112/123 frames voiced
7. [STT] endpoint rule=base silence_ms=800 utt_ms=64
   listen completed | mode=command | audio=0.32s | text=''
   listen() returned after 1.12s
```

Every number identifies a false start, and they reconcile exactly:

| reported | accounted for by |
| --- | --- |
| `audio=0.32s` | 300 ms `_pre_buf` blob + one 32 ms trigger chunk = 332 ms |
| `utt_ms=64` | 2 list entries × 32 ms |
| `silence_ms=800` | 25 chunks — the endpoint firing **correctly** |
| 1.12 s elapsed | 332 ms + 800 ms + overhead |

Room noise was RMS **29** against a 210 start gate, so energy cannot have
opened that window. The only other term was `self._vad.is_speech(chunk)`,
and stage 5 shows webrtcvad voicing **112 of 123 chunks of that same
near-silent clip**. VAD opened the capture on room tone before the user
spoke.

**Why "a false start is cheap" was the error.** A false start *consumes the
listen() call*: the window opens on noise, ends 800 ms later with nothing,
and the user's speech arrives after it has already closed. Before D39 this
was invisible only because capture never ended — it ran the full 12 s and
usually swallowed the speech anyway. **D39 did not create this defect; it
removed what was masking it.** Both gates were one defect, and fixing half
of it exposed the other half.

**Fix.** Energy decides both gates. Measured margins: speech RMS 1599
against a 210 start gate (7.6×); room noise RMS 29, 6.3× below the 180
continuation gate.

**Why no sensitivity is lost** — the two facts that make energy-only safe:

* `_pre_buf` retains `PRE_SPEECH_MS` (300 ms ≈ 9 chunks) of history, so a
  gate that triggers a chunk later still captures the word onset.
* `energy_threshold` is **not a constant**. `_run_one_recalibration()`
  measures the noise floor off the ring buffer and re-derives it with a 250
  margin, clamped ±150 per step — so in this 29-RMS room it converges toward
  ~279 and the start gate falls toward ~98. A quiet speaker gets a lower
  gate automatically, which is what replaces VAD's sensitivity.

VAD is untouched at its other call sites (`_passes_barge_in_gate`,
`is_user_speaking`); this is only about who may open or hold a capture
window.

**Tests.** 10 in `tests/test_capture_start_gate.py`, including a replay
proving the old rule false-starts on RMS-29 noise and the new one does not,
and that soft speech (RMS 150) still opens a capture once the gate
recalibrates.

**Diagnostic added.** Stage 5 now cross-checks VAD against the measured
energy of the same chunks and reports what share of *near-silent* audio it
calls speech. Counting voiced frames alone reads as a healthy `PASS`; only
the comparison reveals the detector is unreliable. **This is the check that
would have identified D45 immediately**, and its absence is why D39 shipped
with a wrong assumption. Stage 7 now names a false start explicitly instead
of pointing vaguely at stage 4.

**Status:** FIXED — awaiting one Windows stage-7 run.

---

## D46 — `utt_ms` counts list entries, not chunks

`_endpoint_reached()` is passed
`len(speech_chunks) - trailing_silence_chunks` as its utterance length, and
`endpoint.py` multiplies it by `chunk_ms`. But `speech_chunks[0]` is the
drained `_pre_buf` **blob** — `PRE_SPEECH_MS` (300 ms ≈ 9 chunks) joined
into a single `bytes` — not one chunk. So the count is of list entries and
under-reports by roughly 8 chunks (~256 ms) on every utterance. Visible in
the D45 trace as `utt_ms=64` for a capture that returned 332 ms of audio.

This feeds `required_silence_ms()`'s short-utterance guard
(`utterance_ms < 500 ms` never endpoints below `base`). Under-reporting
makes that guard fire *more* often, so the current behaviour is **more
conservative than intended**, not less — which is why it is being recorded
rather than fixed here: correcting it makes endpointing more aggressive, and
that is a latency/accuracy trade that needs the Windows measurement first.

**Status:** FIXED — the utterance length is now measured in BYTES
(`sum(len(c) for c in speech_chunks[:voiced]) / bytes_per_chunk`) instead of
by counting list entries, so the `_SHORT_UTTERANCE_MS` guard sees the real
duration. Covered by `tests/test_endpoint_utterance_length.py`.

---

## D47 — threshold calibration is far too slow to be the safety net D45 claimed

D45's code comment told a future reader:

> `energy_threshold` is not a fixed constant either… so in this quiet room it
> converges toward ~279 and the gate falls with it. A quiet speaker therefore
> gets a lower gate automatically rather than needing VAD.

True in principle, **misleading in practice**, and the comment has been
corrected in place. The actual cadence:

| | value |
| --- | --- |
| first calibration | `_RECALIB_FIRST_INTERVAL` **15 s** |
| every later one | `_RECALIB_INTERVAL` **300 s** |
| movement per step | clamped **±150** |
| skipped entirely while | `_is_listening` is set |

Moving 600 down to a floor-derived ~283 takes three clamped steps —
15 s + 300 s + 300 s ≈ **10.3 minutes**. And inside a `bench/stt_doctor.py`
run the engine lives about 10 seconds, so **the first calibration never
fires at all** — which is the window the user has been testing in.

So within any session a user actually experiences, the gate is effectively
`BARGE_IN_ENERGY_THRESHOLD × 0.35` = 210. On a quiet microphone that is the
binding constraint.

**Status:** FIXED — a fast first calibration now runs ~1.5 s after the
stream opens (enough for `_NoiseFloor` to have its 5 samples) and is allowed
to skip the ±150 step clamp, so the gate reaches the right order of
magnitude at once; every later cycle keeps the clamp, so ordinary drift
stays gradual. Shipped **with** D49 as that condition required.

---

## D48 — auto-gain compensates for a quiet mic at transcription, but not at the gate

`_normalize_audio_float32()` amplifies a captured clip up to peak 0.95
(**up to 8×**) before it reaches Whisper. The capture gates read raw chunk
RMS off the ring buffer and get no such help.

That asymmetry is exactly the contradiction observed on the target machine:

```
4. [PASS] speech would be detected -- 2.9x above the gate   (loudest RMS 607)
6. [PASS] transcript produced -- 188ms    TRANSCRIPT: 'Open Chrome.'
7. [FAIL] nothing captured in 8.0s -- speech never passed the START gate
```

Stage 6 succeeded **because** of the gain; stage 7 failed because the gate
never sees it. Transcription is forgiving of a quiet mic and capture is not,
so the pipeline's own two halves disagree about whether the user is audible.

**Status:** OPEN — recorded, not fixed. Applying gain before the gate would
also amplify room noise into it, so the sound fix is a noise-relative gate
(D49) rather than a louder input.

---

## D49 — the floor-derived threshold puts the start gate BELOW the noise floor on a noisy mic

`_NoiseFloor.suggested_threshold(margin=250)` returns `floor + 250`, and the
start gate is `threshold × 0.35`. So:

```
gate = 0.35 × (floor + 250) = 0.35·floor + 87.5
gate > floor   requires   87.5 > 0.65·floor   →   floor < 134.6
```

| noise floor | threshold | start gate | gate ÷ floor |
| --- | --- | --- | --- |
| 29 | 279 | 98 | 3.37× |
| 33 | 283 | 99 | 3.00× |
| 125 | 375 | 131 | 1.05× |
| **134** | 384 | 134 | **1.00×** |
| **164** | 414 | 145 | **0.88×** |
| **203** | 453 | 159 | **0.78×** |

**Above a floor of ~135 the gate sits below the room noise it was derived
from.** The target machine measured floors of 164 and 203 in earlier runs, so
this is not hypothetical for that hardware — it is the D45 false-start
condition, reached by the calibration path instead of by VAD.

A fixed absolute `margin` cannot hold a ratio once it is multiplied by 0.35.
A noise-relative rule — `gate = max(floor_absolute, k × floor)`, i.e.
`threshold = max(…, k/0.35 × floor)` — holds it at every floor.

**Why this matters more than its own severity:** it is the reason D47 was
*not* fixed in this batch. Speeding calibration up without this would have
turned a mechanism that is too slow to help into one that is fast enough to
hurt.

**Status:** FIXED — `_NoiseFloor.suggested_threshold_relative()` derives
`gate = clamp(4.0 × floor, 90, 650)` and `threshold = gate / 0.35`, which
holds its margin at every floor where `floor + 250` could not. Validated
against all seven floor/speech pairs measured on the target machine: worst
margin 3.20× over room noise, 3.6× under speech. 33 tests in
`tests/test_noise_relative_gate.py`. **Enabled by default in D65, reverted
to off in D71 after the change was heard as added latency — the formula and
its tests stand, only the default moved.**

---

## Diagnostic added — stage 7a, the engine's own microphone

Stage 3 opens its **own** `sounddevice` stream; stage 7's gate reads chunks
off the **engine's** ring buffer, filled through
`_ingest_processed_chunk`. When those two disagreed — stage 4 `PASS`, stage 7
timeout — nothing in the diagnostic could say which of three things was
true: the engine's stream is not feeding, its chunks arrive quieter than
stage 3 measured, or the user simply spoke more softly the second time.

Stage 7a now drains the engine's ring directly and reports the gate's own
input: chunk count, loudest and median RMS, the gate value, and how many
chunks cleared it. A stage-7 timeout is no longer ambiguous. (`listen()`
clears the ring when it starts, so probing first costs that session
nothing.)

---

## PIPELINE VERIFIED END TO END — 29 Oct 2026

First run in which every stage passed on the target machine:

```
7a. [PASS] engine stream is feeding -- 95 chunks
           loudest chunk RMS 2202, 24 chunks over the 105 gate (21.0x)
7.  [STT] endpoint rule=complete silence_ms=640 utt_ms=896
    listen completed | audio=1.15s | text='Open Chrome.' | confidence=0.780
8.  [PASS] transcript reached the router -- intent=open_app, target='chrome'
    No stage failed.
```

Capture, endpointing, transcription and routing all work. The chain
mic → gate → endpoint → Whisper → router → tool is closed.

**The user set `BARGE_IN_ENERGY_THRESHOLD=300` in `.env`** (gate 105), which
is what let their quiet microphone through. That is their machine's setting,
**not a new default** — at the noise floors their earlier runs measured (164,
203) a gate of 105 sits *below* the room noise and would false-start, which
is D49 exactly. The default stays until D47+D49 make the gate noise-relative.

---

## D50 — stage 7 blamed the silence gate for the user's own reaction time

The warning read:

```
listen() returned after 6.70s
[WARN] 6.7s is slow for a short command; the silence gate is close to
       your room noise (stage 4)
```

Both halves were wrong on a healthy run. `elapsed` is measured from the
moment `listen()` is **called**, so it includes however long the user takes
to start speaking — `listen()` waits up to `timeout` (8 s) for that. The
engine's own line in the same run said `audio=1.15s`, so the breakdown was
**5.55 s of waiting plus a 1.15 s capture**, ended by a 640 ms endpoint and
nowhere near the 12 s ceiling. Meanwhile stage 4 had just measured room
noise 25 against a silence gate of 90 — **3.6× clear**, the opposite of
"close".

A diagnostic must not assert a cause it cannot observe. From outside
`listen()` the only unambiguous defect signal is hitting the ceiling, since
that is the one outcome reaction time cannot produce. The gate claim is
gone; stage 4 measures that directly and the `[STTDiag] audio=` line reports
the capture length.

**Status:** FIXED.

---

## D51 — ~~the "complete command" fast path never actually fires~~ SUPERSEDED BY D72

> **Do not read this section as current.** Three of its claims are wrong,
> and **D72** carries the corrections:
>
> 1. *"The reported rule is not the rule that was applied."* Wrong — the
>    label was honest; the preview was simply slow. Already recorded in the
>    self-review table below.
> 2. *base = 625 ms.* Wrong — that is the `base_ms=None` fallback.
>    `_endpoint_open()` passes a base derived from `_SilenceGate`: **800 ms**
>    fresh, **450 ms** after short commands, 900 ms after dictation. 625 ms
>    never occurs in the running application.
> 3. *"Measured cost: 288 ms per command."* Wrong, following from (2). The
>    fast path does fire on a late partial, and against the 450 ms base that
>    repeated short commands produce the ceiling on the whole saving is
>    **128 ms**, with 4 of 7 measured previews contributing nothing.
>
> Kept rather than deleted because the preview-latency instrument it added
> is what made the correction possible.

`rule=complete` is the adaptive endpoint's whole latency win: a recognised
command should end after `ENDPOINT_MIN_MS` (350 ms) instead of `base`. The
target machine reported `rule=complete` — and still waited 640 ms:

```
base      = lo + 0.5 × (hi − lo) = 350 + 0.5 × 550 = 625 ms
next 32 ms chunk boundary after 625  = 640 ms
observed  silence_ms = 640           ← exactly base, not 350
```

The decision was made on **base**, not on "complete". `decide_endpoint()` is
called with the latest partial transcript, which was still `None` at 350 ms,
so it used `base`; by the time it returned `True` the partial had landed, and
`rule` is recomputed *after* that — so the label says `complete` for a
decision that was not. **The reported rule is not the rule that was
applied**, which is how this stayed invisible.

Measured cost: **288 ms per command** (640 → 352), on every recognised
command.

The partial has to arrive within 350 ms of speech ending. It is spawned at
`ENDPOINT_PREVIEW_AT_MS` (150 ms of silence), leaving ~200 ms for a
transcribe that runs on the model in D52.

**Status:** OPEN — fix with telemetry, so the saving is measured rather than
computed. Also worth making the debug line report the rule actually applied.

---

## D52 — the preview model is hardcoded to CPU while the main model is on GPU

`_load_fast_wake_whisper()` passes `device="cpu"` as a literal, with no GPU
attempt, no fallback chain and no config override — unlike
`_load_faster_whisper()`, which tries CUDA first and falls back (D36/D37):

```python
model = _load_whisper_model_offline_first(
    model_size_or_path=model_size,
    device="cpu",
    compute_type="int8",
    cpu_threads=cpu_threads,
)
print("[STT] ✅ Wake-word Whisper loaded on CPU.")
```

That same model serves the live-caption **preview**, which produces the
partial transcript D51 depends on. So the one inference on the critical path
for the 288 ms saving is the one pinned away from the GPU that the main model
is already using successfully.

Pinning it to CPU may well be deliberate — keeping the GPU clear for the
command model, and the wake probe is latency-tolerant. But the preview is
**not** latency-tolerant, and nothing in the code says which trade was
intended.

**Status:** FIXED — the wake/preview model now attempts CUDA first and
falls back to CPU, recording the accepted device exactly as the main model
does (D36/D37). 13 tests in `tests/test_wake_model_device.py`.

---

## SARA WORKS END TO END IN THE REAL APP — 29 Oct 2026

```
[STT] ✅ Wake word detected | score=0.7981 | threshold=0.6000
listen completed | audio=1.05s | text='Open Chrome.' | confidence=0.745
[Debug] Launched application: chrome (spoken: 'chrome')
```

Confirmed working in `main.py`: wake word, capture, endpointing,
transcription, routing and tool execution. `'Open Chrome.'` → launched
(twice), `'Open VS Code.'` → launched, `'Close this window.'`,
`'Who is the Prime Minister of India?'` → answered.

Endpointing also **adapted**: `silence_ms` fell from 800 to **480** across
the session as `_SilenceGate` learned the user's short utterances, so D41 is
working and tuning itself.

---

## D53 — zero voice turns were recorded, and nothing said why

The user spoke ~15 commands as instructed, then:

```
VOICE turns (microphone -> first audio): no turns recorded.
TEXT turns (typed; NO wake/capture/STT -- a lower bound): 16 turns
```

The 16 text rows are **not** those voice turns: the voice path passes
`lang=brain.get_language()` while the GUI path passes none, and all 16 have
`lang` NULL. So the voice session genuinely recorded nothing, and the whole
measurement session was lost.

**Two findings, one symptom.**

### 53a — the switch is invisible

`Config.TELEMETRY_ENABLED` defaults to `True`, but
`sara/gui/app/perf_api.py::_apply_saved()` re-applies a **persisted**
`ui:telemetry_enabled` preference the first time the GUI asks for a perf
summary — which the dashboard does on load. So a user who once turned
tracing off in the GUI has it off in **every later session**, with nothing
printed and no error. Nothing in the log mentions telemetry at all.

**Fixed.** Boot now prints one of:

```
[Telemetry] recording ON -> C:\Users\...\telemetry.sqlite
[Telemetry] recording OFF -- no turns will be measured this session.
```

And `bench/voice_latency.py` no longer answers zero rows by telling the user
to speak more. It reads the saved preference and distinguishes the two
causes:

```
tracing switch : *** OFF (saved GUI preference) ***
                 Nothing was recorded, however much you spoke.
```
```
tracing switch : ON (saved GUI preference)
                 So zero voice rows means voice turns are not
                 reaching the recorder -- not that tracing is off.
```

### 53b — the LAST turn of every session was lost (FIXED)

**CORRECTION.** The first draft of this finding claimed turns were only
ever written when the next one began, so a 15-command session recorded
almost nothing. **That was wrong**, and measuring it properly disproved it:
the repro only waited 6 s. `_sweep()` runs from the store's idle hook every
`_FLUSH_S` (2 s) and finalises turns that ended more than
`_FINALISE_AFTER_END_S` (20 s) ago — three turns plus a 24 s wait produced
three rows on disk. So 53b was never the reason the user's session recorded
nothing; **53a (tracing switched off) is.**

The real loss is narrower and still worth fixing. A turn that ends **within
20 s of the process exiting** was lost completely, because the store's
writer is a daemon thread killed at interpreter exit with its queue still
full. Measured with a subprocess that ends a turn and exits immediately:

```
RESULT: no DB file -> the turn was LOST on exit
```

That is always the **last** turn of a session — frequently the one being
investigated, and the one a user reaches for after saying "that reply was
slow". The user's own session ended 5 s after its last turn, so that turn
was lost.

**Fix.** `atexit` handlers run while the interpreter is still alive and
before daemon threads are torn down. `_flush_at_exit()` sweeps with the age
bar at `0.0` and then drains the queue **on the exiting thread** via a new
`TraceStore.drain_now()`, which is time-bounded and silent — a failure to
log must never become a failure to exit. Re-measured:

```
RESULT: 1 row(s) survived exit -> [('voice', 'ok', None)]
```

`ttfa_ms` is null in those rows, and that is correct: the 20 s wait exists
so a turn can still pick up `ttfa_ms` when TTS finishes after `end_turn`,
and at shutdown there is no such chance left. A row with an honest null
beats no row. Turns still in flight (no `end_turn`) are deliberately left
alone — they have no outcome, so a row for them would be a guess.

**Tests.** 11 in `tests/test_telemetry_exit_flush.py`.

**Status:** 53a FIXED, 53b FIXED.

---

## Measured from the real session (text turns only — a lower bound)

| stage | p50 | p95 |
| --- | --- | --- |
| intent routing | **922 ms** | 2000 ms |
| model first token | 2360 ms | 3110 ms |
| synthesis → first audio | 1250 ms | 1453 ms |
| TIME TO FIRST AUDIO | **4156 ms** | 5359 ms |

### D54 — Gemini free tier is exhausted, and SARA retries a daily quota

```
429 RESOURCE_EXHAUSTED ... limit: 20, model: gemini-2.5-flash
Generate content requests per day limit exceeded on Free Tier
Please retry in 6h35m46s
```

Every LLM turn made **two** attempts with a 1.5 s delay between them before
failing over to ollama. The error itself says to retry in **six and a half
hours**, so retrying in 1.5 s cannot succeed — it is pure added latency on
every turn, roughly 4 s. The circuit breaker did eventually engage
(`[LLM] Circuit breaker tripped — skipping primary backend this turn`) but
only after several turns had each paid the full cost.

A daily-quota 429 carries `RetryInfo.retryDelay` (23746 s). That is the
signal to trip the breaker immediately rather than retry.

**Status:** FIXED (29 Oct) — see the batch entry below.

### D55 — Kokoro TTS runs on CPU although GPU was requested

```
[Debug] TTS engine  : Kokoro ONNX (GPU requested=True)
[Debug] ORT provider: active=CPUExecutionProvider |
                      available=['AzureExecutionProvider','CPUExecutionProvider']
```

GPU was requested and silently not honoured: the installed `onnxruntime`
has no CUDA provider, so only CPU is available. Measured cost:
`synthesis → first audio p50 = 1250 ms`, the largest controllable share of
time-to-first-audio after the LLM. Needs `onnxruntime-gpu`, and the main
Whisper model is already proving CUDA works on this machine.

**Status:** OPEN — a dependency change, so it needs the user's agreement.

### D56 — the Gemini embedding endpoint 500s, retried 3× per turn, with a traceback

`gemini-embedding-001` returned HTTP 500 repeatedly. Each turn: 3 attempts
with backoff (~1.6 s) and then a **full Python traceback printed to the
console**, followed by `[RAG] search() aborted`. An upstream 500 is an
expected condition for a remote service, not a programming error; a
traceback per turn is noise that buries real failures.

**Status:** FIXED (29 Oct) — see the batch entry below.

### D57 — Latin-script hallucinations defeat the D43 filter

```
listen completed | audio=1.60s | text='Kanu hermekleyenli.' | confidence=0.780
```

Turkish gibberish at confidence 0.78, sent to the LLM as a real message
(`Sara replied: I don't understand that message`). D43 only catches
**non-Latin** scripts, so Turkish, German and French hallucinations pass
straight through.

The root cause is visible alongside it: language detection wanders badly on
short clips — `'ko' 0.21`, `'de' 0.55`, `'tr' 0.52`, `'fr' 0.12`, `'hi' 0.38`
— for a user who speaks only English, Hindi and Hinglish, with
`ForcedLang=None` and `WHISPER_MODEL_SIZE=tiny`. Two candidate fixes, both
measurable: finish the `medium` download, and constrain detection to the
three supported languages instead of all 99.

**Status:** OPEN.

### D58 — a persisted GUI slider silently overrides the `.env` threshold

```
[STT] Ready — ... threshold=300          <- from .env
[Debug] Restored mic sensitivity: 23% (threshold=793)   <- overrides it
```

The user set `BARGE_IN_ENERGY_THRESHOLD=300` in `.env`; a saved GUI
sensitivity of 23 % replaced it with **793**, so the real start gate was
`793 × 0.35 = 277`, not 105. **CORRECTION to the first draft of this finding:** it claimed this also
suppresses recalibration for ten minutes. It does not. `history.py` uses the
plain `energy_threshold` setter, **not** `set_manual_energy_threshold()`, so
`_manual_threshold_until` is untouched and `_run_one_recalibration()` still
runs on its usual schedule. Only the override itself was real.

The two lines print 300 and 793 moments apart with no indication that the
second wins. Precedence is real but undocumented and unlogged.

**Status:** FIXED (29 Oct, reporting) — see the batch entry below.

### D59 — app allowlist misses common targets

```
open_application: no match for 'youtube'
open_application: no match for 'word'
```

48 allowlist entries, and two of the user's first handful of commands missed.
`'Open YouTube'` and `'Open MS word'` transcribed **correctly** — this is
coverage, not recognition.

**Status:** NOT A DEFECT, reclassified. `"word"` is already an alias for
`winword`; `no match` means the alias resolved and the program is not
installed, which is correct. The routing attempt was **reverted** — see the
D59 entry in the batch below.

---

## Batch: the measured waste from the real session — 29 Oct 2026

Four fixes shipped, one change **reverted on its own evidence**, two
findings recorded without a fix.

### D54 — FIXED: SARA retried a daily quota the server said needed 6½ hours

```
429 RESOURCE_EXHAUSTED ... limit: 20 ... Please retry in 6h35m46s
'retryDelay': '23746s'
[LLM:gemini] Retry 1/1 in 1.5s: 429 RESOURCE_EXHAUSTED ...
```

Every LLM turn made two full round trips plus a 1.5 s sleep against a
free-tier **daily** limit, before failing over to ollama — roughly 4 s of
pure latency per turn for an attempt that could not succeed.
`non_transient` only covered `_ClientUnavailableError`, so a quota 429 was
retried like a network blip.

**Fix.** `_server_retry_after_s()` reads the delay the server itself asked
for — structured `google.rpc.RetryInfo` first, then the message text — and
the retry loop treats anything longer than `max_delay` as non-transient.
The rule is self-describing: *if the server wants longer than this loop
would ever sleep, another attempt is guaranteed to fail.* A short
per-minute 429 asking for 2 s stays retryable, which is what the backoff is
for. `None` and `0.0` stay distinguishable, so "no advice" is never read as
"wait zero".

**Tests.** 9 in `tests/test_llm_quota_no_retry.py`, driven by the real
payload, including junk input and that the threshold tracks `max_delay`
rather than a hardcoded number.

### D55 — FIXED (reporting): TTS asked for the GPU and silently got CPU

```
[Debug] TTS engine  : Kokoro ONNX (GPU requested=True)
[Debug] ORT provider: active=CPUExecutionProvider |
                      available=['AzureExecutionProvider','CPUExecutionProvider']
```

Those two lines sit about fifty log lines apart and nothing connected them.
The selection logic is correct — it checks availability and falls back — but
a reader of the first line reasonably concludes TTS is on the GPU. Measured
cost: `synthesis → first audio p50 = 1250 ms`.

This is **not** a missing CUDA toolkit: the main Whisper model loads on CUDA
on the same machine. It is the wrong `onnxruntime` package, so the remedy is
named in the log rather than left to be guessed:

```
[TTS] GPU was requested (KOKORO_USE_GPU=True) but onnxruntime has no
      CUDAExecutionProvider, so synthesis runs on CPU.
[TTS]   to use the GPU: pip uninstall -y onnxruntime && pip install onnxruntime-gpu
```

The dependency change itself is the user's to run.

### D56 — FIXED: an upstream 500 printed a full traceback every turn

`gemini-embedding-001` returned HTTP 500 on most turns; each one logged two
retry warnings and then a `logger.error(..., exc_info=True)` — a ~40-line
traceback whose frames are always identical (`google.genai`, `tenacity`,
`concurrent.futures`). Four of them in a two-minute session, burying the
429s that mattered.

**Fix.** `exc_info=not server_side`, where `server_side` is the 5xx
classification the code had already computed one line above. A remote 5xx is
an expected operating condition and its stack says nothing new; an exception
we could **not** classify keeps its traceback, because there not knowing the
call path is the actual problem. Retries and backoff unchanged — this was a
logging fix.

**Tests.** 4 in `tests/test_embedding_error_logging.py`, including that the
one-line message still carries status, model, text length and exception
type.

### D58 — FIXED (reporting): a saved GUI slider silently overrode `.env`

```
[STT] Ready — ... threshold=300                          <- from .env
[Debug] Restored mic sensitivity: 23% (threshold=793)    <- this wins
```

`1000 − (23 × 9) = 793`, so the real start gate was `793 × 0.35 = 277`, not
the 105 the user expected from `BARGE_IN_ENERGY_THRESHOLD=300`. Both numbers
appeared in the same log, neither marked as effective.

Precedence itself is right — an explicit GUI action should beat a file
default. Only the silence was wrong. The log now says so, names the gate it
implies, and points at the slider rather than `.env`.

### D57 — OPEN, and a naive fix is **disproved** by the data

```
listen completed | audio=1.60s | text='Kanu hermekleyenli.' | confidence=0.780
```

Turkish gibberish at 0.78 confidence, sent to the LLM as a real message.
D43's filter only catches **non-Latin** scripts, so Turkish, German and
French hallucinations pass through. Language detection wanders badly on
short clips with `tiny` and `ForcedLang=None`.

The obvious fix — discard a transcript whose detected language SARA does not
support — **would have made things worse.** From the same session:

| detected | prob | transcript |
| --- | --- | --- |
| `'de'` | 0.55 / 0.42 | **`'Open Chrome.'`** — a correct command |
| `'tr'` | 0.52 / 0.41 | `'Kanu hermekleyenli.'` — a hallucination |
| `'hi'` | 0.38 | `'Open calendar.'` — correct, English text |
| `'fr'` / `'ko'` | 0.12 / 0.21 | empty anyway |

Gating on detected language would have discarded a correct "Open Chrome."
The signal is too noisy to filter on, and losing a real command is worse
than a confused reply. Recorded unfixed; the real lever is the `medium`
model, already part-downloaded.

### D59 — REVERTED after it regressed the project's own accuracy gate

`'Open YouTube.'` transcribed perfectly and did nothing
(`open_application: no match for 'youtube'`), because bare "open youtube"
matches no `open_url` pattern — those all require a dot — and `play_youtube`
needs something to play.

A bare-known-site pattern plus a `_KNOWN_SITES` resolver was implemented and
measured against `bench/run_bench --mode router`:

```
without the change : acc=98.8%  en=98.2%
with the change    : acc=98.6%  en=97.9%
```

Diffing the result rows found **exactly one** regression, and it was
decisive: golden row `adversarial-0027`, `'sara sara open youtube'`,
**expects `open_app`**.

So the project's own golden set says "open youtube" is an app launch. And
the reason `open_app` failed on the target machine is **not understood**:
the code says the Windows Search fallback should have run —
`_resolve_app_name('youtube')` gives `source='raw'`,
`_search_fallback_applies('youtube')` is `True`, `youtube` is absent from
the 43-entry allowlist, and both config switches are on — yet the log shows
the not-found path instead.

Routing to `open_url` would therefore have papered over a different bug
while lowering the accuracy gate on a judgment call that cannot be
substantiated from here. **Both halves reverted**; `web.py` and
`patterns.py` are byte-identical to the shipped versions again and the bench
is back to 98.8% / 98.2%. `_KNOWN_SITES` went with the pattern, since
without it the map is unreachable and dead code that looks like a feature is
worse than none.

**Open question for the next batch**, needing a probe on the target machine:
why `_open_via_windows_search()` was not reached for `'youtube'`. Note too
that an **aliased** app which is not installed (`word → winword`) can never
reach the fallback at all, because the condition requires
`resolved.source == "raw"` — so SARA gives a second chance to names it does
*not* know and none to the names it does.

### Known flaky test — SUPERSEDED, now fixed (see D62 below)

`tests/test_tts_stream_properties.py::test_no_stale_audio_from_a_stopped_stream_reaches_the_next_one`
was recorded here as "timing-sensitive rather than wrong" after it passed
3 × 10 times under CPU load. **That conclusion was premature** — a harder
reproduction found it at 3/25 and identified a real race. Fixed in D62.

### Validation

* full suite **2380 passed, 19 skipped, 0 failed** (run twice)
* `bench/run_bench --mode router`: **acc 98.8%, en 98.2%, hi 98.9%,
  hinglish 100.0%, neg_false_triggers 0/60** — identical to before the batch
* new tests: 9 (D54) + 4 (D56)

---

## Batch: audio-pipeline work, unattended — 29 Oct 2026

Three fixes, and **two of my own earlier claims corrected by measurement**.

### D46 — FIXED: the utterance length counted list entries, not audio

`_collect_speech` passed `len(speech_chunks) - trailing_silence_chunks` to
the endpoint. But `speech_chunks[0]` is the drained `_pre_buf` **blob** —
`PRE_SPEECH_MS` (300 ms, ~9 chunks) joined into one `bytes` object by
`_PreBuffer.drain()`. So every utterance was under-reported by ~8 chunks
(~256 ms). Confirmed against the target machine's own trace: `utt_ms=896`
for a capture that returned `audio=1.15s` — 36 chunk-equivalents counted as
28 entries, exactly the 9-chunk blob collapsing to one.

**Why it was a latency bug, not just a wrong number.**
`required_silence_ms()` refuses to endpoint below `base` while
`utterance_ms < _SHORT_UTTERANCE_MS` (500 ms) — a guard for stray blips.
Under-reporting by 256 ms pushed genuine **500–760 ms commands** under that
bar, so they waited `base` (625 ms) instead of `ENDPOINT_MIN_MS` (350 ms).
Measuring bytes restores the boundary the guard was designed around rather
than moving it, so the guard becomes *more* accurate, not weaker — a
genuinely short 256 ms utterance is still protected.

**Why the existing suite missed it.**
`tests/test_stt_endpoint_integration.py`'s `_FakePre.drain()` returns
`None`, so there is no blob and entry-count and byte-count agree. The 20
tests there passed before and after the fix without being able to see it.
`tests/test_endpoint_utterance_length.py` uses a pre-buffer that behaves
like the real one; 5 tests, including that the fix does not weaken the
blip guard.

### D51 — CORRECTED: the log was honest; the timing was the defect

An earlier draft claimed "the reported rule is not the rule that was
applied". **Wrong.** Replaying the decision against `endpoint.py` with the
measured numbers:

```
silence= 352ms  text=None          -> need=625  rule=base      fired=False
silence= 448ms  text=None          -> need=625  rule=base      fired=False
silence= 544ms  text=None          -> need=625  rule=base      fired=False
silence= 640ms  text='open chrome' -> need=350  rule=complete  fired=True
```

At 640 ms the partial really had arrived and the rule really was
`complete`. `rule` is recomputed after `decide_endpoint` returns, but from
the **same** `text`, so it cannot disagree. The 288 ms was lost purely
because the partial transcript arrived at ~640 ms when it must arrive
within `ENDPOINT_MIN_MS` (350 ms) to be usable.

### D52 — FIXED: the preview model was pinned off the GPU by a literal

`_load_fast_wake_whisper()` passed `device="cpu"` as a literal, with no GPU
attempt and no config override — unlike `_load_faster_whisper()`, which
tries CUDA first and proves it (D36/D37). That same model runs the
live-caption **preview**, whose partial transcript is the input D51 depends
on. So the one inference on the critical path for the 288 ms saving was the
one kept off the GPU that the main model was already using successfully on
that machine.

**Fix.** The same GPU-first chain as the main model: try CUDA with
`int8_float16`, **prove it can infer** (`_prove_model_can_infer` — a
successful constructor proves nothing, since CTranslate2 resolves CUDA
lazily), and fall through to the original CPU load on any failure. So the
worst case is exactly the previous behaviour. New `Config.WAKE_MODEL_USE_GPU`
(default True) turns it off deliberately, declared as a real field rather
than a `getattr` default — the audit has found that pattern lying twice
already (D31, D40). Both branches record the device, per D37's lesson.

`ENDPOINT_PREVIEW_AT_MS` (150 ms) was deliberately **not** changed: a tiny
model on GPU should finish inside the remaining 200 ms, and lowering the
threshold would fire previews on ordinary inter-word pauses. One change, so
the next trace attributes cleanly.

**Tests.** 8 in `tests/test_wake_model_device.py`.

### Needs the Windows machine

* whether the partial now lands inside 350 ms — visible as
  `[STT] endpoint rule=complete silence_ms=~350` instead of `~640`
* whether the wake model actually loads on GPU there — the new
  `[STT] ✅ Wake-word Whisper loaded on GPU` line says so
* `pip uninstall -y onnxruntime && pip install onnxruntime-gpu` for D55

### Validation

* full suite **2404 passed, 19 skipped, 0 failed**
* `bench/run_bench --mode router`: **98.8% / en 98.2% / hi 98.9% /
  hinglish 100.0% / 0/60 false triggers** — unchanged across all three
  fixes
* new tests: 11 (D53b) + 5 (D46) + 8 (D52)

---

## Self-review of this session's own changes — 29 Oct 2026

The user asked for the work to be re-checked and verified rather than
trusted. Three passes were run.

### Pass 1 — is every change actually reachable?

A probe asserted each of this session's 13 behavioural changes is live at
runtime, not just present in a file: **13/13**. This is the D32 trap
(a complete, tested subsystem that was switched off) and the D31/D40 trap
(a flag that looked tunable via `.env` and silently was not), checked
deliberately rather than assumed.

### Pass 2 — two real mistakes found in my own D52 change

**`_wake_model_device` was written and never read.** Both load branches
recorded the device and nothing reported it — dead code that looks like a
feature, which is precisely the reason D59 was reverted an hour earlier.
Fixed by reporting it: the `Ready` line now carries `Preview=…`, which is
the line that answers "is the model gating the 350 ms endpoint path on the
GPU" (D51/D52).

```
[STT] Ready — FasterWhisper=✓ medium on GPU (CUDA, int8_float16) |
      Preview=tiny on GPU (CUDA, int8_float16) | VAD=✓ | AEC=✓ ...
```

Rendered in all five device combinations, including `Preview=✗` when no
wake model loaded, `Preview=none` when a model loaded without recording a
device, and the `DEGRADED (no STT model)` case.

**The probed wake model was warmed twice.** D52's GPU path calls
`_prove_model_can_infer()`, which runs an inference and therefore warms the
model — then `_warm_recognisers()` warmed it again. On the target machine
the wake warm-up measured 563–719 ms, so that was most of a second of
duplicated boot cost. Fixed with a `_wake_model_warmed` flag set **only**
on the probed GPU path; the CPU fallback does not probe, so a model from
there still has its first-inference cost unpaid and must still be warmed.
Both directions are tested behaviourally, not just structurally.

A near-miss worth recording: `_prove_model_can_infer()` does **not** set
`_main_model_warmed` — the caller does. Had it set the flag itself, calling
it for the wake model would have marked the MAIN model warmed and skipped
warming it entirely. Checked, and it is clean.

### Pass 3 — any other attribute written and never read?

A scan of every changed production file for write-only attributes returned
no genuine cases. `_model_device` and `_wake_model_device` are both read
once (via `getattr` in `_log_init`); `_main_model_warmed` and
`_wake_model_warmed` are read through a variable attribute name, so grep
cannot see them but the behavioural test can and does. The remaining hits
(`_barge_boost`, `_endpoint_guard`, `_wake_diag_*`) are pre-existing and
each has a reader.

---

## Audio pipeline: checked and found sound (not changed)

Recorded so the same ground is not re-ploughed, and so a later change has a
measured baseline to argue against.

### The v2 first-chunk chunker is correct; its conservatism is deliberate

Measured characters-to-first-audio across realistic SARA replies:

| chars | first chunk |
| --- | --- |
| 10 | `'Theek hai!'` |
| 14 | `'Opened chrome.'` |
| 21 | `'Okay, Chrome is open.'` |
| 29 | `'No. That file does not exist.'` |
| 33 | `'Sure! Opening Chrome for you now.'` |
| 47 | `'Done! Screenshot saved to your Pictures folder.'` |
| 62 | `"Sorry, I couldn't find an app called youtube on this computer."` |

The worst cases share one shape: a short leading acknowledgement whose
terminator is rejected by `MIN_FIRST_CHARS` (8), so the cut lands on the
**second** sentence end and the chunk carries both.

That is the intended trade, not an oversight. A 5-character first chunk
starts audio sooner and is then followed by an audible gap while the rest
synthesises — and on the target machine Kokoro is on the **CPU** at
`synthesis → first audio p50 = 1250 ms` (D55), where that gap would be
plainly audible. Merging to the next sentence end also keeps prosody
intact, which cutting after "Done!" would not. So `MIN_FIRST_CHARS` was
**not** touched: the right value depends on synthesis speed, and changing
it while TTS is CPU-bound would be tuning blind.

**What was missing was the gate, not the fix.**
`tests/test_tts_first_chunk_latency.py` guards cut *safety* (no mid-token,
mid-number, URL or abbreviation cuts) and nothing guarded *latency* — a
chunker change could push first audio later on every reply and the suite
would stay green. `tests/test_tts_stream_latency_budget.py` now pins the
budget above, within 5 characters of the measured value so it gates rather
than decorates, plus the hard ceiling (`WORD_MIN_CHARS` must fire) and the
inverse case (an unpunctuated reply below that ceiling must still wait,
rather than being "fixed" into chopping mid-phrase). The unit is
**characters of stream, not milliseconds** — Kokoro is not installable
here, so synthesis time is neither measured nor claimed.

One of those tests initially failed on a Devanagari reply and looked like a
real bug — Hindi producing no streaming audio at all. It was a **stale test
premise**: the string was 59 characters, one short of `WORD_MIN_CHARS`
(60), so nothing had fired yet and buffering was correct. `_safe_cut`
returns True at every Devanagari word boundary past the rule, now pinned by
its own test so that conclusion is not re-derived.

### `TTS_FIRST_CHUNK_MIN_CHARS` is inert under the current defaults

`VOICE_CHUNKER_V2` is on (D31), and in `_chunker_worker` the v2 branch
either `continue`s or sets `first_pushed = True` — so the legacy
`_FIRST_TRIGGER` path is unreachable for the first chunk.
`TTS_FIRST_CHUNK_MIN_CHARS` (12) and
`TTS_FIRST_CHUNK_SOFT_BOUNDARY_MIN_CHARS` (35) therefore have no effect
while v2 is enabled. `config.py` says so at the flag; recorded here because
an earlier batch was asked to investigate that 12 and it is the wrong lever
under today's defaults.

### The preview window does not inflate preview cost on commands

`PREVIEW_WINDOW_S = 6.0` bounds the rolling preview buffer, and the
endpoint preview transcribes `b"".join(ep["preview"])`. For a 1.2 s command
that deque holds ~1.2 s, so the 6 s ceiling is not reached and there is no
wasted inference on the D51 critical path. The ceiling only engages during
long dictation, where a longer caption is wanted anyway.

### `_FIRST_TRIGGER` is duplicated into five modules where it is dead

`sara/audio/tts/{cache,text_prep,synth,player,voice_params}.py` each define
`_FIRST_TRIGGER = 5  # lowered from 8 — flush first micro-chunk sooner` and
**none of them use it** (1 mention each, 0 uses). The live value is
`engine.py`'s, which reads Config (12, itself inert per above). Editing any
of the five would appear to change first-chunk behaviour and would do
nothing — the same shape as D31/D40.

Not removed: dead constants are a maintenance hazard rather than a defect,
and churning five files unattended buys nothing the user asked for.
**Status:** OPEN, cosmetic.

### Validation

* full suite **2429 passed, 19 skipped, 0 failed**
* `bench/run_bench --mode router`: **98.8% / en 98.2% / hi 98.9% /
  hinglish 100.0% / 0/60** — unchanged
* new tests: 20 (streaming budget) + 5 (self-review findings)

---

## D47 + D49 — FIXED, shipped OFF by default — 29 Oct 2026

The two had to land together: fixing the cadence without the formula would
have turned a mechanism too slow to help into one fast enough to hurt.

### D49 — the additive formula cannot hold a ratio

`_NoiseFloor.suggested_threshold()` returns `floor + margin`, and the start
gate is `threshold * 0.35`:

```
gate = 0.35 * (floor + 250) = 0.35·floor + 87.5
gate > floor   requires   87.5 > 0.65·floor   →   floor < 134.6
```

**Above a floor of ~135 the gate sits below the room noise it came from** —
the D45 false-start condition, reached through calibration instead of
through the VAD. The target machine measured floors of 164 and 203.

**Replacement:** `suggested_threshold_relative()` —
`gate = clamp(multiplier × floor, gate_min, gate_max)`,
`threshold = gate / 0.35`.

`multiplier = 4.0` was chosen against the seven floor/speech pairs measured
on that machine, **each pair from one session** so the levels are
comparable:

| floor | speech | gate | over noise | under speech |
| --- | --- | --- | --- | --- |
| 24 | 2202 | 96 | 4.0× | 22.9× |
| 25 | 3131 | 100 | 4.0× | 31.3× |
| 29 | 1599 | 116 | 4.0× | 13.8× |
| 33 | 607 | 132 | 4.0× | 4.6× |
| 125 | 4996 | 500 | 4.0× | 10.0× |
| 164 | 2337 | 650 | 4.0× | 3.6× |
| 203 | 3082 | 650 | 3.2× | 4.7× |

The worst observed speech/floor ratio was **14.2×**, which is what leaves
room for both margins at once. At the user's quiet floor of 33 the rule
yields a gate of 132 — close to the 105 they had already verified by hand.

**A correction to my own first analysis:** it paired floor 203 with speech
607 and concluded the rule failed. Those came from *different sessions* —
mic gain drifts and moves floor and speech together, so cross-session
pairing is meaningless. Re-done per session, every pairing passes.

`gate_min` (90) stops a muted mic driving the gate toward zero, where
everything false-starts. `gate_max` (650) stops a very noisy room making
SARA deaf.

### D47 — and the old formula never got there anyway

The ±150 step clamp plus `_RECALIB_FIRST_INTERVAL` (15 s) and
`_RECALIB_INTERVAL` (300 s) means moving 600 down to a floor-derived ~283
takes three cycles — **about ten minutes** — and inside a
`bench/stt_doctor.py` run the engine does not live long enough for even the
first. So within any session a user experiences, the gate was simply
`BARGE_IN_ENERGY_THRESHOLD`, which is exactly why that `.env` value was the
only thing that helped a quiet microphone.

**Fix:** a fast first pass at `STT_RECALIB_FIRST_FAST_S` (1.5 s —
`_NoiseFloor` needs 5 samples and the ring fills at ~31 chunks/s), allowed
to skip the step clamp because the clamp is a stability device for the
steady state and at boot there is nothing yet to stabilise. Every later
cycle keeps the clamp, so ordinary drift stays gradual. The fast pass is
guarded by the same `_is_listening` / `_tts_active` checks as the loop, so
it cannot calibrate against the user's voice or SARA's own.

Verified end to end with the flag on:

```
[STT] startup calibration: noise floor 33 -> threshold 377 (start gate 132)
```

### Why it ships OFF

It changes the gate on **every** capture, on a pipeline that had only just
started working end to end, for a person who could not test it at the time.
That is the one case where defaulting on would be a bet rather than a fix.

This is **not** D32 repeating itself. D32 was a finished subsystem switched
off with no note, no tests against real data and no way to know. This has a
33-test matrix built from the target machine's own measurements, a startup
log line that states the result, and one `.env` line to enable:

```
VOICE_GATE_NOISE_RELATIVE=True
DEBUG_MODE=True
```

If commands still start reliably, `BARGE_IN_ENERGY_THRESHOLD` stops
mattering and quiet or noisy rooms are handled without hand-tuning. If they
do not, setting it back to `False` restores the previous behaviour exactly
— pinned by a test, so the off-path cannot rot.

**Tests.** 33 in `tests/test_noise_relative_gate.py`: the D49 defect itself
(the additive formula must still invert above 135, or the rationale is
stale), every measured pair against both the start and continuation gates,
both clamps, the uncalibrated case, the division guard, the D47 cadence and
clamp-skip, the listening/speaking guard, every knob being a real Config
field, and that the default stays off.

### Validation

* full suite **2462 passed, 19 skipped, 0 failed**
* `bench/run_bench --mode router`: **98.8% / en 98.2% / hi 98.9% /
  hinglish 100.0% / 0/60** — unchanged

---

## D60 — FIXED: unbounded printing from the real-time audio callback

The project's brief is explicit: *"Keep audio-device callbacks minimal and
non-blocking. Do not run model inference, disk I/O, network requests,
long-running locks, or expensive processing inside real-time audio
callbacks."* Both capture callbacks broke it in two places.

**The stream-status report** was a raw `print()`:

```python
if status and Config.DEBUG_MODE:
    print(f"[STT] input stream status: {status}")
```

`status` is set on overflow/underflow — **exactly when the audio device is
already struggling**. The callback runs ~31 times a second (512 samples at
16 kHz = 32 ms) and a `print()` to a Windows console costs milliseconds
against that 32 ms budget. So a stream in trouble printed 31 lines per
second *from the audio thread* and made the trouble worse: a
self-worsening diagnostic. The target machine runs with `DEBUG_MODE=True`,
so this was live there.

**The AEC-drop warning** was throttled, but by count:

```python
if Config.DEBUG_MODE and self._aec_drop_count % 50 == 1:
    print(...)
```

A count-based limit is not a limit from the audio thread's point of view —
at 31 chunks/s a sustained AEC stall still printed every ~1.6 s, while the
stall was in progress.

**Fix.** Both now go through `_log_rate_limited()`, which already existed
in this module for the AEC error paths with a 30 s interval — reused rather
than adding a second mechanism. Still `DEBUG_MODE`-gated, so a healthy run
costs nothing here. The drop **counter** is still incremented
unconditionally, so nothing is lost silently; only the report is throttled.
Separate limiter keys per backend, so the sounddevice path cannot suppress
the pyaudio path's report.

**Deliberately not changed.** When AEC is unavailable
`_ingest_processed_chunk` runs on this thread and `_passes_barge_in_gate`
does an RMS plus a webrtcvad call during TTS. Both are bounded C-level work
on a 30 ms frame, the existing comments say so, and with the AEC wheel
present the worker thread handles it instead.

**Tests.** 14 in `tests/test_audio_callback_is_realtime_safe.py` —
no `print(`, rate limiting present, still debug-gated, no blocking put, no
inference or I/O, drops still counted, a real limiter interval, and
distinct keys per backend.

**Two of those tests initially failed for two different reasons**, which is
worth recording. Two caught a genuine gap in my own fix — I had converted
the status prints but left the AEC-drop prints, so `print(` was still
present. The other two were my test being imprecise: a bare `"put("`
substring check false-matched `put_nowait(` itself. Fixed the code for the
first pair and the test for the second, rather than relaxing either.

### Validation

* full suite **2476 passed, 19 skipped, 0 failed**
* `bench/run_bench --mode router`: **98.8% / en 98.2% / hi 98.9% /
  hinglish 100.0% / 0/60** — unchanged

---

## D61 — FIXED: a dead worker thread made SARA silently deaf

`_aec_worker_loop`'s body was unguarded:

```python
chunk = self._aec_raw_q.get(timeout=...)
processed = self._apply_aec(chunk)
self._ingest_processed_chunk(processed)
```

When AEC is enabled — the default, and what the target machine runs — this
thread is the **only** route microphone audio takes from the real-time
callback into the capture ring. It is a daemon thread and nothing
supervises it: the mic-disconnect watchdog probes the *stream*, not this
worker.

So one unexpected exception killed the thread and left SARA **permanently
deaf** — device still open, callback still firing, GUI level meter still
moving, and nothing logged. That is the same shape as D34/D35, which cost
about twelve days before it was found. `_apply_aec()` already swallows its
own errors, but `_ingest_processed_chunk()` does not: it runs
`_passes_barge_in_gate()` (numpy over raw bytes) and the ring/pre-buffer
writes, any of which could raise on a malformed or short chunk.

**Fix.** One bad chunk costs one chunk, not the microphone. Rate-limited,
because if it recurs it recurs ~31 times a second. `_recalib_loop` got the
same treatment one severity lower (a dead thread there freezes the energy
threshold rather than killing audio), including the startup call added
earlier in this same audit — unguarded, that addition would have killed the
thread before the steady-state loop ever ran.

**Tests.** 9 in `tests/test_worker_loops_survive_errors.py`, including a
behavioural one that feeds a raising chunk mid-stream and asserts every
later chunk still arrives, and one that asserts 40 consecutive failures
produce at most 2 log lines.

---

## D62 — FIXED: a genuinely racy test, not a timing-sensitive one

Earlier in this session
`test_no_stale_audio_from_a_stopped_stream_reaches_the_next_one` was
recorded as "flaky but not wrong" after passing 3 × 10 times under load.
**That was the wrong conclusion**, reached from too weak a reproduction. A
harder one — 25 runs with 2× CPU-count busy threads — reproduced it at
**3/25**, and the failing assertion was not the one expected:
`drive(player, thread2)`, the **second** stream never finishing.

**Two things conspire.** `FakePlayer.enqueue()` parks on `self.gate`
*before* appending, and `player.clear()` (which `tts.stop()` triggers) sets
that gate — so the stopped stream's enqueue is released but runs on the
player worker, which `thread.join()` does not cover. And `make_tts()` hands
out `ThreadPoolExecutor(max_workers=1)` **shared across both streams**. So
starting the second stream while the first is still draining leaves it
waiting for a worker it cannot get, and `drive` times out.

**Fix.** A `wait_settled()` helper that waits for the player to go quiet
before the second stream starts — which covers both halves: the stale
append has landed, and the first stream's single worker is free. The test
also now **asserts** the first thread joined, so if a stopped stream
genuinely fails to unwind the failure says that, instead of surfacing
confusingly on the second stream.

Measured: **3/25 → 0/25** under the same saturation, and 0/25 for the whole
file. Raising `TIMEOUT` (already 10 s) would only have widened the window.

**A note on my own comment.** The first version of it explained the fix via
the snapshot race, which is real but was *not* the failing assertion. The
comment and the helper docstring now describe the measured mechanism.
Writing a plausible-but-wrong explanation into the code is the same defect
class as D47's misleading comment and D51's wrong claim, both corrected in
this audit — so it gets corrected here too rather than left to mislead.

**Noted:** my own D61 tests spawn real threads and lengthened the suite
(31 s → 43 s), which made this pre-existing flake fire more often. That is
how it was caught.

### Validation

* full suite **2485 passed, 19 skipped, 0 failed**
* `tests/test_tts_stream_properties.py` **0/25 failures** under 2×CPU
  saturation (was 3/25)
* `bench/run_bench --mode router`: **98.8% / en 98.2% / hi 98.9% /
  hinglish 100.0% / 0/60** — unchanged

---

## Status reconciliation + D65 + D40 — 29 Oct 2026, unattended

The user went to sleep and asked for the audio pipeline to be made smarter,
faster and more accurate without them, including: *recheck and verify my own
work, find my own mistakes, fix them, verify again.* This section is that
pass.

### The self-review found four of my own errors — all in the findings, not the code

A context compaction mid-session dropped the record of work that had already
shipped, and the recent batches then wrote **"OPEN" against findings that
were already fixed**. That is a real documentation defect, and an expensive
one: it sent this very session off to re-design two fixes that existed.

| finding | said | actually |
| --- | --- | --- |
| **D32** | OPEN, "next batch" | FIXED by D41 |
| **D46** `utt_ms` | OPEN, "deferred" | FIXED — measured in bytes |
| **D47** slow calibration | OPEN, "must not ship before D49" | FIXED — fast first pass exists |
| **D49** gate below noise | OPEN, "fix with D47" | FIXED — `suggested_threshold_relative()`, 33 tests |
| **D52** preview on CPU | OPEN | FIXED — CUDA attempt + fallback, 13 tests |

Two further corrections to claims I made in those batches:

* **D51 said "the reported rule is not the rule that was applied."** That is
  **wrong**. `rule` is recomputed after `decide_endpoint()` returns True, and
  at that moment the partial *had* landed, so `rule=complete` was the rule
  actually applied — at 640 ms, the first chunk after the partial arrived.
  The log was honest; the preview was slow. Corrected below.
* **D58 said the override also suppresses recalibration for ten minutes.**
  It does not — `history.py` uses the plain setter, not
  `set_manual_energy_threshold()`. Already corrected in that entry.

I also re-derived my own **D45 margin table** against the shipped
implementation and found my proposed multiplier (K = 2.0) **worse** than the
K = 4.0 already in the code: I had paired each noise floor with speech
measured in a *different* session, which is not a comparable ratio. The
shipped version pairs them within one session. Nothing changed; the existing
code was right and mine would have been a regression.

### Verified sound, not just assumed

`grep` for every remaining VAD call site: after D39 and D45 the only two are
`_passes_barge_in_gate` and `is_user_speaking`, and **both test energy
first** and use the VAD only to narrow an energy-passed decision. So the
invariant "webrtcvad may never open or hold a capture by itself" now holds
uniformly across the engine. That was the thing most at risk of being left
half-done, and it is not.

`_sweep()` and `_flush_at_exit()` were tested properly rather than by my
earlier 6-second repro: a tool-only voice turn (no `tts_done`) persists at
t+23 s via the idle sweep, and `atexit` covers shutdown. **So D53b's
"deferred by one turn / last turn always lost" was wrong** — the 20 s grace
is deliberate, so `ttfa_ms` can still be recorded, and both halves of the
mechanism already existed. My repro simply did not wait long enough.

### D65 — ~~FIXED: the noise-relative gate is now ON by default~~ REVERTED BY D71

> **Superseded. Do not read this section as current.** The flag is `False`
> again. The reasoning below contains a specific error — the reference gate
> of 105 was `bench/stt_doctor.py`'s, not the application's (278), so this
> justified a 2.6× cut using a number from a different program. The user
> heard the result as added latency. See **D71** for the correction; the
> margin table here is still the evidence for the rule itself, which is why
> this is kept rather than deleted.

`VOICE_GATE_NOISE_RELATIVE` shipped `False` because, as its own note said,
the pipeline it changes had only just started working and the person who has
to hear the difference could not test it. The pipeline now works end to end,
so that reason has expired.

**Why enabling is the low-risk direction.** The user had already hand-tuned
`BARGE_IN_ENERGY_THRESHOLD=300` in `.env` to get past a quiet microphone —
a start gate of **105** — and that is the configuration their one fully
successful run was measured on. In that same room (floor 24–33) this rule
derives **96–132**. Enabling it lands within a few percent of a value already
verified on that hardware, and removes the need to hand-tune it.

It also repairs both failure modes they actually hit, which no single fixed
threshold can do at once:

| observed | with a fixed gate | with the rule |
| --- | --- | --- |
| quiet speech RMS 607, gate 210 | 8 s timeout | gate 132 → 4.6× margin |
| room noise 203, gate 105 | false start | gate 650 → 3.2× over noise |

**Not proven:** no A/B against the fixed threshold on that machine. One
`.env` line reverts it, and the additive formula remains the else-branch,
pinned by a test.

### D40 — FIXED: `VAD_AGGRESSIVENESS` is a real field now

Declared with `default=2` (unchanged behaviour — D40 was about making the
knob real, not retuning it) and clamped to webrtcvad's own 0–3 range in
`validate()`, because `webrtcvad.Vad()` raises outside it and is constructed
during boot: declaring the field without clamping would have turned a typo
in `.env` into a startup crash, which is worse than the silent default it
replaced. `_VADFilter` keeps its own clamp too, since it cannot assume
`validate()` has run.

Scope, so it is not misread: after D39/D45 this no longer affects whether
SARA hears a command. It tunes barge-in sensitivity.

### D51 — ~~the preview latency is now measured instead of inferred~~ SUPERSEDED BY D72

> **The instrument this section added was wrong, and so is its reasoning.**
> "Every command fell back to `base` (625 ms)" is false — a late partial is
> still used — and 625 ms is the fallback base, not the one the engine
> passes. The log line quoted below no longer exists; D72 replaced it with
> one that races the two rules and reports what the preview was actually
> worth. The *idea* of measuring rather than tuning was right, and it is
> what made the correction possible.

Derived from the target machine's own trace: the preview is spawned at the
first chunk whose trailing silence reaches `ENDPOINT_PREVIEW_AT_MS`
(150 ms → the 160 ms boundary), and the endpoint fired at `silence_ms=640`
with `rule=complete` — i.e. the instant the partial landed. **So the
transcribe took about 480 ms.** For `rule=complete` to fire at
`ENDPOINT_MIN_MS` the partial must arrive within 350 − 160 = **190 ms**, so
480 ms is **2.5× too slow**, and that is why every command fell back to
`base` (625 ms) and the designed 288 ms saving was never collected.

D52 has since moved that model onto the GPU, so whether 190 ms is now met is
measurable. `_spawn_preview_transcribe` now logs it under `DEBUG_MODE`:

```
[STT] preview took NNNms (budget 190ms for rule=complete to fire at
      ENDPOINT_MIN_MS) -- in budget | TOO SLOW
```

**Deliberately not tuned yet.** `ENDPOINT_PREVIEW_AT_MS` could be lowered to
buy headroom, but measuring before tuning is the whole discipline that caught
D45 and D59; one run of the real app now answers it.

### Validation

* full suite **2492 passed, 19 skipped, 0 failed**
* `bench/run_bench --mode router`: **98.8% / en 98.2% / hi 98.9% /
  hinglish 100.0% / neg_false_triggers 0/60**
* the suite was also run with `VOICE_GATE_NOISE_RELATIVE=False` to confirm
  the only difference is the deliberate default tripwire
* new tests: 7 (D40)

**Correction to an earlier report:** I quoted "2380 passed" in the previous
batch. The true collected total is 2504 (2492 pass, 19 skip, 1 deliberate
tripwire that moves with the flag). The earlier figure came from runs made
before some of this session's own test files were present in the mirror.

---

## D63 — FIXED: D65 made interrupting SARA impossible in one measured room

**This is a regression I introduced tonight, caught by checking my own
change's second-order effects rather than by anything failing.**

D65 made `energy_threshold` track the room noise, which is correct for the
**capture** gate — it has to sit above room noise or capture false-starts.
Barge-in then inherited it, because `_passes_barge_in_gate()` and
`is_user_speaking()` both multiplied `self.energy_threshold` by
`TTS_BLEED_GUARD_MULTIPLIER` (1.6) — a multiplier tuned against a **fixed**
threshold.

Those are different quantities. Barge-in must clear SARA's own **TTS echo
bleed**, which is bounded by playback level and has nothing to do with the
room. Against the seven floor/speech pairs measured on the target machine,
with `BARGE_IN_ENERGY_THRESHOLD` at its 600 default:

| noise floor | measured speech | D65 barge-in gate | margin |
| --- | --- | --- | --- |
| 125 | 4996 | 2286 | 2.19× |
| **164** | **2337** | **2971** | **0.79×** ← gate above the user's own voice |
| 203 | 3082 | 2971 | 1.04× |

At floor 164 the gate sat **above** the speech level, so in that room the
user could not have interrupted SARA at all. D65 would have traded a
capture bug for an interruption bug.

**Fix.** A new `_barge_in_threshold()` caps it at
`Config.BARGE_IN_ENERGY_THRESHOLD` — the field literally named for this job.
The cap is **one-sided on purpose**: barge-in may become *easier* when
calibration lowers the threshold in a quiet room, but never *harder*. That
restores the pre-D65 ceiling — the value that was observably working, since
telemetry recorded `barge_in` outcomes — and leaves D65's capture-gate fix
untouched.

| noise floor | measured speech | capped gate | margin |
| --- | --- | --- | --- |
| 125 | 4996 | 960 | 5.20× |
| 164 | 2337 | 960 | **2.43×** |
| 203 | 3082 | 960 | 3.21× |

Only the barge-in branch is capped. `is_user_speaking()`'s TTS-idle branch
still uses the calibrated threshold, because "is the user speaking" is a
capture-side question — capping it there would partly undo D65.

**Tests.** 13 in `tests/test_barge_in_threshold_cap.py`, including that
every measured room can still interrupt, that the uncapped gate really was
above the measured speech (so the test keeps demonstrating the defect), and
that both call sites multiply the *same* helper so they cannot drift.

---

## Also examined tonight and deliberately left alone

**TTS prewarm contention.** `[TTS] prewarmed 52 phrases in 58.4 s` overlaps
the first minute of use, so it looked like a latency risk. It is not:
`_wait_turn()` blocks while `tts._speaking` is set, is re-checked before
every phrase, and there is a 30 ms gap between phrases. Synthesis does
overlap STT, but STT is on CUDA and Kokoro on CPU, so they are not competing
for the same device. No change — there was no defect to fix here.

**Ollama streaming.** Verified as genuine incremental streaming:
`client.chat(..., stream=True)` with a generator yielding each chunk's
content as it arrives, not a collect-then-split. `think=False` is also set
explicitly, which independently closes the old worry about qwen3 `<think>`
tags reaching TTS. `model first token p50 2360 ms` is the model's own prefill
on a 4B quantised model with a RAG + history prompt, not a code defect.
Reducing prompt size would trade context quality for latency — a product
decision, not mine to make unattended.

**Phonemizer warnings.** Every synthesis logs `words count mismatch on
100.0% of the lines`, `extra phones may appear in the "hi" phoneset` and
`language switch flags have been kept (applying "keep-flags" policy)`. The
last one means language-switch markers are left in the phoneme stream, which
could plausibly degrade Hinglish pronunciation. **Not changed:** SARA does
not configure phonemizer — `kokoro_onnx` does, internally — so altering it
means monkeypatching a third-party library's internals, and the result is
only verifiable by *listening*, which cannot be done from here. Recorded
with the exact experiment: set `language_switch="remove-flags"` in
kokoro_onnx's phonemizer call and compare Hinglish playback by ear.

**The flaky TTS stream test — already diagnosed, see D62 above.** It failed
once in a full-suite run tonight and then passed 7 further full-suite runs,
3 of them under deliberate CPU saturation of every core. I initially
recorded that as "a rare flake", which was **the same wrong conclusion D62
had already corrected earlier in this session** from a weak reproduction.
`wait_settled()` is wired into it at line 200 and the hazard is documented
there. The single failure tonight is consistent with the residual window
D62 describes, not with a new defect — but "rare flake" is not the finding;
D62 is.

### Validation

* full suite **2505 passed, 19 skipped, 0 failed**
* 7 full-suite runs tonight, 3 under full CPU saturation, all green
* `bench/run_bench --mode router`: **98.8% / en 98.2% / hi 98.9% /
  hinglish 100.0% / neg_false_triggers 0/60** — unchanged all night
* new tests tonight: 7 (D40) + 13 (D63)

---

## D64 — FIXED: the D42 defect existed in a second place, and there it was worse

Found by following through on D63 rather than by anything failing: checking
what else could inflate the barge-in gate led to `_barge_boost`, which led
to `barge_watch._aec_available()`:

```python
return getattr(self._ears, "_aec", None) is not None
```

**Exactly the D42 defect**, in a file D42 did not touch. `AECProcessor`
constructs successfully with `enabled=False` when the
`aec-audio-processing` wheel is missing, so this read `True` on a machine
doing no echo cancellation at all.

**The consequence is the opposite of D42's, and worse.** This decides
whether to apply `BARGE_NOAEC_THRESHOLD_BOOST` (1.5), which exists to stop
SARA's own voice tripping barge-in when nothing is cancelling the echo.
Reading `True` meant the boost was **skipped exactly when it was needed**,
so SARA could interrupt herself mid-reply and take her own speech as the
user's next command. D42's version made SARA too eager to shortcut; this one
made her interrupt herself.

The `hasattr` escape hatch is kept: a stand-in `ears` with no `_aec`
attribute is a test double, not a machine without AEC, and must not be
pushed onto the no-AEC path.

Also fixed, same family, one line: `tts/engine.py` printed
`AEC: wired` whenever an object was handed in. It now distinguishes
`not provided` / `wired` / `provided but DISABLED (no echo cancellation)`.

### The guard I wrote, then had to narrow — worth recording

My first version of the regression test grepped the whole package for
`_aec … is not None` and flagged five more sites: the AEC worker thread
start in `stt/engine.py` and three far-end feeds in `tts/player.py`.

Those are **legitimate**. Every `AECProcessor` method that touches audio
(`feed_far_end`, `process_near_end`, and the others) returns early on
`if not self.enabled`, so handing data to a disabled processor is a no-op —
wasteful at worst, not wrong. A guard that fails on correct code is worse
than no guard, because it teaches people to edit the guard.

So the test is now scoped to the three places that **decide behaviour** from
AEC availability (`core_wiring.aec_active`,
`barge_watch._aec_available`, the STT `Ready` line), plus a second test
asserting that `AECProcessor`'s data methods still guard on `enabled` —
which is *why* the narrower scope is honest. If one of them ever stops
guarding, those five sites become real defects and that test fails.

**Tests.** 9 in `tests/test_aec_active_gate.py` (4 new).

### Validation

* full suite **2509 passed, 19 skipped, 0 failed**
* `bench/run_bench --mode router` unchanged: 98.8% / en 98.2% / hi 98.9% /
  hinglish 100.0% / 0/60 false triggers

---

## Correction: I reused three finding numbers tonight

Working unattended after a context compaction, I numbered three new findings
**D60, D61, D62** — all three of which were already taken by work from
earlier in this same session:

| number | already meant | what I wrongly gave it | renumbered to |
| --- | --- | --- | --- |
| D60 | unbounded printing from the real-time audio callback | the noise-relative gate flag flip | **D65** |
| D61 | a dead worker thread made SARA silently deaf | the barge-in threshold cap | **D63** |
| D62 | a genuinely racy test, not a timing-sensitive one | the second AEC existence check | **D64** |

Renumbered across `FINDINGS.md`, the code comments and the test files. The
pre-existing D60/D61/D62 comments in `sara/audio/stt/engine.py` were left
untouched; only the sites I wrote tonight moved.

This is the same failure mode as the stale `OPEN` statuses at the top of
this section — a compaction dropped what had already shipped, and I wrote
over it. It is worth recording rather than quietly fixing, because a finding
number that means two different things is how the next person loses an hour.

**Note on the remaining duplicate headings:** `grep` still reports D40, D46,
D47, D49, D51, D52, D54–D59 as duplicated. Those are **not** collisions —
each is a finding section plus a later `### Dnn — FIXED` note about the same
subject, which is the cross-referencing convention this document already
uses. Only genuinely different defects sharing a number needed fixing.

**Also corrected tonight:** I had written that the flaky TTS stream test was
"a rare flake, recorded rather than chased". That repeated the wrong
conclusion **D62 above had already overturned** from a stronger
reproduction. Corrected in place.

---

## D66 — FIXED: after D65, two startup log lines contradicted each other

Following D65's consequences further (the method that found D63 and D64)
led to the two startup writers of `energy_threshold`:

```
[STT] Ready — ... threshold=300
[Debug] Restored mic sensitivity: 23% (threshold=793)
[STT] startup calibration: noise floor 29 -> threshold 331 (start gate 116)
```

The order is **deterministic, not racy**: the saved GUI preference is
restored right after `SpeechToText.__init__` returns, and the fast startup
calibration fires `STT_RECALIB_FIRST_FAST_S` (1.5 s) after the mic stream
opened — so calibration lands last and wins.

**That ordering is correct and stays.** A *live* slider move still sticks:
`Api.set_mic_sensitivity` goes through `set_manual_energy_threshold()`,
which suppresses recalibration for 600 s. A value merely **restored** at
startup does not suppress it, and should not — a stale saved number ought to
yield to a fresh measurement of the actual room.

**What was wrong was the reporting**, and one line of it was my own from
earlier tonight:

* `history.py`'s D58 message ended *"Change it with the GUI slider, not
  .env."* — **wrong advice the moment D65 shipped**, since the slider's
  restored value is superseded too. It now says calibration will re-derive
  the value and that moving the slider overrides it for 10 minutes, and only
  says the old thing when the adaptive gate is off.
* the calibration line now names what it replaced
  (`, superseding 793`) or says `(unchanged)`, so the two lines can no
  longer be read as disagreeing about which value is live.

No behaviour changed. Three log lines that described three different
thresholds now agree about which one is in force — which is the whole
difference between a log you can debug from and one that misleads you, as
D37, D42, D55 and D64 each demonstrated tonight.

### Validation

* full suite **2509 passed, 19 skipped, 0 failed**

---

## D67 — the zero-voice-rows cause, narrowed to one silent fallback

The D53a diagnostic did its job and **eliminated my leading hypothesis**:

```
tracing switch : ON (saved GUI preference)
                 So zero voice rows means voice turns are not
                 reaching the recorder -- not that tracing is off.
```

Note also that the figures were **byte-identical** to the 05:58 run eight
hours earlier (16 turns, p50 922 / 2360 / 1250 / 4156), so the 05:54 voice
session wrote nothing at all, not even a partial row.

### What is now ruled out, each by measurement rather than reasoning

| candidate | eliminated by |
| --- | --- |
| tracing switched off | the saved preference reads `'1'` |
| an early exit between `listen()` and `begin_turn` | read the path: no `continue`/`break`/`return` between `_t_begin("voice")` and `_t_end("ok")` |
| the store giving up (`ok = False`) | that logs `[telemetry] persistence disabled`; the session log has no telemetry line at all |
| retention pruning fresh rows | `_retention_days()` is clamped to `max(1, …)`, so it cannot delete anything from today |
| the deferred-finalise / lost-last-turn theory (my D53b) | tested to t+28 s: a tool-only turn with no `tts_done` persists at t+23 s via the idle sweep, and `_flush_at_exit` is registered with `atexit` |
| rows written under a different `source` | the voice path passes `lang=`, the GUI path does not, and **all 16 rows have `lang` NULL** |

### What is left

`core_wiring.py` wraps the recorder import in a bare `except Exception` that
installs **no-op stubs**:

```python
try:
    from sara.core.telemetry import begin_turn, end_turn, mark_pending
except Exception:          # printed NOTHING
    def _t_begin(*a, **k): return ""
```

The fallback is right — telemetry is genuinely optional — but it was
**silent**, so a failure there discards every voice turn with no error
anywhere, which from outside is indistinguishable from a voice problem.
It is also the only remaining candidate that produces *exactly* this
signature: nothing written, nothing logged, and the GUI's own text rows
surviving from an earlier session when the import did work.

### Three instruments added, so one run settles it

1. **The fallback is loud.** It now prints
   `[Telemetry] UNAVAILABLE -- voice turns will NOT be recorded this
   session: <error>`.
2. **`bench/voice_latency.py` probes the import** the way `core_wiring`
   does — importing the package and checking **all three names**, because a
   package that imports but lacks one name fails that statement and takes
   the whole no-op path, so probing a single name would not reproduce it.
   Verified in both directions: it prints `recorder import : ok` normally,
   and under a shim that deletes `mark_pending` it prints
   `*** FAILED *** ImportError: cannot import name 'mark_pending'`.
   **This answers the question in ten seconds without running SARA.**
3. **The turn id is logged at begin and end** under `DEBUG_MODE`. The no-op
   stub returns `""`, the real `begin_turn()` returns a 12-hex id, so
   `[Telemetry] voice turn begun: <<NO-OP STUB -- not recording>>`
   separates "the recorder is not there" from "the recorder is there and
   the row is lost later". Pairing it with
   `[Telemetry] voice turn ended: …` also rules out a turn that begins and
   never ends, which `_sweep()` would drop since it only finalises turns
   with `ended_at` set.

**Status:** OPEN — see the update below. The import probe came back `ok`, so
that candidate is eliminated too.

### Update, 14:41 — my labelling hypothesis was WRONG

The probe printed `recorder import : ok (begin_turn, end_turn,
mark_pending)`, so I went looking again and noticed the counts lined up
suspiciously well with the 05:54 voice session:

| the bench said | the session log had |
| --- | --- |
| 16 "TEXT" turns | **16** non-empty transcripts |
| `tool execution n=3` | **3** `Launched application` lines |
| `languages: ?=16` (all NULL) | the voice path sends `brain.get_language`, which does not exist → NULL |

I concluded those 16 rows **were** the voice turns, mislabelled — which
would have meant the baseline existed all along. `dump --last 20` settled
it:

```
27 23:27:07  text  llm          chat             ok
17 12:00:39  text  tool_router  open_app         barge_in
16 11:20:40  text  llm          chat             ok
```

**16, 17 and 27 October.** Not the 29th. Those are genuinely old GUI typing
sessions across three different days, and the 16=16 / 3=3 match was
coincidence. Third time tonight an inference of mine was wrong and the
measurement caught it — which is the system working, but worth writing down
plainly rather than quietly dropping.

### What the dump did establish, which is more useful

`--last 20` returned **16 rows — that is the entire table.** Every one is
`src=text`, and every one has `stt` NULL.

**So no voice turn has EVER been recorded**, not in the 05:54 session and
not on 16/17/27 Oct either. This is therefore **not a regression from this
audit** — the voice path's telemetry has never worked.

### Also eliminated since

| candidate | eliminated by |
| --- | --- |
| `_record()` raising on a voice turn's richer stamp set | drove a full 12-stage voice turn end to end: row lands at **t+3 s** with `source='voice'`, `intent='open_app'`, `ttfa_ms` populated |
| `core_wiring` getting the no-op stub | imported it in isolation: `_t_begin.__module__` is `sara.core.telemetry.trace`, the real one |
| SARA writing to a different `telemetry.sqlite` than the bench reads | both resolve through `config.app_data_subdir("data")`, the same folder their `sara_data.db` is in |

So the recorder works, the voice path reaches it, and the row still does not
appear. **Nine candidates are gone and the remaining one is specific to
their running process**, which no amount of reading from here can settle:
either the import fails *in main.py's import order* (it succeeds in mine),
or `begin_turn` is reached and the row is lost downstream in that process.

D67's three instruments distinguish exactly those two, so **one
`python main.py` run answers it.** I am deliberately not guessing a fix:
patching an import I cannot see fail is precisely how D45 and D59 went
wrong.

### Validation

* full suite **2509 passed, 19 skipped, 0 failed**
* router bench unchanged: 98.8% / en 98.2% / hi 98.9% / hinglish 100% / 0/60
* the import probe exercised in both directions

---

## D68 — ANSWERED: tracing was off, and my own two instruments misreported it

One `python main.py` run settled D67, and it was not an import failure:

```
[Telemetry] recording OFF -- no turns will be measured this session.
```

`_enabled` is initialised from **`Config.TELEMETRY_ENABLED`** at module
import (`trace.py:68`), so `TELEMETRY_ENABLED` in their `.env` is what
disabled it. The saved GUI preference reads `'1'`, which is why the bench
and the app disagreed: the preference is only re-applied later, when the
performance panel first asks for a summary, and in a session where the panel
is never opened it never takes effect at all.

**Fix for them: `TELEMETRY_ENABLED=True` in `.env`.** That is all.

### Both of my instruments were wrong, in opposite directions

**The message named the wrong source.** It said *"Turn it back on in the
GUI's performance panel (the saved preference wins over TELEMETRY_ENABLED)"*
— and here the saved preference was ON while config was OFF, so it pointed
at the wrong file and cost a round trip. It now prints **both** values and
marks which one is in force:

```
[Telemetry]   TELEMETRY_ENABLED (.env/config) = False   <-- this is what disabled it
[Telemetry]   saved GUI preference            = '1'
[Telemetry]   fix: set TELEMETRY_ENABLED=True in .env and restart
```

**The turn-id log looked like proof of recording when nothing was
recorded.** `begin_turn()` allocates and returns an id *before* it checks
whether recording is on:

```python
turn_id = new_turn_id()
if not _enabled:
    return turn_id      # a real 12-hex id, and nothing written
```

So the log printed `voice turn begun: 63e0660b8974` for a turn that was
never stored — the exact opposite of what I built it to distinguish. It now
asks the recorder directly and prints
`63e0660b8974 <<NOT RECORDED -- tracing off>>`.

Both were the same mistake I have been fixing all night in other people's
code: a report that cannot be trusted is worse than no report. Two of
tonight's own instruments had it.

---

## D69 — FIXED: the startup calibration lost a race and silently gave up

The same run has **no `[STT] startup calibration:` line at all**, and the
gate stayed where the GUI slider put it:

```
[Debug] Mic sensitivity 23% ... -> 793; speech must now exceed RMS 278
[TTS] warm-up complete in 3.310s
```

The fast pass was a **single attempt** at `STT_RECALIB_FIRST_FAST_S` (1.5 s),
skipped silently if `_is_listening` or `_tts_active` was set. TTS warm-up
takes ~3.3 s on that machine, so the attempt lost that race **every time**.
The next chance is `_RECALIB_FIRST_INTERVAL` (15 s), by which point the user
is mid-command, and after that `_RECALIB_INTERVAL` (300 s). In a 50-second
session the calibration never ran — so **D65's entire benefit was lost to a
one-shot that lost a race with an unrelated warm-up.**

**Fix.** The fast pass now polls for a bounded window
(`STT_RECALIB_FIRST_FAST_WINDOW_S`, 12 s — comfortably clearing a 3.3 s
warm-up plus the first wake cycle) and runs the moment the engine is idle.
The idle guard stays: calibrating during a live listen would sample the
user's own voice as the noise floor. If it does give up, **it now says so**
— the silent skip is why this went unnoticed for a whole session.

**Tests.** 7 in `tests/test_startup_calibration_retry.py`, including that
the window is bounded, is a real Config field, and clears a realistic
warm-up; plus that the idle guard and the steady-state loop both survive.

**Two existing tests broke, and that was my fault, not theirs.**
`test_the_fast_pass_does_not_run_while_listening_or_speaking` and
`test_the_startup_calibration_call_is_guarded` both slice
`_recalib_loop`'s source at `_RECALIB_FIRST_INTERVAL` — and my new comment
*mentions* that name, so `str.index` found the mention first and truncated
the window before the code under test. Their assertions were still true.
Both now strip comments before slicing, which is the same fix I applied to
my own tests earlier tonight.

---

## What that one run also confirmed working

| change | evidence in the log |
| --- | --- |
| **TTS on the GPU** (D55, their pip install) | `ORT provider : active=CUDAExecutionProvider` |
| **preview model on the GPU** (D52) | `Wake-word Whisper loaded on GPU (CUDA, int8_float16) -- live preview and wake checks share it` |
| **the endpoint fast path** (D51) | `endpoint rule=complete silence_ms=352` — **it fired**, on the turn where the preview came in at 218 ms. The "288 ms saving" attached to it here was wrong; see D72 |
| **no retry on a daily quota** (D54) | `server asked to wait 78222s (> max_delay 8s) -- not retrying, falling back now` |
| **foreign-script filter** (D43) | `Discarded foreign-script hallucination: 'ออบนคร้ม'` and `'โอเป็นครู'` — two Thai hallucinations stopped |
| **the override message** (D58/D66) | `OVERRIDES BARGE_IN_ENERGY_THRESHOLD 300 -> 793 … Startup calibration will re-derive this` |

### D51 is now a measurement, not a guess

> **The interpretation below is superseded by D72.** The distribution is
> real and still the evidence; what was wrong was the "200 ms budget"
> framing and the conclusion drawn from it. There is no cliff at 200 ms, the
> base is not 625 ms, and `ENDPOINT_PREVIEW_AT_MS` is **not** the next
> lever — against the 450 ms base that short commands produce, the whole
> saving is capped at 128 ms.

Seven preview timings in one short session: **218, 282, 313, 344, 375, 390,
485 ms** against a 200 ms budget. The GPU move helped — the best case now
*just* fits, and `rule=complete` fired once because of it — but the median
is still roughly 1.7× over. So the 288 ms saving lands occasionally rather
than reliably, and the next lever is `ENDPOINT_PREVIEW_AT_MS` (150 ms),
which buys headroom directly. **Measure first:** that is now a real
distribution rather than one inferred number.

### Validation

* full suite **2516 passed, 19 skipped, 0 failed**
* router bench unchanged: 98.8% / en 98.2% / hi 98.9% / hinglish 100% / 0/60
* new tests: 7 (D69); also removed a pre-existing unused import flagged by
  pyflakes in `test_worker_loops_survive_errors.py`

---

## D70 — one cause explains most of that session: there was music playing

Re-reading the 14:45 log, line 107:

```
[volume-match] title='Tu_Har_Lamha_Full_Video_-_Khamoshiyan...' artist='SR Lofi World'
[WakeDiag] RAW mic stream reached wake detector | chunks=4 | rms=980.1
```

A YouTube track was playing, reaching the microphone at **RMS ~980**. The
start gate that session was `793 × 0.35 = 278`. So **the music cleared the
gate by 3.5×**, and the arithmetic accounts for nearly every oddity in that
run at once:

| observed | explained by |
| --- | --- |
| `audio=5.58s text='Yes.' confidence=0.441` | the music, captured and mis-transcribed |
| `'ออบนคร้ม'` and `'โอเป็นครู'` (Thai) | a Hindi song heard as Thai — **both correctly discarded by D43** |
| `audio=0.12s text=''` | a brief music transient opening a capture |
| `Detected language 'de' 0.78`, `'th' 0.23`, `'nl' 0.27` | not speech at all |
| wake score `0.6586` against a 0.6 threshold | barely over, plausibly the music |

AEC cannot help: it cancels **SARA's own** TTS via the far-end reference.
Audio from Edge is a different process and never reaches it.

**So the practical guidance is: measure with the music off.** Otherwise the
numbers describe the song, not the pipeline.

### And calibration declined, in silence

`_run_one_recalibration()` bails when the sampled average exceeds half the
threshold — `980 > 793 × 0.5 = 396`, so it bailed. That guard is **correct
and stays**: without it the gate could be derived from the user's own voice
and would climb until SARA went deaf. But it fires identically for sustained
room audio, and it **returned silently**, so nothing anywhere said the room
was simply too loud to measure.

It now says so, rate-limited and `DEBUG_MODE`-gated, with the three numbers
needed to act — measured level, the threshold it is compared against, and
the resulting start gate:

```
[STT] calibration declined: the room averaged RMS 980, over half the
      current threshold 793, so it cannot be told apart from speech. The
      gate stays at 793 (start gate 278) -- anything louder than that opens
      a capture, so music or a fan nearby will produce spurious transcripts.
```

Note this also means **D69's retry would not have helped that session
either** — the attempt would have fired and `_run_one_recalibration` would
still have bailed. D69 fixes losing a race with the TTS warm-up; this is a
different reason for the same outcome.

### The limitation this documents, deliberately unfixed

Because the guard bails, `VOICE_GATE_NOISE_RELATIVE` can **lower** the gate
in a quiet room but can never **raise** it in a loud one — the loud-room
measurement is precisely the one it refuses to take. `_NoiseFloor.update()`
compounds it: it only samples chunks below `threshold × 0.4` (317 here), so
the floor estimate never sees the music at all.

Telling sustained noise apart from a person talking needs evidence across
several attempts (speech is intermittent, music is not). Getting it wrong
raises the gate until SARA goes deaf, which is the worst outcome in this
whole audit. **That wants its own batch and a real measurement**, not a
guess at 17:00 after three wrong inferences in one day.

**Tests.** 7 in `tests/test_loud_room_calibration_notice.py`, including that
the guard itself survives, that the notice is rate-limited (unbounded
printing is the D60 defect in a new place), and a replay of the measured
numbers proving the music both clears the gate and trips the guard while
never being sampled as the floor.

### Validation

* full suite **2523 passed, 19 skipped, 0 failed**
* router bench unchanged: 98.8% / en 98.2% / hi 98.9% / hinglish 100% / 0/60

---

## D71 — REVERTED: D65 made the gate too low and it was heard as latency

The only finding in this audit raised by **listening** rather than by
reading code, and the only one that reverses an earlier one of mine.

### The report

After the D68–D70 batch: the previous batch "felt fast", and after this one
*"thoda high letency lagg rahi h"*. That is a measurement — the pipeline's
purpose is to feel immediate to the person speaking into it, and they are
the instrument. It outranks my arithmetic, so the arithmetic is what I went
back to check.

### Which change could have done it

Of the three in the batch, **D68 and D70 are logging only**: a clearer
telemetry-unavailable message, a clearer calibration-declined message.
Neither can alter timing.

**D69 changed behaviour** — and not in the way its own note describes. D69
fixed a one-shot startup calibration that *always* lost a race with the
~3.3 s TTS warm-up and therefore never ran. Fixing it meant **D65's
noise-relative gate took effect for the first time**, having been merged
one batch earlier as dead code. So the batch the user felt was not D69's
retry loop; it was D65 finally arriving.

Measured on their machine: start gate **278 → ~96–132**.

### The specific error in D65's justification

D65 argued the drop was safe because the rule derives 96–132 in their room,
*"within a few percent of the gate 105 their successful run used"*.

Gate 105 came from `BARGE_IN_ENERGY_THRESHOLD=300` **as seen by
`bench/stt_doctor.py`** — and the doctor does not load GUI preferences. The
**application** restored the saved slider (23% → threshold 793) and ran at
a start gate of **278**. The application is what gets judged by feel.

So I justified cutting the application's gate by **2.6×** using a reference
point taken from a different program. The two numbers were in the same log
(D58 is the finding that made them both visible); I read the wrong one.

### Why a lower gate reads as latency rather than as misfires

Captures open on typing, a mouse click, a chair, a breath. Each false start
consumes a whole `listen()` window: it runs the silence budget (480–800 ms),
returns empty, retries. Speak during one and the command is clipped and has
to be repeated. From outside, none of that looks like a threshold problem —
it looks like lag.

### The fix: both defaults, not one

Turning the rule off is **not sufficient**, which is the half that is easy
to miss. The fast first pass runs with `first=True`, which deliberately
skips the ±150 step clamp, so even on the **additive** formula it jumps
straight to `floor + 250` ≈ 280 → gate **98**. Same 2.6× drop, other route.

| setting | was | now |
| --- | --- | --- |
| `VOICE_GATE_NOISE_RELATIVE` | `True` (D65) | `False` |
| `STT_RECALIB_FIRST_FAST_S` | `1.5` (D47) | `0.0` (disabled) |

A third change, found while verifying the second: `_recalib_loop` read
`getattr(Config, "STT_RECALIB_FIRST_FAST_S", 1.5)` — a fallback that now
disagreed with its own default, so a `Config` stand-in lacking the field
would run a fast pass production does not. Aligned to `0.0`.

### What this restores

| | threshold | start gate |
| --- | --- | --- |
| slider restore (~2 s) | 793 | **278** |
| first calibration (15 s, clamped) | 643 | **225** |
| +300 s | 493 | 173 |
| +600 s | 343 | 120 |

A short session therefore sits at 278 → 225, and long sessions drift down
gradually. This is the **original pre-audit behaviour**, and specifically it
is the behaviour they described as fast — because `STT_RECALIB_FIRST_FAST_S`
was already 1.5 before this batch, but the fast pass lost the TTS race every
time, so it never ran. Setting it to `0` makes explicit what was
accidentally true; it does not introduce a new state.

### What is NOT reverted

The machinery stays and stays tested: the noise-relative formula, the
`_NoiseFloor` ratio method, D69's retry loop, D70's declined-calibration
notice. Only the two **defaults** moved. D49's argument against the additive
formula (above a floor of ~135 the derived gate sits *below* the room noise)
is still correct and still unaddressed — it is a reason the rule should
eventually be on, not a reason to leave it on untested.

**Re-enabling requires a baseline first:** `TELEMETRY_ENABLED=True` in
`.env` (D68), a session's worth of recorded turns on the current gate, then
the same with `VOICE_GATE_NOISE_RELATIVE=True`, compared. Two numbers, not
an argument from margins.

### Tests

In `tests/test_noise_relative_gate.py`, replacing D65's tripwire:

* `test_it_ships_off_again_after_a_heard_latency_regression`
* `test_the_unclamped_fast_first_pass_is_also_off_by_default`
* `test_zero_actually_disables_the_fast_pass` — asserts `_recalib_loop`
  still guards on `fast_s > 0`. Without this, `0.0` would mean "wait no
  time, then calibrate immediately" and the revert would be a **no-op that
  reads as a fix**.
* `test_the_getattr_fallback_agrees_with_the_config_default`

Both new tests were **mutation-checked**: reinstating the `1.5` fallback and
removing the `fast_s > 0` guard each fail exactly one of them, and
`engine.py` was restored byte-identical afterwards.

### The lesson worth keeping

Two instruments disagreed by 2.6× and I took a number from the one that was
not being judged. A reference value needs its source named — "gate 105 *in
the doctor*" would not have survived being written down. This is the second
time in this audit that a claim of mine was wrong in a way the user's
hardware then disproved (D39 was the first), and in both cases the error was
reasoning about the pipeline from the wrong vantage point rather than a
mistake in the code.

### Validation

* `tests/test_noise_relative_gate.py` + `test_startup_calibration_retry.py`:
  **43 passed**
* full suite **2526 passed, 19 skipped, 7 subtests passed, 0 failed**
* router bench unchanged: 98.8% / en 98.2% / hi 98.9% / hinglish 100% / 0/60
* `pyflakes` clean on all three changed files
* written back to `C:\project` and verified byte-identical
  (`config.py` 97 407, `engine.py` 139 963,
  `tests/test_noise_relative_gate.py` 16 487); backups in
  `backups/pre-batch-D71/`

---

## D72 — CORRECTED: my own D51 instrument reported a cliff that does not exist

Two wrong instruments in a row, both mine, found by replaying the shipped
decision function instead of reasoning about it. The second error is the
same one as D71's: a number taken from a default rather than from the
running system.

### What D51 printed, and why it was wrong

```
[STT] preview took 313ms (budget 200ms for rule=complete to fire
      at ENDPOINT_MIN_MS) -- TOO SLOW
```

with the conclusion, in D51's own note, that overshooting that budget meant
the command *"fell back to base (625ms)"* and the designed **288 ms** saving
*"was never collected"*.

`_endpoint_reached()` re-reads `ep["partial"]` on **every 32 ms tick**. A
partial that arrives late is still used the moment it lands, so the endpoint
fires at about `ENDPOINT_PREVIEW_AT_MS + preview_ms` — later than ideal, far
earlier than base. The budget is not a cliff; overshooting it costs the
overshoot.

The line said `TOO SLOW` on **7 of 7** measured previews while the fast path
was firing on **7 of 7**. Acting on that verdict would have meant lowering
`ENDPOINT_PREVIEW_AT_MS` to "get inside the budget" — trading preview
accuracy to fix a cliff that was not there.

### The second error: the base is not 625 ms

The replacement computed the cost against `base = MIN + (MAX−MIN)/2 = 625`.
That is the `base_ms=None` **fallback** in `required_silence_ms`, and the
engine never passes `None`. `_endpoint_open()` passes
`base_ms = _SilenceGate.silence_limit * 1000`, clamped into `[MIN, MAX]`:

| gate state | `silence_limit` | base |
| --- | --- | --- |
| fresh engine | `_DEFAULT` 0.8 s | **800 ms** |
| recent utterances avg < 1.5 s | `_FLOOR` 0.45 s | **450 ms** |
| recent utterances avg > 5.0 s | `_CEIL` 1.2 s | 900 ms (clamped) |

**625 ms never occurs in the running application.** This is D71's mistake
repeated — reading a default instead of the running system — and it is why
the figure needed correcting twice.

### What the real bases mean — the finding worth keeping

Replaying `decide_endpoint` tick by tick against the seven measured preview
latencies:

| base | endpoint fires | mean cost | ceiling on the saving | previews wasted |
| --- | --- | --- | --- | --- |
| 800 ms (first utterance) | 384–640 ms | +155 ms | 448 ms | 0 / 7 |
| **450 ms (short commands)** | **384–480 ms** | **+110 ms** | **128 ms** | **4 / 7** |
| 900 ms (after dictation) | 384–640 ms | +155 ms | 576 ms | 0 / 7 |

Against the 450 ms base — the steady state for repeated short commands,
which is how this assistant is actually used — the endpoint can never fire
later than 480 ms. So a *perfect* preview saves at most **128 ms**, and
**four of the seven measured previews contribute nothing at all**: the base
rule ends the turn before the partial lands.

**This is the reason not to optimise the preview.** I had identified the
decode path's temperature fallback `(0.0, 0.4, 0.8)` and its ~325-char
`initial_prompt` as candidate costs, neither of which the warm-up path pays.
But the fallback is lazy — it only retries when a threshold is violated, so
it is a tail cost, not a median one — and the prefill is small next to the
encoder. Neither is measurable from here: `faster_whisper` is not installed
in the audit mirror, there is no GPU, and the model weights are not
reachable. Shipping a speculative speed-up against a 128 ms ceiling in the
common case is exactly the D65 mistake, so it was not shipped.

### The corrected instrument

Races the two rules and reports which won:

```
[STT] preview took 218ms -> endpoint fires ~384ms (ideal 352ms, +32ms);
      saved 96ms against the 450ms base rule
[STT] preview took 520ms -> endpoint fires ~480ms (ideal 352ms, +128ms);
      the 450ms base rule fired first, so this preview changed nothing
[STT] preview took 40ms  -> endpoint fires ~352ms (ideal 352ms, +0ms);
      free -- it landed inside ENDPOINT_MIN_MS
```

The base is read from the live `_silence_gate`, the tick length from
`CHUNK_SIZE`/`SAMPLE_RATE`, and the fire time rounds up to a real tick.
Still `DEBUG_MODE`-gated, still logs no transcript text.

### The testing lesson, which nearly slipped through

The first version of the key test recomputed the instrument's formula
**inside the test**. A mutation run — breaking the formula in `engine.py` —
left it **passing**: it was checking my arithmetic against itself. Two of
three mutations passed.

It was replaced with a behavioural test that runs the real
`_spawn_preview_transcribe` with a `_transcribe` of known duration and a
`_silence_gate` of known state, captures the printed line, and checks those
numbers against the shipped `decide_endpoint` — using the line's own
reported `took` as the input, which makes it immune to `sleep` jitter.

The `520 ms × 450 ms-base` cell is what exposed the second wrong
instrument: the test failed with *"no longer yields rule=complete (got
'base')"*, which is how the gate-derived base was found at all.

### Tests

15 in `tests/test_preview_cost_instrument.py`, including the behavioural
match across two gate states × three preview latencies, the wasted-preview
case, the free case, that the same latency reports differently against two
bases (the D71-class guard), the 4-of-7 and 128 ms figures, and that the
625 ms fallback is not what `_endpoint_open` passes.

All five candidate mutations fail at least one test: ignoring the base race,
substituting the 625 ms fallback, dropping the tick rounding, forgetting
`ENDPOINT_PREVIEW_AT_MS`, and never marking a preview wasted.

### Validation

* `tests/test_preview_cost_instrument.py`: **15 passed**
* full suite **2541 passed, 19 skipped, 7 subtests passed, 0 failed**
* router bench unchanged: 98.8% / en 98.2% / hi 98.9% / hinglish 100% / 0/60
* `pyflakes` clean

---

## D73 — FIXED: "volume up" routed to chat, and the obvious fix broke negation

### The gap

All four `set_volume` patterns require the **verb first**:

```
(?:set|change|put) (?:the )?volume (?:to |at )?(\d{1,3})%?
volume (?:to |at )?(\d{1,3})%?
(?:increase|raise|turn up) (?:the )?volume(?: by (\d+)%?)?
(?:decrease|lower|turn down|reduce) (?:the )?volume(?: by (\d+)%?)?
```

So "turn up the volume" worked and bare **"volume up"** — object first, which
is how it is actually spoken — fell through to `chat`.
`bench/golden/adversarial.jsonl` row 41 records it in its own note:
*"known miss: bare 'volume up' means set_volume, not chat"*.

### The fix, and the version of it that was wrong

`_detect_intent_cached` matches with `pattern.search()`, so the obvious form

```python
r"(?:volume|sound) (?:up|down)(?: by (\d+)%?)?"
```

matches the substring "volume up" **anywhere**. Probing adjacent phrasings by
hand found it taking over three utterances that correctly went to chat:

| phrase | before | with the unanchored fix |
| --- | --- | --- |
| `don't turn the volume up` | chat | **set_volume** — a negated command |
| `volume up mat karo` | chat | **set_volume** — negated, Hinglish |
| `what is the volume up to` | chat | **set_volume** — a question |

The negation guard does not save the first two: `_ACTION_VERBS` has no
"turn up", so `is_negated_action_request()` returns False and the match
stands. **None of the three is in `bench/golden`**, so the row-level diff
across all 590 rows reported *zero regressions* while all three were broken.
The golden set is a regression net for the rows it contains and nothing else.

Shipped form, using the file's existing `_FILLER` idiom (`^` plus the
wake/politeness prefixes) and a trailing `$`, the same shape D19 used on
`unmute` for the same class of over-match:

```python
_FILLER + r"volume (?:up|down)(?: by (\d+)%?)?$"
```

`sound up` is deliberately **not** included: the `set_volume` gate is
`("volume",)`, a plain substring test, so any `sound` alternative is
unreachable dead code. Widening the gate changes matching for all five
patterns and was not measured, so it was not done — recorded in a test so
anyone adding it has to touch the gate and re-measure.

### Validation

* golden row diff: **1 row changed, 0 regressions** (`'volume up'`
  chat → set_volume)
* router bench **98.8% → 99.0%**, en **98.2% → 98.5%**, negatives still 0/60
* 20/20 hand probes, including all three over-match cases and
  `turn up the brightness` (the nearest neighbouring intent)
* 29 tests in `tests/test_volume_object_first_order.py`; all four mutations
  caught (unanchor, drop the `$`, remove the alternative, widen the gate)

---

## D74 — FIXED: 66% of negated commands executed anyway

Found while probing D73, and much larger than D73.

### The defect

`_NEGATED_ACTION_PATTERNS` gates every alternative on `_ACTION_VERBS`:

```
open|launch|start|run|restart|reboot|shutdown|close|quit|kill|delete|remove|
clear|format|mute|unmute|lock|unlock|enable|disable|turn on|turn off|play|
pause|stop|install|uninstall|send|submit|purchase|buy|exit|leave|...
```

About 30 verbs. The pattern set has **~130 intents**, and the verbs they
actually use were never added to it: set, increase, decrease, dim, max, min,
maximize, minimize, snap, move, resize, scroll, zoom, switch, press, paste,
copy, type, undo, redo, add, mark, remind, show, read, list, find, search,
calculate, summarize, empty, hibernate. Each new intent shipped its pattern
and not its verb, so the guard fell steadily behind the router it guards.

### Measured scope

840 negated phrasings, built by prefixing every positive command in
`intents_en.jsonl` and `intents_en_noisy.jsonl` with `don't `, `do not ` and
`never `. **558 of them slipped the guard and matched an executable intent:**

| tier | intents | phrases | examples |
| --- | --- | --- | --- |
| **3** | 3 | 18 | `do not empty recycle bin`, `do not hibernate my pc`, `do not put the computer to sleep` |
| **2** | 12 | 60 | `do not lock my laptop`, `do not press the enter key`, `do not paste it`, `do not cancel all my reminders`, `do not type this: …` |
| 1 | 65 | 333 | `do not set brightness to 70`, `do not minimize all windows`, … |
| 0 | 25 | 147 | `do not what's the time`, … |

A negated destructive command executing is the worst failure this router can
have, and it was 66% of them.

### The fix: ask the matcher, not a list

The verb list was only ever a proxy for *"the rest of this is an action
request"*. The matcher answers that exactly. So when an utterance **opens**
with a negation marker, strip the marker and match the remainder:

```
"do not empty recycle bin"  ->  "empty recycle bin"  ->  empty_recycle_bin
                            ->  therefore negated    ->  chat
```

This cannot fall behind the pattern set the way a list did — it covers
intents a skill registers at runtime too — and it cannot over-trigger the way
a bare "starts with don't" rule would: it fires only when the remainder
genuinely resolves to a non-chat intent. `"don't worry about it"` leaves
`"worry about it"`, which is chat, so nothing happens. If a *do not disturb*
intent is ever added, `"do not disturb"` leaves `"disturb"`, also chat, so it
keeps working.

**`stop`, `pause` and `cancel` are deliberately not negation markers** —
they are real commands here (`stop_media`, `pause_media`,
`cancel_shutdown`), and a test pins that.

### Two corrections found by measuring, not reasoning

**The guard has to try every route the router tries.** Checking only
`_detect_intent_cached` left **15 of the 840** still routing — every one from
`intents_en_noisy` with an STT-style misspelling (`"don't set brightness two
70"`, `"don't paste it hear"`, `"don't snap the window to the write"`),
because `detect_intent()` recovers those through `_correct_typos()` and the
guard did not. Mirroring its escalation — and asking
`_resolve_known_app_open()` as well — closed them.

**The Hinglish half is deliberately out of scope.** A first attempt at the
closing form (`… mat karo`, `… नहीं करना`) regressed a golden row
immediately: `\s*(?:मत|नहीं|नही|ना)…$` matched the **"ना" inside "भरना"** in
`'टू-डू में जोड़ो बिजली का बिल भरना'`, turning a valid `add_todo` into chat.
Devanagari has no usable `\b`. Worse, bare trailing `na`/`ना` is usually
**emphasis** in Hinglish — *"volume up kar do na"* means **please** turn it up
— so treating it as negation would refuse commands the user is asking for
more firmly, which is a worse failure than the one being fixed. The measured
leak is entirely English-prefix, so the Hinglish side is recorded as open
rather than guessed at.

### Validation

* negated-command leaks: **558 → 0** of 840
* golden row diff vs the pre-D73 baseline: **1 row changed, 0 regressions**
* router bench **99.0%** / en 98.5% / hi 98.9% / hinglish 100% / **0-of-60**
* redteam bench: recall 100%, ASR 0%, FPR 0%, invariant ok
* 27/27 over-trigger probes (`don't worry about it`, `never mind`,
  `do not disturb`, `type i don't like it`, `play never gonna give you up`, …)
* full suite **2598 passed, 19 skipped, 0 failed**
* 28 tests in `tests/test_negated_action_remainder.py`; 9 of 10 mutations
  caught, the tenth being provably equivalent (removing the redundant `^`
  from a pattern applied with `.match()`)

### Three of my own tests had to be rewritten to be able to fail

Worth recording, because the pattern repeated:

1. A source-line scan for `detect_intent(` tripped on the function's **own
   docstring**, which names it while explaining why it must not be called.
   The fourth time this audit hit comment-breaks-the-scan. Replaced with an
   `ast` walk over `Call` nodes, which cannot see comments at all.
2. `test_the_guard_also_covers_the_known_app_open_route` was **vacuous** —
   deleting the branch it tested left it passing, because `"don't open
   spotify"` is caught by the old allowlist (`open` is in `_ACTION_VERBS`)
   before the new check runs. A sweep of all 590 golden texts × 5 negation
   forms found **zero** phrases that can exercise that branch. It is kept
   anyway — the guard should be complete with respect to the router's
   *routes*, not to today's dataset — and the test was rewritten to assert
   exactly that: the set of route resolvers the guard calls must cover the
   set `detect_intent` calls. That is the D74 defect generalised into a
   guard against its own recurrence.
3. `test_a_matcher_failure_cannot_break_routing` counted `except Exception`
   clauses, so a mutation turning them into bare `raise` passed. Replaced
   with an injected failing matcher. That then exposed that the two handlers
   need separate tests: the first one's `continue` skips the second entirely.

---

## D75 — FIXED: "turn down the volume by 20" made it louder

Found while checking D73's fix end to end — the router was right and the
handler then did close to the opposite of what was asked.

### The defect

`set_volume` folds raising and lowering into **one** intent, and its
patterns capture the amount in both readings:

```
"set volume to 40"           ->  40 is a TARGET
"turn up the volume by 10"   ->  10 is a DELTA
```

`_h_set_volume` did `int(match.group(1))` and called `set_volume(level)` on
every captured number:

| said | did | should have done |
| --- | --- | --- |
| `turn up the volume by 10` | `set_volume(10)` — **quieter** | `adjust_volume(+10)` |
| `turn down the volume by 20` | `set_volume(20)` — from 10, **louder** | `adjust_volume(-20)` |
| `increase the volume by 15` | `set_volume(15)` | `adjust_volume(+15)` |

Asking for a small change in one direction produced a jump in the other.
Pre-existing in the `(?: by (\d+)%?)?` groups of the increase/decrease
patterns; D73's object-first alternative inherited it for
`volume up by 10`.

**The handler had no tests at all**, which is how a wrong-direction action
survived in a shipped command path.

### Why brightness does not have it

`set_brightness` captures a number; `increase_brightness` is a **separate**
intent, anchored, with no numeric group. So brightness never had a
delta/target ambiguity to get wrong. That asymmetry is probably why nobody
noticed — and `brightness up$` already existed, which is further evidence
D73's missing `volume up` was an inconsistency rather than a design choice.

### The fix

A captured number is a delta only when the utterance says **both** a
direction and `by N`:

```python
direction = _volume_direction(user_input)
if direction and _VOLUME_BY_AMOUNT_RE.search(user_input):
    return _quick(ctx, system_tools.adjust_volume(direction * level))
volume_state["last"] = level
return _quick(ctx, system_tools.set_volume(level))
```

Three details that each came out of testing rather than reasoning:

* **The `by` requirement is load-bearing, and I first thought it was not.**
  `"turn up the volume to 40"` reaches the handler with a direction word
  *and* a captured target, because the unanchored
  `volume (?:to |at )?(\d{1,3})` pattern matches the substring
  `"volume to 40"` inside it. Keying only on the direction word would raise
  the volume **by** 40 instead of setting it **to** 40. A mutation that
  dropped the check passed until those five phrasings were added as tests.
* **`volume_state["last"]` is not written for a delta.** It means "the
  level we set"; storing a delta there would make the next relative change
  compound off a wrong base.
* **The direction words now live in one place and match on word
  boundaries.** They were two inline tuples inside the `except` branch, and
  the fix needs the same lists on the numbered path. Two copies of a list
  that must agree is exactly how D74 happened. The inline version also used
  `"up" in lowered_input`, which fires on "setup", "backup" and "upload" —
  harmless while only a no-number utterance reached it, not harmless once
  it decides whether a number is a delta.

### Validation

* 27 tests in `tests/test_volume_relative_vs_absolute.py`, driving the real
  handler against a recording `system_tools`
* all 6 mutations caught: absolute-only, drop the `by` check, lose the
  sign, record the delta as the last level, substring direction matching,
  bare direction no longer steps
* golden row diff vs the pre-D73 baseline: **1 changed, 0 regressions**
* full suite **2625 passed, 19 skipped, 0 failed**
* router bench 99.0% / en 98.5% / hi 98.9% / hinglish 100% / 0-of-60
* `pyflakes` clean

---

## D76 — FIXED: "close window" closed the wrong thing, and so did "close windows explorer"

One pattern, two defects, pointing in opposite directions:

```python
r"close (?:this|the|current) window"
```

### Missed phrasings

One determiner was **required**, out of exactly three. Everything else fell
through to `close_app` — route position 139, after this intent at 55 —
carrying a nonsense app name:

| said | routed to | captured app name |
| --- | --- | --- |
| `close window` | close_app | **`window`** |
| `close active window` | close_app | **`active window`** |
| `close the active window` | close_app | **`active window`** |
| `close my window` | close_app | **`my window`** |

`close_app` then looks for a running process by that name. On Windows that
is not a harmless no-op — "window" is a substring of real process names, so
the wrong thing can be closed.
`bench/golden/intents_en_noisy.jsonl` row 55 is `close window`.

### Over-match, which was already live

With no boundary after `window`, `pattern.search()` matched
`"close the window"` **inside** a longer name:

```
"close the windows explorer"   ->  close_active_window
```

So asking to close Windows Explorer closed whatever happened to be focused.
`close windows terminal` and `close windows security` escaped only because
they have no determiner — the bug was one word away from being much more
visible, and nothing in the 590-row bench covered it.

### The fix

```python
r"close (?:(?:this|the|my|current|active)\s+)*window$"
```

Determiners optional and repeatable (`the active window`), and the `$` is
what stops `windows <anything>` matching. The same anchoring lesson as D73,
reached the same way: by probing adjacent phrasings rather than trusting the
bench.

Trailing politeness (`close the window please`) is deliberately not covered
— it was not covered before either, and `_FILLER` absorbs only leading
prefixes.

`_NON_APP_TOKENS` was **not** touched. Adding English "window" to a list of
Hinglish function words would be off-theme, and it was unnecessary once
route order was checked: `close_active_window` is tried first, so widening
its pattern is sufficient on its own.

### Validation

* 19 tests in `tests/test_close_active_window_phrasings.py`, including the
  route-order dependency the fix rests on, and that D74's guard makes the
  newly-reachable intent newly refusable (`don't close window` → chat)
* all 3 mutations caught: revert the pattern, drop the end anchor, make the
  determiner required again
* 17/17 hand probes across the phrasing space
* golden row diff: **1 changed, 0 regressions**
* router bench **99.0% → 99.2%**, en **98.5% → 98.8%**
* redteam: recall 100%, ASR 0%, FPR 0%
* full suite **2644 passed, 19 skipped, 0 failed**

### One test had to be re-scoped

`test_the_pattern_is_boundary_anchored` asserted `$` over **every**
`close_active_window` pattern and failed on the Hinglish one registered from
`patterns_multilingual.py`. That one needs no `$`: it requires `window\s+`
followed by a closing verb (`ye window band kar do`), and `windows explorer`
has an `s` where that whitespace must be. Scoped to the English pattern,
with a second behavioural test pinning the reason the Hinglish one is safe —
a guard that fires on correct code teaches people to edit the guard.

---

## D77 — FIXED: every spoken duration failed, and the bench scored it as a pass

The most interesting finding of the batch, because of **how** it was
invisible.

### The defect

`_parse_duration_to_seconds` was digit-only:

```python
(r"(\d+)\s*(?:hour|hr)s?",     3600),
(r"(\d+)\s*(?:minute|min)s?",    60),
(r"(\d+)\s*(?:second|sec)s?",     1),
```

So every duration said **out loud** failed outright:

```
"set a timer for ten minutes"   -> "Sorry, I couldn't understand the
                                    duration 'ten minutes'."
"set a timer for half an hour"  -> same
```

Which is to say: the only way a voice assistant ever receives a duration
did not work.

### Why no bench row showed it

`bench/golden` checks the **intent**, not the slot. The router returned
`set_timer` correctly in every one of these cases, so every one counted as
a pass.

The clearest example is `adversarial.jsonl` row 37, annotated *"long
rambling sentence with one embedded command"*:

> so i was talking to my brother yesterday about the trip and honestly it
> was a lot of fun but anyway can you please **set a timer for fifteen
> minutes** because the tea is on the stove

Recorded as **passing**, and the timer it embeds did nothing. The number
word is what defeated the parser, not the trailing words — a slot with a
digit tolerates trailing text fine (`"90 seconds and then remind me"` → 90).

**This is a structural blind spot, not a missing row.** An intent-only
bench cannot see a whole class of *right intent, dead command* defects. It
was found by sweeping for the D73/D76 pattern — patterns whose match span
leaves part of the utterance uncovered — and then asking what the handler
does with the slot it was handed.

### The fix

A converter that rewrites spoken numbers as digits, applied **before** the
existing regexes, so the existing logic is untouched:

| said | now |
| --- | --- |
| `ten minutes` | 600 s |
| `forty five minutes` / `forty-five minutes` | 2700 s |
| `twenty two minutes` | 1320 s |
| `an hour` | 3600 s |
| `half an hour` / `half hour` | 1800 s |
| `one hour thirty minutes` | 5400 s |
| `ninety seconds` | 90 s |

Three scope decisions:

* **`"a"`/`"an"` count as 1 only in front of a unit.** Otherwise
  `"a lot of fun"` — a real slot, from the rambling row above — becomes
  `"1 lot of fun"`.
* **`"half"` is rewritten first**, because it is not a count.
* **The converter is NOT wired into intent routing.** It would rewrite
  `"open one note"` to `"open 1 note"`, and the measured gain in the router
  is a single bench row (`set volume to fifty`) against a real risk to app
  names. A test pins that separation, and a mutation that wires it in fails.

### On duplicating the number table

`sara/core/memory2/timeparse.py` keeps its own smaller `_NUMWORD` (1–12,
plus Hindi `ek`/`एक`) for relative date windows. Merging them would couple
`memory2` to the orchestrator for no benefit, so they stay separate — but
two tables that must not contradict each other is exactly the shape of the
D74 and D75 defects, so a test pins that they agree wherever they overlap.

### Validation

* 48 tests in `tests/test_spoken_duration_words.py`, asserting on parsed
  **seconds** rather than on the intent — which is the whole point
* all 5 mutations caught, including wiring the converter into routing
* golden row diff: **0 rows changed** (D77 touches no router pattern)
* router bench unchanged at 99.2% / en 98.8% / hi 98.9% / hinglish 100%
* full suite **2692 passed, 19 skipped, 0 failed**
* `pyflakes` clean

### Checked alongside, and sound

* `_h_set_alarm` calls `dateparser.parse()` with no local `try`/`except`.
  Not a defect: `dispatcher` wraps **every** handler invocation, with a
  comment explaining that one bad handler must not reach
  `run_sara_logic()`'s outer handler and kill the voice loop. A raising
  alarm parse becomes one failed command.
* `dateparser` itself cannot be exercised in the audit mirror —
  `tzlocal` rejects the container's `TZ=Asia/Calcutta`. That is an
  environment artifact here, not evidence about Windows, and is **not**
  claimed either way.
* The uncovered-span sweep flagged 81 golden rows. All but this one are
  benign: Hinglish sentences where the pattern matches the core and the
  Hindi verb wrapper (`kar do`, `batao`, `laga do`) sits outside the span
  by design.

---

## D78 — FIXED: "take screenshot" went to chat

### The gap

The only screenshot-taking pattern was

```python
r"take a screenshot"
```

with the article **required** and the compound spelling not accepted, so
three of the four ways this is said went to `chat`:

| said | before |
| --- | --- |
| `take a screenshot` | ✓ |
| `take screenshot` | **chat** |
| `take screen shot` | **chat** |
| `capture screen` | **chat** |

Dropping an unstressed "a" is one of the commonest things an STT engine
does, and Whisper writes "screen shot" as two words at least as often as
one. `bench/golden/adversarial.jsonl` row 32 records exactly this,
annotated *"dropped article in speech-to-text"*.

The three `describe my screen` style patterns were unaffected, which is why
the intent looked covered.

### The fix

```python
r"(?:take|grab|capture) (?:a )?screen\s?shot",
r"(?:take|grab|capture) (?:a |the |my )?screen\b(?!\s*(?:shot|settings|resolution))",
_FILLER + r"screen\s?shot$",
```

The bare noun is bound at **both** ends. Unanchored, `search()` would fire
on any sentence containing the word — `"where do screenshots go"`,
`"that screenshot was blurry"`, `"i sent you a screenshot yesterday"` — so
it uses `_FILLER` and `$`, the same shape D73 and D76 needed. The
`capture ... screen` alternative carries a negative lookahead so it does not
eat `screen settings` or `screen resolution`.

### Validation

* 27 tests in `tests/test_screenshot_phrasings.py`
* all 5 mutations caught: require the article, unanchor the bare noun, drop
  its end anchor, drop the lookahead, remove the bare form
* 18/18 hand probes
* golden row diff: **1 changed, 0 regressions**
* router bench **99.2% → 99.3%**, en **98.8% → 99.1%**
* redteam: recall 100%, ASR 0%, FPR 0%
* full suite **2719 passed, 19 skipped, 0 failed**

### A test mis-scoped the same way D76's did

The structural test selected the bare-noun pattern as *"every pattern
mentioning screen without an English verb"* and picked up the Hinglish
pattern from `patterns_multilingual.py` (`screen pe kya chal raha hai`),
failing on correct code. Re-scoped to select by what the fix actually uses —
patterns built from `_FILLER`, i.e. starting `^[\s,]*`.

**The general lesson, now twice:** selecting patterns by what they *lack*
finds the wrong ones. Select by the shape the fix introduced.

---

## D79 — NOT FIXED, deliberately: colon-free dictation would type the wrong thing

Recorded because the decision is the finding.

`typing_text` requires a colon:

```python
r"\btype\b(?: this)?(?: for me)?:\s*(.+)"
```

so `"type hello world"` goes to chat. `intents_en_noisy.jsonl` row 69 is
`"type see you tomorrow"`, one of the remaining bench misses.

### Why it stays

The candidate fix is an anchored colon-free form,
`_FILLER + r"type\b(?!\s+of\b)\s+(.+)$"`. Simulated against phrases that
open with "type", **without** changing the file:

| phrase | would be typed |
| --- | --- |
| `type hello world` | `hello world` ✓ wanted |
| `type see you tomorrow` | `see you tomorrow` ✓ wanted |
| `type of car do you like` | — correctly skipped |
| **`type faster`** | **`faster`** |
| **`type a letter for me`** | **`a letter for me`** |
| **`type the following`** | **`the following`** |
| **`type up my notes`** | **`up my notes`** |

**5 of 12 probes would inject the wrong text.** And `typing_text` does not
merely answer — it types into whatever currently has focus, so the wrong
text can land in a document, a chat window or a terminal. `"type the
following"` is the worst of them: the user is about to dictate, and SARA
would type the preamble.

Against that: one bench row.

A leading-function-word exclusion list (`a|an|the|of|up|out|faster|…`) would
catch four of the five, but that is a hand-kept word list standing in for a
judgement — the exact shape of the D74 defect, which took 558 negated
commands to surface. The colon is doing real disambiguation work, and it is
cheap for the user to say.

**Status: WONTFIX** until there is a safer discriminator than a word list.
The bench row stays a known miss, as its own file already describes it.

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
