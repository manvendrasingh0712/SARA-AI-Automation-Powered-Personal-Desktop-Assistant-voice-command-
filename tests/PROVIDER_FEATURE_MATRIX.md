# Provider / Feature Matrix

Built strictly from the files reviewed: `sara/core/llm/clients.py`,
`sara/core/tool_router.py`, `sara/core/planning/planner.py`,
`sara/orchestrator/proactive.py`, `config.py`. Any row/cell not
confirmable from those files is marked **needs verification** rather
than assumed.

| Feature | Ollama | Gemini | Internet Required |
|---|---|---|---|
| Chat | ✅ (`Config.LLM_BACKEND="ollama"`, `clients._get_ollama_client()`) | ✅ (`Config.LLM_BACKEND="gemini"`, default; `clients._get_gemini_client()`) | Only if Gemini is selected (`LLM_BACKEND=gemini`); Ollama runs against `OLLAMA_HOST` (local by default) |
| Planner | ✅ (`planner._call_planner_llm()` has an explicit `if backend == "ollama":` branch using `client.chat(..., tools=_get_plan_tool_schema())`) | ✅ (same function's Gemini branch, `client.models.generate_content(..., tools=_get_gemini_plan_tools())`) | Only if Gemini is selected |
| Tool Router | ✅ (`tool_router._resolve_tool_call_llm()` has an explicit `if backend == "ollama":` branch using `client.chat(..., tools=TOOLS_SCHEMA)`) | ✅ (same function's Gemini branch via `google.genai`'s native function-calling; module docstring notes the file "was originally written against Ollama and has since been migrated" but the Ollama branch is still present in code) | Only if Gemini is selected. Also has a fully-local fallback path (`TOOL_CALLING_MODE="heuristic"` or LLM-call timeout/failure) that needs no LLM call and no internet at all |
| Proactive rephrase | ✅ (`proactive._quick_llm_rephrase()` has an explicit `if backend == "ollama":` branch) | ✅ (same function's Gemini branch via `google.genai`) | Only if Gemini is selected. Best-effort: any failure (including no internet) falls back to the plain template with no error surfaced |
| RAG embedding | ✅ (`clients._get_embedding_vector()`, `Config.EMBEDDING_BACKEND="ollama"`, uses `OLLAMA_EMBEDDING_MODEL`, default `nomic-embed-text`) | ✅ (`Config.EMBEDDING_BACKEND="gemini"`, default, `EMBEDDING_MODEL="gemini-embedding-001"`) | Only if `EMBEDDING_BACKEND=gemini`. Note `EMBEDDING_BACKEND` is an independent switch from `LLM_BACKEND` (config.py comment: can run `LLM_BACKEND=ollama` for chat while still embedding via Gemini, or vice versa) |
| Weather/News/Web | ✅ | ✅ | ✅ (needs verification of the exact implementation, but not in the 5 files given for this task — `sara/tools/web.py` was reviewed in a prior task and calls external APIs — DDGS, wttr.in — directly, independent of `LLM_BACKEND`) |
| Vision | needs verification | needs verification | needs verification (`config.py` only defines `VISION_MODEL`, defaulting to `"gemini-2.5-flash"`, with no corresponding `OLLAMA_VISION_MODEL`-style key anywhere in the file; no vision-handling module — e.g. a `sara/core/vision.py` or similar — was included in this task's files, so whether an Ollama vision path exists at all can't be confirmed here) |

Do not call this system fully offline unless every enabled feature for that mode is actually local.
