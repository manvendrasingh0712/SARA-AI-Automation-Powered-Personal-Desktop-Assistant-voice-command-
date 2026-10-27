# SARA AI — Validation Report

What was actually verified, how, and what remains unverified. Written to be
read sceptically: a claim here should be checkable from the command given.

- **Date:** 2026-10-26
- **Baseline commit:** `da343da` (pristine mirror of `C:\project`)
- **Head commit:** `fadcedf`

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
| Node | v22.23.4 |

**`winsdk` publishes no Linux wheel**, which is exactly why the project's CI is
`windows-latest` only. Everything Windows-specific is therefore *unverified
here* and is marked as such below.

---

## 2. Test results

### 2.1 Python — `python -m pytest -q`

| Run | Passed | Failed | Skipped |
| --- | --- | --- | --- |
| Baseline (`da343da`) | 2009 | **19** | 5 |
| After batch 3 | 2017 | 0 | 24 |
| After batch 4 | 2027 | 0 | 24 |
| **After batch 5 (head)** | **2044** | **0** | 24 |

All 19 baseline failures came from a single cause: `_ensure_windows()` raising
`RuntimeError` in `sara/tools/system/_shared.py:38`. They were never logic
failures — they pass on Windows and are now explicitly skipped off it.

Wall time improved 40 s → 33 s (see §4.2).

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

| Suite | Result |
| --- | --- |
| `bridge_contract.test.js` *(new)* | 19 passed, 0 failed |
| `chat_state.test.js` | 11 passed, 0 failed |
| `ui_prefs_theme.test.js` | 46 passed, **1 failed** — pre-existing, see §5.1 |

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

### 5.1 `ui_prefs_theme.test.js` — theme ordering · **NEEDS A DECISION**

Pre-existing, present at baseline, unchanged by this work.

```
FAIL: theme list
  got  [..., "aurora", "lime", "neon", "ink", "oat", "custom"]
  want [..., "aurora", "neon", "ink", "oat", "lime", "custom"]
```

`theme.js` places `lime` (Graphite Lime) between `aurora` and `neon`; the test
expects it between `oat` and `custom`. This is the **display order of the theme
picker** — a product decision, not a correctness question, so it has not been
guessed at. Both files were last modified within ~20 seconds of each other, so
one of the two edits was simply not carried across.

Either answer is a one-line change:
- keep `theme.js` as shipped → update the expected list in the test, or
- the test is right → move `lime` in `theme.js`'s `LIST`.

**This blocks running the node suites in CI**, which is itself worth doing:
`ci.yml` does not execute them at all, which is why this failure went
unnoticed.

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

```bash
# Python suite (expect: 2044 passed, 24 skipped, 0 failed)
python -m pytest -q

# CI lint gate (expect: no output after the filter)
python -m pyflakes . | grep -v "imported but unused"

# Front-end suites
node sara/gui/js/tests/bridge_contract.test.js    # 19 passed
node sara/gui/js/tests/chat_state.test.js         # 11 passed
node sara/gui/js/tests/ui_prefs_theme.test.js     # 46 passed, 1 failed (§5.1)

# Order-independence
python -m pytest -q tests/test_planning.py tests/test_planning_gemini_backend.py
python -m pytest -q tests/test_planning_gemini_backend.py tests/test_planning.py
```

On Windows the 19 `@requires_windows` tests run instead of skipping, so expect
**2063 passed, 5 skipped**.
