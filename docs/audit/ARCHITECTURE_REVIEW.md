# SARA AI — Architecture Review

Scope: module boundaries, dependency coupling, concurrency, resource lifecycle
and shutdown. Evidence-driven; nothing here is refactored on style grounds.

Date: 2026-10-27 · repo checkpoint `144787c` · mirror `6eb36c2`

---

## 1. Shape of the system

`sara/` is seven subsystems over 50,198 production lines:

| Package | Role | Modules |
| --- | --- | --- |
| `audio/` | STT, TTS, acoustic echo cancellation | 22 |
| `core/` | intent, llm, memory, memory2, planning, router, security, telemetry, rag | 75 |
| `gui/` | 16 `Api*Mixin` classes + 41 JS + 36 CSS | 23 py |
| `orchestrator/` | dispatcher, handlers, proactive, wiring | 34 |
| `skills/` | drop-in plugin skills | 7 |
| `tools/` | Windows automation, reminders, web, vision | 28 |

Boundaries are clean and the naming is honest — a module's name predicts its
contents. The layering (`gui → orchestrator → core/tools → audio`) is real and
mostly respected.

## 2. Coupling

| Module | Internal imports | Note |
| --- | --- | --- |
| `config` | imported by **82 of 195** | the single widest dependency, and at 1,652 lines also the largest root file |
| `orchestrator/dispatcher.py` | imports 32 | integration chokepoint |
| `orchestrator/core_wiring.py` | imports 27 | the composition root |

**Verdict: leave alone.** `config` being universal is normal for a settings
module, and it is a leaf (it imports nothing internal), so the fan-in costs
nothing architecturally. `dispatcher` and `core_wiring` are *supposed* to know
about everything — that is what a dispatcher and a composition root do.
Splitting either would move the coupling, not remove it, and the audit brief is
explicit that working architecture is not rewritten without evidence of
benefit. No defect was found in either.

### Circular imports

One real cycle: `dispatcher ↔ route_chat`. It is deliberately broken — a
call-time import at `route_chat.py:258` with a comment explaining exactly why.
That is the correct treatment.

`router.policy ↔ router.features` was a **false positive** in the Phase 1
scan: `policy.py:8` imports `.features` under `TYPE_CHECKING`, which never
executes.

## 3. Concurrency inventory

Long-lived threads and pools created in production code:

| Owner | Kind | Daemon | Has stop | Torn down |
| --- | --- | --- | --- | --- |
| `orchestrator/db_writer.py` `AsyncDBWriter` | thread | yes | `shutdown()` | yes |
| `orchestrator/tts_worker.py` `TTSWorker` | 2 threads | yes | `shutdown()` | yes (as `tts`) |
| `orchestrator/proactive.py` `ProactiveEngine` | thread | yes | `stop`/`shutdown` | yes |
| `orchestrator/supervisor.py` | thread | yes | `stop()` | yes |
| `orchestrator/notifications.py` watcher | thread | yes | `stop`/`shutdown` | yes (bootstrap) |
| `gui/app/helpers.py` `_PrefWriter` | thread | yes | `stop(timeout)` | yes (bootstrap) |
| `gui/app/core.py` command worker | thread | yes | queue-drained | process exit |
| `orchestrator/network_utils.py` | pool(4) | — | `_shutdown_network_executor` | yes |
| `core/planning` planner + correction | pool(2)+pool(2) | — | `shutdown_planning_executors` | yes (bootstrap) |
| `core/tool_router.py` | pool | — | `_shutdown_tool_call_executor` | yes (bootstrap) |
| `orchestrator/core_wiring.py` init | pool(3) | — | context manager | yes |
| `skills/_framework.py` per-skill | thread | yes | timeout-bounded | n/a |

Everything long-lived is a **daemon** thread or a bounded pool, so nothing can
wedge process exit. Teardown is split deliberately across two layers:

- **`run_sara_logic()`'s `finally`** — components the logic thread owns.
- **`bootstrap.main()` after `webview.start()` returns** — process-level
  resources: the hotkey, the notification watcher, the three executors, the
  preference writer, and `db.close()`.

That split is sound. Both halves are reached on a normal exit.

## 4. Defect found — D16, silent conversation-log loss

**Severity:** Medium (data loss, on an error path, silent).

