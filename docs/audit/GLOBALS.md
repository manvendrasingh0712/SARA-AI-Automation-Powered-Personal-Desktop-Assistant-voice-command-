# `global` statements (audit Task 6.4)

Generated with an AST scan (`global` inside a function). "Lock in function" = the function body contains a `with <...lock...>:` block; it does NOT prove that the readers of the same name are locked. The risk column is a judgement from the name; nothing here was changed. Rows marked `review` are the ones to read first.

| File | Function | Name(s) | Lock in function | Risk (judgement) |
|---|---|---|---|---|
| config.py | app_data_dir | _APP_DATA_RESOLVED | no | review: written without a lock |
| logging_config.py | setup_logging | _configured | no | review: written without a lock |
| main.py | _acquire_single_instance_lock | _single_instance_mutex_handle | no | low: lazy init / flag |
| sara/audio/ducking.py | _state_file | _STATE_FILE | no | review: written without a lock |
| sara/core/intent/engine.py | _rebuild_routes | _COMPILED_PATTERNS, _ROUTES, _TRIGGER_VOCAB | no | review: written without a lock |
| sara/core/llm/clients.py | _get_ollama_client | _ollama_client | yes | low: written under a lock |
| sara/core/llm/clients.py | _get_gemini_client | _gemini_client | yes | low: written under a lock |
| sara/core/llm/clients.py | _get_embedding_vector | _embed_down_until | no | review: written without a lock |
| sara/core/llm/clients.py | _build_gemini_generate_config | _thinking_logged | no | low: lazy init / flag |
| sara/core/memory2/__init__.py | get_store | _STORE, _FAILED | yes | low: written under a lock |
| sara/core/memory2/__init__.py | reset_for_tests | _STORE, _FAILED | yes | low: written under a lock |
| sara/core/planning/executor.py | _get_gemini_correction_tools | _GEMINI_CORRECTION_TOOLS_CACHE | no | review: written without a lock |
| sara/core/planning/planner.py | _get_plan_tool_schema | _plan_tool_schema_cache | yes | low: written under a lock |
| sara/core/planning/planner.py | _get_gemini_plan_tools | _GEMINI_PLAN_TOOLS_CACHE | no | review: written without a lock |
| sara/core/router/features.py | warm | _detect_fn, _tool_fn, _resolved | no | review: written without a lock |
| sara/core/router/router.py | get_router | _ROUTER | yes | low: written under a lock |
| sara/core/security/events.py | configure | _STORE | yes | low: written under a lock |
| sara/core/security/policy_rules.py | app_data_layout | _layout | no | review: written without a lock |
| sara/core/telemetry/trace.py | set_enabled | _enabled | no | review: written without a lock |
| sara/core/telemetry/trace.py | begin_turn | _latest | yes | low: written under a lock |
| sara/core/tool_router.py | _get_gemini_tools | _GEMINI_TOOLS_CACHE | no | review: written without a lock |
| sara/core/tool_router.py | _note_gemini_failure | _gemini_blocked_until | yes | low: written under a lock |
| sara/core/tool_router.py | resolve_tool_call | _consecutive_timeouts, _circuit_open_until | yes | low: written under a lock |
| sara/gui/app/media.py | _get_com_executor | _com_executor | yes | low: written under a lock |
| sara/orchestrator/command_helpers.py | _ack | _LAST_ACK_INDEX | no | review: written without a lock |
| sara/orchestrator/emergency_stop.py | register_emergency_stop | _hotkey_handle | no | review: written without a lock |
| sara/orchestrator/emergency_stop.py | unregister_emergency_stop | _hotkey_handle | no | review: written without a lock |
| sara/orchestrator/notifications.py | init_watcher | _watcher_instance | yes | low: written under a lock |
| sara/orchestrator/notifications.py | get_watcher | _watcher_instance | yes | low: written under a lock |
| sara/skills/notes_qa.py | sync_notes_folder | _warned_unpatched, _needs_retry | yes | low: written under a lock |
| sara/skills/notes_qa.py | finished | _needs_retry | yes | low: written under a lock |
| sara/tools/system/system_info.py | start_stopwatch | _stopwatch_start, _stopwatch_laps | yes | low: written under a lock |
| sara/tools/system/system_info.py | stop_stopwatch | _stopwatch_start, _stopwatch_laps | yes | low: written under a lock |
| sara/tools/system/timers.py | set_timer | _active_timer | yes | low: written under a lock |
| sara/tools/system/timers.py | cancel_timer | _active_timer | yes | low: written under a lock |

## Verdict

Of the 35 `global` statements, none is a harmful race:

- `_embed_down_until` (`llm/clients.py`), `_LAST_ACK_INDEX` (`command_helpers.py`), `_enabled` (`telemetry/trace.py`): a single float/int/bool
  rebinding from several threads; each reader tolerates a stale value (a cooldown that ends a moment later, an ack phrase that repeats once).
- `_*_TOOLS_CACHE`, `_layout`, `_APP_DATA_RESOLVED`, `_configured`, `_STATE_FILE`: idempotent lazy initialisation; two threads racing build the same value.
- `_rebuild_routes` rewrites three tables; its only callers hold `_registry_lock` (`register_intent`).
- `_hotkey_handle` (`emergency_stop.py`): set at start-up and on shutdown from the same thread.

No code change was needed. (The audit counted 52; a plain AST scan of `global` inside functions finds 35 in this snapshot.)
