"""Compare two system prompts for the chat model on a mixed question set: accuracy (regex on the answer), reply
length compliance (quick facts 1-2 sentences, explanations 3-6), and time.

    python bench/llm_prompt_eval.py --backend ollama            # local model (Ollama must be running)
    python bench/llm_prompt_eval.py --backend gemini            # GEMINI_FAST_MODEL
    python bench/llm_prompt_eval.py --backend ollama --prompts new     # one prompt only

"old" is the compact prompt shipped before the adaptive-length change (kept here verbatim as the reference);
"new" is `sara.core.llm.prompt._build_base_prompt` as it is now.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402
from sara.core.llm.prompt import _ACTION_RULES, _build_base_prompt, _security_rule  # noqa: E402

NOTE_DT = "Current local date and time: Saturday, 31 October 2026, 8:04 AM."

OLD_HINGLISH = (
    "You are Sara, a smart, accurate voice assistant on the user's PC. It is currently evening. "
    "Your text is spoken aloud: no markdown, no emojis, no lists; say numbers and symbols the way a person says them. "
    "Answer the question directly in one or two short sentences, with the answer first. Accuracy matters more than "
    "style: work out maths step by step silently and give the final result; if you are not sure of a fact, say so "
    "briefly instead of guessing. "
    "Language: reply in the language the user wrote. English question: plain English. Roman-script Hindi or Hinglish "
    "question: simple natural Hinglish in Roman script (Hindi words may be mixed with common English words). "
    "Devanagari question: Hindi in Devanagari. "
    "Do not repeat the question, do not say you are an AI, no filler or apologies. "
    "If the user is frustrated or in a hurry, be even shorter. "
    "Answer only the current message. Never bring up an earlier topic unless the user does or it is needed to answer."
    " The user's name is Manvendra; use it rarely, not every turn."
)

# (question, regex the answer must match or "", kind) -- kind: fact | maths | hi | explain
Q = [
    ("what is the capital of Australia", "canberra", "fact"),
    ("who wrote Romeo and Juliet", "shakespeare", "fact"),
    ("what is the chemical symbol for gold", "Au(?![a-z])", "fact"),
    ("how many continents are there", "(?<!\\d)7(?!\\d)|seven", "fact"),
    ("what is the largest planet in the solar system", "jupiter", "fact"),
    ("who painted the Mona Lisa", "vinci", "fact"),
    ("in which year did India gain independence", "1947|nineteen forty.?seven", "fact"),
    ("what does CPU stand for", "central processing unit", "fact"),
    ("what is the currency of Japan", "yen", "fact"),
    ("who discovered gravity", "newton", "fact"),
    ("what is 17 times 23", "391|three hundred (and )?ninety", "maths"),
    ("what is 15 percent of 240", "(?<!\\d)36(?!\\d)|thirty.?six", "maths"),
    ("if a shirt costs 800 rupees and is 25 percent off what is the sale price", "600|six hundred", "maths"),
    ("what is the next number in the sequence 2 4 8 16", "(?<!\\d)32(?!\\d)|thirty.?two", "maths"),
    ("how many minutes are there in a day", "1,?440|fourteen hundred", "maths"),
    ("india ki rajdhani kya hai", "delhi|दिल्ली", "hi"),
    ("taj mahal kisne banwaya tha", "shah ?jahan|शाहजहाँ|शाहजहां", "hi"),
    ("pani kitne degree par ubalta hai", "100|sau|सौ|hundred", "hi"),
    ("bharat ke pehle rashtrapati kaun the", "rajendra|राजेंद्र", "hi"),
    ("ek ghante me kitne minute hote hain", "60|sixty|saath|साठ", "hi"),
    ("sabse bada grah kaunsa hai", "jupiter|guru|brihaspati|बृहस्पति|गुरु", "hi"),
    ("mahatma gandhi ka janm kab hua tha", "1869|october|अक्टूबर", "hi"),
    ("why is the sky blue", "scatter|rayleigh|short|blue", "explain"),
    ("explain how a refrigerator works", "heat|coolant|refrigerant|compress", "explain"),
    ("why do we have seasons", "tilt|axis|sun", "explain"),
    ("how does the internet work", "network|data|packets?|server|router", "explain"),
    ("what is the difference between a virus and a bacteria", "virus|bacteri", "explain"),
    ("how can i focus better while studying", "break|phone|distraction|goal|timer|sleep|quiet", "explain"),
    ("explain recursion with an example", "itself|call|base", "explain"),
    ("monsoon kyun aata hai", "hawa|wind|garmi|samundar|nami|baarish|monsoon", "explain"),
    ("neend nahi aati to kya karun", "sleep|neend|phone|screen|routine|caffeine|chai|coffee|raat", "explain"),
    ("what should i keep in mind while buying a laptop", "ram|ssd|processor|battery|budget|display|storage", "explain"),
]

SENT_RE = re.compile(r"(?<=[.!?।])\s+")


def sentences(text: str) -> int:
    return len([p for p in SENT_RE.split(text.strip()) if len(p.strip()) > 2])


def git_old_prompt(lang: str, rev: str = "HEAD") -> str:
    """The `_build_base_prompt(lang)` of an earlier commit (default HEAD), run from its source text."""
    import subprocess

    src = subprocess.run(["git", "show", f"{rev}:sara/core/llm/prompt.py"], capture_output=True, text=True,
                         cwd=str(Path(__file__).resolve().parent.parent)).stdout
    ns: dict = {"__name__": "old_prompt"}
    exec(compile(src, "old_prompt.py", "exec"), ns)  # noqa: S102 -- our own committed file
    return ns["_build_base_prompt"]("Sara", "It is currently evening.", lang, "Manvendra")


def prompts(lang: str = "hinglish"):
    tail = _security_rule() + _ACTION_RULES
    new = _build_base_prompt("Sara", "It is currently evening.", lang, "Manvendra")
    if lang == "hinglish":
        return {"old": OLD_HINGLISH + tail, "new": new}
    return {"old": git_old_prompt(lang), "new": new}


def ask_ollama(system: str, question: str, note: str):
    body = {"model": Config.OLLAMA_MODEL, "stream": False, "think": False, "keep_alive": "10m",
            "messages": [{"role": "system", "content": system}, {"role": "system", "content": NOTE_DT},
                         {"role": "system", "content": note}, {"role": "user", "content": question}],
            "options": {"num_ctx": 4096, "num_predict": 300, "temperature": 0.2, "top_k": 30, "top_p": 0.9,
                        "repeat_penalty": 1.08, "num_gpu": 99}}
    req = urllib.request.Request(Config.OLLAMA_HOST.rstrip("/") + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))["message"]["content"]


_gem = {}


def ask_gemini(system: str, question: str, note: str):
    from google.genai import types

    from sara.core.llm.clients import _build_gemini_generate_config, _get_gemini_client

    client = _gem.setdefault("c", _get_gemini_client(Config))
    model = Config.GEMINI_FAST_MODEL or Config.GEMINI_MODEL
    cfg = _build_gemini_generate_config(types, 30, model=model, system_instruction=system + " " + NOTE_DT + " " + note,
                                        max_output_tokens=400, temperature=0.4)
    for attempt in range(5):
        time.sleep(1.2)  # free-tier Gemini answers a burst with 429/503; pace the calls
        try:
            text = client.models.generate_content(model=model, contents=question, config=cfg).text or ""
            if text:
                return text
        except Exception as e:  # noqa: BLE001
            _gem["errors"] = _gem.get("errors", 0) + 1
            _gem["last"] = type(e).__name__
        time.sleep(2.0 * (attempt + 1))
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["ollama", "gemini"], default="ollama")
    ap.add_argument("--prompts", nargs="*", default=["old", "new"])
    ap.add_argument("--lang", choices=["hinglish", "english", "hindi"], default="hinglish",
                    help="english / hindi compare the current prompt of that language with the one at git HEAD")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    ask = ask_ollama if args.backend == "ollama" else ask_gemini
    allp = prompts(args.lang)
    questions = [q for q in Q if (q[2] != 'hi' if args.lang == 'english' else True)]
    for name in args.prompts:
        stats = {k: [0, 0] for k in ("fact", "maths", "hi", "explain")}
        length_ok = {"fact": [0, 0], "explain": [0, 0]}
        total_t = 0.0
        lens = {"fact": [], "explain": []}
        for q, rx, kind in questions:
            note = "Reply in Hinglish." if kind == "hi" or kind == "explain" and re.search(r"kyun|karun|nahi", q) else "Reply to this message in English only."
            t = time.perf_counter()
            a = ask(allp[name], q, note)
            total_t += time.perf_counter() - t
            good = bool(re.search(rx, a, re.I)) if rx else True
            stats[kind][0] += good
            stats[kind][1] += 1
            n = sentences(a)
            if kind in length_ok:
                lens[kind].append(n)
                ok = (1 <= n <= 2) if kind == "fact" else (3 <= n <= 6)
                length_ok[kind][0] += ok
                length_ok[kind][1] += 1
            if args.verbose and not good:
                print(f"   X [{kind}] {q} -> {a[:90]!r}")
        acc = " ".join(f"{k}={v[0]}/{v[1]}" for k, v in stats.items())
        ln = " ".join(f"{k}: {v[0]}/{v[1]} in range (mean {sum(lens[k]) / max(1, len(lens[k])):.1f} sentences)" for k, v in length_ok.items())
        if _gem.get("errors"):
            print(f"   ({_gem['errors']} Gemini errors so far, last {_gem['last']})")
        print(f"{args.backend:7} {name:4} accuracy {acc} | length {ln} | {total_t / len(questions):.2f}s/question")
    return 0


if __name__ == "__main__":
    sys.exit(main())
