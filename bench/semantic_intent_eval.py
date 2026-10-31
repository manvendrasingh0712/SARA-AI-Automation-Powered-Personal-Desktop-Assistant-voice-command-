"""Measure the semantic intent fallback on the golden sets (Ollama running, embedding model pulled).

    python bench/semantic_intent_eval.py

Split: every 3rd positive golden row of tier 0-1 intents is held out; the rest are the examples. Reported per threshold:
accuracy on the held-out positives that cleared the threshold, the share of held-out positives answered at all, and the
false-trigger rate on the negatives (plain chat) -- these are what decide whether the fallback may be switched on."""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sara.skills  # noqa: F401,E402
from config import Config  # noqa: E402
from sara.core.intent.semantic import SemanticIntentIndex  # noqa: E402
from sara.core.llm.clients import _get_embedding_vector  # noqa: E402
from sara.core.security.tiers import tier_of  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden"


def rows(name):
    out = []
    for line in (GOLDEN / name).read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            d = json.loads(line)
            out.append((d["t"], d["i"]))
    return out


def main() -> int:
    Config.EMBEDDING_BACKEND = "ollama"
    embed = lambda t: _get_embedding_vector(Config, t)  # noqa: E731
    positives = []
    for f in ("intents_en.jsonl", "intents_hinglish.jsonl", "intents_hi.jsonl"):
        positives += [(t, i) for t, i in rows(f) if i != "chat" and tier_of(i) <= 1]
    negatives = [t for t, _ in rows("negatives.jsonl")] + [t for t, _ in rows("heldout_question_negatives.jsonl")]
    train = [r for k, r in enumerate(positives) if k % 3 != 0]
    test = [r for k, r in enumerate(positives) if k % 3 == 0]
    examples = defaultdict(list)
    for t, i in train:
        examples[i].append(t)
    t0 = time.perf_counter()
    idx = SemanticIntentIndex(tau_high=0.0, margin=0.0)
    n = idx.build(examples, embed, tier_of)
    print(f"index: {n} example phrases, {len(examples)} intents, built in {time.perf_counter() - t0:.1f} s")
    scored = [(idx.classify(t, embed), i) for t, i in test]
    neg_scored = [idx.classify(t, embed) for t in negatives]
    lat = []
    for t, _ in test[:20]:
        s = time.perf_counter()
        idx.classify(t + " ", embed)   # a new string: cache miss
        lat.append((time.perf_counter() - s) * 1000)
    print(f"classify latency (cache miss): median {sorted(lat)[len(lat) // 2]:.0f} ms | {len(test)} held-out positives, {len(negatives)} negatives")
    print("tau   margin | answered  accuracy(answered) | false triggers on negatives")
    for tau in (0.70, 0.75, 0.80, 0.85, 0.90):
        for margin in (0.0, 0.04):
            ans = [(r, i) for r, i in scored if r[1] >= tau and r[2] >= margin]
            ok = sum(1 for r, i in ans if r[0] == i)
            fp = sum(1 for r in neg_scored if r[1] >= tau and r[2] >= margin)
            print(f"{tau:.2f}  {margin:.2f}   | {len(ans) / len(scored):6.1%}    {ok / max(1, len(ans)):6.1%}              | {fp}/{len(neg_scored)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
