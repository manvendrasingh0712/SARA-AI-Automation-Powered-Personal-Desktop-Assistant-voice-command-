"""A/B: native Ollama tool calling vs a JSON-schema constrained answer, on phrases the regex layer misses.

    python bench/tool_router_structured.py          (Ollama must be running)

Reports per mode: valid-answer rate (no exception, known tool name), correct-tool rate, wrongly-called-a-tool rate on
plain chat, and median latency."""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402
from sara.core import tool_router as T  # noqa: E402
from sara.core.llm.clients import _get_ollama_client  # noqa: E402

# (phrase, expected tool name or "unknown")
CASES = [
    ("thoda shor kam kar do", "set_volume"), ("gaana agle wala laga", "unknown"), ("mujhe jaipur ka mausam batao zara", "weather"),
    ("kal subah ek meeting rakhni hai doctor ke saath", "unknown"), ("chrome ko band kar dena please", "close_app"),
    ("notepad chalu karo", "open_app"), ("aaj ki badi khabrein sunao", "news"), ("calculator me 45 ko 12 se guna karo", "calculator"),
    ("screen ki roshni badha do", "set_brightness"), ("wifi band kar dena", "unknown"), ("meri list me doodh add kar do", "add_todo"),
    ("python tutorial dhundo google par", "web_search"), ("example.com kholo browser me", "open_url"),
    ("what is photosynthesis", "unknown"), ("tell me a joke about cats", "unknown"), ("how are you today", "unknown"),
    ("explain how a refrigerator works", "unknown"), ("mujhe neend aa rahi hai", "unknown"), ("who is the prime minister of india", "unknown"),
    ("write a short poem about rain", "unknown"),
]


def run(structured: bool):
    client = _get_ollama_client(Config)
    model = Config.OLLAMA_MODEL
    names = {t["function"]["name"] for t in T.TOOLS_SCHEMA} | {"unknown"}
    valid = correct = chat_ok = chat_n = 0
    lat = []
    for phrase, expect in CASES:
        t0 = time.perf_counter()
        try:
            out = (T._ollama_structured_call if structured else T._ollama_tool_call)(client, model, phrase, Config)
            ok = out.get("name") in names and isinstance(out.get("arguments"), dict)
        except Exception:  # noqa: BLE001
            out, ok = {"name": "ERROR"}, False
        lat.append((time.perf_counter() - t0) * 1000)
        valid += ok
        if expect == "unknown":
            chat_n += 1
            chat_ok += out.get("name") == "unknown"
        correct += out.get("name") == expect
        if not out.get("name") == expect:
            print(f"   {'structured' if structured else 'native    '} {phrase!r:48} expected {expect:12} got {out.get('name')}")
    n = len(CASES)
    print(f"{'structured' if structured else 'native    '}: valid {valid}/{n}  correct {correct}/{n}  no-tool-when-none {chat_ok}/{chat_n}  median {statistics.median(lat):.0f} ms")


if __name__ == "__main__":
    run(False)
    run(True)