`run_sara_logic()`'s `finally:` block tore down six components in sequence and
wrapped exactly one of them (`reminders.shutdown()`) in try/except. The other
five ran unguarded, so **one raising step skipped every step after it**.

The ordering is what turned that into data loss. `db_writer.shutdown()` sat
second-to-last, and it is the step that flushes queued conversation-log writes:
`AsyncDBWriter` holds a `queue.SimpleQueue` drained by a **daemon** thread, so
`_flush_pending()` at shutdown is the only thing that gets the tail of that
queue into the database. Whatever is still queued at process exit is gone.

The realistic trigger is the step immediately before it. `tts.shutdown()`
reaches `TTSWorker._release_duck()`, the chunker/synth pools and the
sounddevice output stream — and `TextToSpeech.__del__` wraps its own call to
`shutdown()` in `try/except` *precisely because it can raise*. So an audio
teardown hiccup on exit silently dropped the user's last messages.

The codebase already knows the rule. `bootstrap.py` guards every one of its
teardown steps individually, and says so: *"each wrapped so one failing cleanup
step can never block the rest of shutdown."* The logic thread's teardown simply
never got the same treatment.

**Fix.** Extracted to `shutdown_logic_components()`: every step independently
guarded, failures logged and **returned** rather than swallowed, producers
(`thread_supervisor`, `proactive_engine`, `tts`) stopped before the draining
steps (`notes_memory`, `db_writer`, network executor) so the flush cannot race
new writes, and every component optional to match `bootstrap.py`'s None-checks
for a boot that failed partway.

The extraction is the minimum needed to make the guarantee testable — an inline
`finally:` cannot be exercised without standing up the whole logic thread.

**Verification.** `tests/test_shutdown_lifecycle.py`, 15 tests. The central one
is parametrised over all five earlier steps and asserts the conversation-log
flush still runs when each one raises. Verified **red** against the pre-fix
semantics (10 failed / 5 passed), green after (15 passed).

**Not verified on Windows.** The raising paths this protects — pycaw ducking,
sounddevice stream close, COM — cannot be exercised here. The guarantee is
established structurally and by fault injection, not by observing a real
PortAudio failure.

## 5. Checked and found sound

| Area | Verdict |
| --- | --- |
| Daemon discipline | Every long-lived thread is a daemon; no non-daemon thread can wedge exit |
| Executor bounds | All pools are explicitly bounded (2–4 workers) with `thread_name_prefix` set — threads are identifiable in a dump |
| Idempotent teardown | `TextToSpeech.shutdown()` guards with `_shutdown_done`; `ThreadPoolExecutor.shutdown()` is idempotent by contract; `AsyncDBWriter.shutdown()` is safe to repeat |
| Flush-before-close ordering | `bootstrap.py` stops the executors *before* the pref-writer flush and `db.close()` — correct order, and commented as deliberate |
| Unbounded thread spawning | Previously fixed: `gui/app/core.py` replaced per-call threads with one bounded queue and a single long-lived worker |
| `db.close()` on exit | Previously fixed; the comment records the WAL-not-flushed bug it closed |
| Command queue | Bounded (`_MAX_QUEUED_COMMANDS`) with double-submit suppression |
| Lazy-init proxy | `orchestrator/lazy.py` builds on a background thread and re-raises the factory error on first access, so a failed dependency surfaces at use rather than vanishing |

## 6. Known lifecycle risk, not a defect

`bootstrap.main()` joins the logic thread with `logic_thread.join(timeout=5.0)`
and then proceeds regardless. If the logic thread has not finished its teardown
within five seconds, the process continues to exit and the remainder of
`shutdown_logic_components()` does not complete.

This is a deliberate trade — an app that refuses to close is worse than one
that drops a trailing log write — and the daemon design means nothing hangs.
Worth knowing, not worth changing without evidence that the five seconds is
actually being exceeded. Measuring that needs a Windows run.

## 7. Not yet reviewed

- End-to-end feature traceability (brief item B).
- Bridge **argument arity** (item C). Name-level contract is enforced in CI
  since batch 1; the number of arguments each JS call site passes is not yet
  checked against the Python signature.
- GUI review — accessibility, responsiveness, theme behaviour (item E) →
  `GUI_REVIEW.md`.
- Security: path handling, privilege boundaries, assistant-generated tool
  execution (item F). Phase 1 established there is no unsafe
  `shell`/`eval`/`exec`/`pickle`.
