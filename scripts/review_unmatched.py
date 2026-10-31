"""Turn the unmatched-query log into pattern/golden-set candidates (audit part 2, O8).

    python scripts/review_unmatched.py                      # top clusters, with the closest known intent as a suggestion
    python scripts/review_unmatched.py --top 30 --min 2     # more clusters, only phrases seen at least twice
    python scripts/review_unmatched.py --accept 3:set_volume 7:chat
                                                            # append cluster 3's phrases as set_volume (and 7's as chat negatives)
                                                            # to bench/golden/learned_from_unmatched.jsonl

Nothing is added to the router by this script. Accepted phrases go to a golden file the owner reviews, and `bench/run_bench`
then shows whether a new pattern fixes them without breaking anything. Phrases already handled by `detect_intent`
(the router learned them since) are dropped.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "bench" / "golden"
OUT = GOLDEN / "learned_from_unmatched.jsonl"
STOP = set("the a an is are to of and or in on for me my you your it this that kya hai ho hain ka ki ke ko se me mein par pe aur "
           "ek please sara hey hi hello can could would".split())


def tokens(text: str):
    return [t for t in re.findall(r"[\w']+", text.lower()) if t not in STOP and len(t) > 1]


def load(path: Path):
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                t = json.loads(line).get("text", "").strip()
            except ValueError:
                continue
            if t:
                rows.append(t)
    return rows


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def cluster(phrases, threshold=0.5):
    """Greedy: each phrase joins the first cluster whose centroid phrase shares >= threshold of its words."""
    clusters = []   # [centroid tokens, Counter of phrases]
    for text, n in phrases.items():
        tk = tokens(text)
        if not tk:
            continue
        for c in clusters:
            if jaccard(tk, c[0]) >= threshold:
                c[1][text] += n
                break
        else:
            clusters.append([tk, Counter({text: n})])
    return sorted(clusters, key=lambda c: -sum(c[1].values()))


def known_examples():
    ex = []
    for f in ("intents_en.jsonl", "intents_hinglish.jsonl", "intents_hi.jsonl"):
        p = GOLDEN / f
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines()[1:]:
                if line.strip():
                    d = json.loads(line)
                    ex.append((d["i"], tokens(d["t"])))
    return ex


def suggest(tk, examples):
    best = max(examples, key=lambda e: jaccard(tk, e[1]), default=None)
    return (best[0], jaccard(tk, best[1])) if best else (None, 0.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", help="unmatched log (default: Config.UNMATCHED_LOG_PATH)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--min", type=int, default=1, help="only phrases seen at least this many times")
    ap.add_argument("--accept", nargs="*", default=[], metavar="N:intent")
    args = ap.parse_args(argv)

    from config import Config

    path = Path(args.log or Config.UNMATCHED_LOG_PATH)
    counts = Counter(load(path))
    if args.min > 1:
        counts = Counter({k: v for k, v in counts.items() if v >= args.min})
    # drop what the router handles by now
    import sara.skills  # noqa: F401
    from sara.core.intent.engine import detect_intent

    counts = Counter({k: v for k, v in counts.items() if detect_intent(k)[0] == "chat"})
    clusters = cluster(counts)[: args.top]
    examples = known_examples()
    print(f"{sum(counts.values())} unmatched utterances, {len(counts)} distinct, showing {len(clusters)} clusters from {path}\n")
    for n, (tk, phrases) in enumerate(clusters, 1):
        intent, score = suggest(tk, examples)
        hint = f"closest intent: {intent} ({score:.2f})" if intent and score >= 0.34 else "no close intent (likely chat)"
        print(f"[{n}] x{sum(phrases.values())}  {hint}")
        for text, c in phrases.most_common(3):
            print(f"      {c}x  {text}")
    accepted = []
    for item in args.accept:
        num, _, intent = item.partition(":")
        if num.isdigit() and 1 <= int(num) <= len(clusters) and intent:
            for text in clusters[int(num) - 1][1]:
                accepted.append({"t": text, "i": intent})
    if accepted:
        new = not OUT.exists()
        with OUT.open("a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write(json.dumps({"_defaults": {"lang": "hinglish", "kind": "positive", "style": "casual"}}) + "\n")
            for row in accepted:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"\nappended {len(accepted)} phrases to {OUT} (review it; add it to bench/dataset.py DEFAULT_FILES to benchmark it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
