"""
sara.core.grounding
Time-sensitive questions ("latest iPhone price", "who is the current RBI governor", "India ka score aaj") are answered
from a fresh web search instead of the local model's memory, which is months old and sometimes invented (audit part 2, O4).

`needs_grounding(text)` is a conservative pure check (a question, with a recency cue, that no other intent handles).
`ground(text, search_fn)` runs ONE search with a hard time limit and returns a context block for the chat model, or None
when the search failed / found nothing (the model then answers as before). The block is treated as untrusted data by the
engine (`SaraLLM._secure_reference`), so instructions inside web pages are never obeyed.
"""
from __future__ import annotations

import concurrent.futures
import re
from typing import Callable, Optional

_RECENCY = re.compile(
    r"\b(?:latest|newest|current(?:ly)?|right\s+now|as\s+of\s+(?:today|now)|today'?s|this\s+(?:week|month|year)|"
    r"price\s+of|rate\s+of|how\s+much\s+(?:is|does|do)\b.*\b(?:cost|price)|stock\s+price|share\s+price|"
    r"who\s+(?:is|are)\s+the\s+(?:current|new|present)|who\s+won|who\s+is\s+winning|live\s+score|score\s+of|"
    r"abhi\s+ka|abhi\s+ki|aaj\s+ka|aaj\s+ki|aaj\s+ke|taaza|latest\s+version|release\s+date|kitne\s+ka\s+hai|kya\s+rate\s+hai|"
    r"petrol\s+(?:ka|ki)|gold\s+(?:ka|ki)|sona\s+(?:ka|ki)|dollar\s+(?:ka|ki)|rupee\s+(?:ka|ki))\b",
    re.IGNORECASE,
)
_QUESTIONISH = re.compile(
    r"^\W*(?:what|whats|what's|who|when|where|which|how|is|are|does|do|did|tell|kya|kaun|kab|kahan|kitna|kitni|kitne|batao|bata)\b|\?\s*$|(?:kya\s+hai|kitna\s+hai|kaun\s+hai|kya\s+hai\s+aaj|kya\s+chal\s+raha\s+hai)\W*$",
    re.IGNORECASE,
)
_COMMANDISH = re.compile(r"^\W*(?:open|close|play|set|turn|start|stop|remind|send|call|write|type|search|google|kholo|chalao|laga)\b", re.IGNORECASE)
_FAIL = ("sorry", "no results", "web search requires", "no search query")


def needs_grounding(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8 or len(t) > 200 or _COMMANDISH.match(t):
        return False
    return bool(_RECENCY.search(t)) and bool(_QUESTIONISH.search(t))


def ground(text: str, search_fn: Callable[[str], str], timeout_s: float = 4.0) -> Optional[str]:
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ground")
    try:
        fut = pool.submit(search_fn, text)
        try:
            result = (fut.result(timeout=timeout_s) or "").strip()
        except Exception:  # noqa: BLE001 -- timeout or a failing search: answer without it
            return None
    finally:
        pool.shutdown(wait=False)
    if not result or result.lower().startswith(_FAIL):
        return None
    return (
        "Fresh web search results for the user's question (newer than your own knowledge): "
        "answer from them, say briefly where it comes from (for example 'according to <title>'), and say so if they do not "
        "contain the answer.\n" + result[:1200]
    )
