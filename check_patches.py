"""Run from the project root:  python check_patches.py
Shows which upgrade patches are present in the project (OK / MISSING)."""
import os
import re
import sys

ROOT = os.getcwd()


def read(path):
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def has_file(path):
    return os.path.isfile(os.path.join(ROOT, path))


def has(path, text):
    data = read(path)
    return data is not None and text in data


def lacks(path, text):
    data = read(path)
    return data is not None and text not in data


def gitignore_ok():
    data = read(".gitignore") or ""
    return "!bench/golden/*.jsonl" in data


CHECKS = [
    ("T0", "dispatcher imports _remember_entity", lambda: bool(re.search(r"from \.context_tracking import[^\n]*_remember_entity|_remember_entity,", read("sara/orchestrator/dispatcher.py") or ""))),
    ("T0", "API test lists sync_notes_now", lambda: has("tests/test_api_surface.py", "sync_notes_now")),
    ("T0", "tts/engine.py imports Callable, Optional", lambda: bool(re.search(r"from typing import[^\n]*Callable", read("sara/audio/tts/engine.py") or ""))),
    ("T0", "settings.py duplicate mic function removed", lambda: lacks("sara/gui/app/settings.py", "def mic_sensitivity_to_threshold")),
    ("T0", ".env.example exists", lambda: has_file(".env.example")),
    ("T1", "telemetry package (trace.py)", lambda: has_file("sara/core/telemetry/trace.py")),
    ("T1", "config TELEMETRY_ENABLED", lambda: has("config.py", "TELEMETRY_ENABLED")),
    ("T1", "dispatcher marks", lambda: has("sara/orchestrator/dispatcher.py", "_t_mark")),
    ("T1", "core_wiring begin/end turn", lambda: has("sara/orchestrator/core_wiring.py", "_t_begin")),
    ("T1", "gui core.py begin/end turn", lambda: has("sara/gui/app/core.py", "_t_begin")),
    ("T1", "stt engine marks", lambda: has("sara/audio/stt/engine.py", "_t_pending")),
    ("T1", "llm engine marks", lambda: has("sara/core/llm/engine.py", '_t_mark("llm_req")')),
    ("T1", "tts_worker tts_done mark", lambda: has("sara/orchestrator/tts_worker.py", '_t_mark("tts_done")')),
    ("T1", "tts engine first-audio mark", lambda: has("sara/audio/tts/engine.py", 'tts_first_audio')),
    ("T1", "tests/test_trace.py", lambda: has_file("tests/test_trace.py")),
    ("T2", "perf_api.py", lambda: has_file("sara/gui/app/perf_api.py")),
    ("T2", "perf.js + perf.css", lambda: has_file("sara/gui/js/perf.js") and has_file("sara/gui/style/perf.css")),
    ("T2", "index.html loads perf.js", lambda: has("sara/gui/index.html", "perf.js")),
    ("T2", "bench/results/voice_baseline.md (correct folder)", lambda: has_file("bench/results/voice_baseline.md") and not os.path.isdir(os.path.join(ROOT, "sara", "bench"))),
    ("T3", "bench dataset + runner", lambda: has_file("bench/dataset.py") and has_file("bench/runner.py")),
    ("T3", "golden: intents_en.jsonl", lambda: has_file("bench/golden/intents_en.jsonl")),
    ("T3", "golden: hinglish + hindi", lambda: has_file("bench/golden/intents_hinglish.jsonl") and has_file("bench/golden/intents_hi.jsonl")),
    ("T3", "golden: negatives + adversarial", lambda: has_file("bench/golden/negatives.jsonl") and has_file("bench/golden/adversarial.jsonl")),
    ("T3", "tests/test_bench_dataset.py", lambda: has_file("tests/test_bench_dataset.py")),
    ("T3", ".gitignore keeps bench/golden/*.jsonl", gitignore_ok),
    ("T4", "bench metrics + report + thresholds", lambda: has_file("bench/metrics.py") and has_file("bench/report.py") and has_file("bench/thresholds.json")),
    ("T4", "bench/baseline.json", lambda: has_file("bench/baseline.json")),
    ("T4", "CI bench job", lambda: has(".github/workflows/ci.yml", "bench")),
    ("W2-base", "security tiers.py", lambda: has_file("sara/core/security/tiers.py")),
    ("W2-base", "config SECURITY_MODE", lambda: has("config.py", "SECURITY_MODE")),
    ("W2-base", "TURN_STATE.generation()", lambda: has("sara/orchestrator/state.py", "def generation")),
    ("W2-base", "red-team data", lambda: has_file("bench/redteam/attacks.jsonl") and has_file("bench/redteam/benign.jsonl")),
    ("W2-base", "test fixtures (pages)", lambda: has_file("tests/fixtures/injected_page.html") and has_file("tests/fixtures/normal_page.html")),
    ("T5", "taint / detector / untrusted / redact", lambda: all(has_file(f"sara/core/security/{n}.py") for n in ("taint", "detector", "untrusted", "redact"))),
    ("T5", "prompt rule block (UNTRUSTED)", lambda: has("sara/core/llm/prompt.py", "UNTRUSTED")),
    ("T5", "summarize_url handler wrapped", lambda: has("sara/orchestrator/handlers/media.py", "untrusted")),
    ("T6a", "policy.py + events.py", lambda: has_file("sara/core/security/policy.py") and has_file("sara/core/security/events.py")),
    ("T6b", "guard.py", lambda: has_file("sara/core/security/guard.py")),
    ("T6b", "dispatcher uses guard", lambda: has("sara/orchestrator/dispatcher.py", "security.guard")),
    ("T6b", "route_chat uses guard", lambda: has("sara/orchestrator/route_chat.py", "security.guard")),
    ("T7", "security_api.py", lambda: has_file("sara/gui/app/security_api.py")),
    ("T7", "security.js + security.css", lambda: has_file("sara/gui/js/security.js") and has_file("sara/gui/style/security.css")),
    ("T7", "bench/redteam_runner.py", lambda: has_file("bench/redteam_runner.py")),
]

if not has_file("config.py"):
    print("Run this from the project root (the folder that contains config.py).")
    sys.exit(1)

last = None
missing = 0
for group, name, fn in CHECKS:
    try:
        ok = bool(fn())
    except Exception:
        ok = False
    if group != last:
        print(f"\n[{group}]")
        last = group
    print(f"  {'OK     ' if ok else 'MISSING'}  {name}")
    missing += 0 if ok else 1
print(f"\n{len(CHECKS) - missing}/{len(CHECKS)} present, {missing} missing")
