"""Deterministic noisy-variant generator: intents_en.jsonl -> intents_en_noisy.jsonl."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.dataset import GOLDEN_DIR, load_rows  # noqa: E402

SEED = 1337
SOURCE = GOLDEN_DIR / "intents_en.jsonl"
TARGET = GOLDEN_DIR / "intents_en_noisy.jsonl"

_FILLERS = ("uh", "um", "please")
_EDGE = ".,!?;:\"'"
_DROPPABLE = frozenset({
    "the", "a", "an", "my", "me", "please", "kindly", "just", "this", "that",
    "now", "can", "you", "could", "would",
})
_HOMOPHONES: Dict[str, str] = {
    "to": "two", "two": "to", "for": "four", "four": "for",
    "wait": "weight", "weight": "wait", "no": "know", "know": "no",
    "weather": "whether", "whether": "weather", "write": "right",
    "right": "write", "hear": "here", "here": "hear", "by": "buy",
    "buy": "by", "one": "won", "won": "one",
}

Tokens = List[str]
Op = Callable[[Tokens, random.Random, Set[str]], Tokens]


def _core(token: str) -> str:
    return token.strip(_EDGE)


def _drop(tokens: Tokens, rng: random.Random, protected: Set[str]) -> Tokens:
    idx = [
        i for i, t in enumerate(tokens)
        if _core(t).lower() in _DROPPABLE and _core(t).lower() not in protected
    ]
    if len(tokens) < 3 or not idx:
        return tokens
    skip = rng.choice(idx)
    return tokens[:skip] + tokens[skip + 1:]


def _homophone(tokens: Tokens, rng: random.Random, protected: Set[str]) -> Tokens:
    idx = [
        i for i, t in enumerate(tokens)
        if _core(t).lower() in _HOMOPHONES and _core(t).lower() not in protected
    ]
    if not idx:
        return tokens
    pos = rng.choice(idx)
    core = _core(tokens[pos])
    swap = _HOMOPHONES[core.lower()]
    if core[:1].isupper():
        swap = swap.capitalize()
    out = list(tokens)
    out[pos] = tokens[pos].replace(core, swap, 1)
    return out


def _filler(tokens: Tokens, rng: random.Random, protected: Set[str]) -> Tokens:
    return [rng.choice(_FILLERS)] + tokens


def _lower_strip(tokens: Tokens, rng: random.Random, protected: Set[str]) -> Tokens:
    return [w for w in (t.lower().strip(_EDGE) for t in tokens) if w]


_OPS: Dict[str, Op] = {
    "drop": _drop,
    "homophone": _homophone,
    "filler": _filler,
    "lower_strip": _lower_strip,
}


def _noisy_text(text: str, groups: Sequence[str], rng: random.Random,
                taken: Set[str]) -> str:
    """Return a noisy variant of `text`, never equal to it or to any taken text."""
    protected = {_core(w).lower() for g in groups for w in g.split()}
    names = list(_OPS)
    planned = set(rng.sample(names, rng.randint(1, 2)))
    tokens = text.split()
    for name in names:
        if name in planned:
            tokens = _OPS[name](tokens, rng, protected)
    result = " ".join(tokens)
    for name in names:
        if result != text:
            break
        if name not in planned:
            tokens = _OPS[name](tokens, rng, protected)
            result = " ".join(tokens)
    while result == text or result in taken:
        result = f"um {result}"
    return result


def generate() -> str:
    """Build the full noisy JSONL text from the English golden file."""
    rows = load_rows([SOURCE])
    rng = random.Random(SEED)
    taken: Set[str] = {row.text for row in rows}
    header = {"_defaults": {"lang": "en", "kind": "positive", "style": "noisy"}}
    lines = [json.dumps(header, ensure_ascii=False, separators=(",", ":"))]
    for row in rows:
        text = _noisy_text(row.text, row.expected_groups, rng, taken)
        taken.add(text)
        obj: Dict[str, object] = {"t": text, "i": row.expected_intent}
        if row.lang != "en":
            obj["l"] = row.lang
        groups = [g for g in row.expected_groups if g in text.lower()]
        if len(groups) == len(row.expected_groups) and groups:
            obj["g"] = groups
        obj["n"] = f"noisy from {row.id}"
        lines.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="augment_noisy")
    parser.add_argument("--check", action="store_true",
                        help="verify the committed file equals regeneration")
    args = parser.parse_args(argv)
    try:
        expected = generate()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.check:
        try:
            current = TARGET.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        except OSError:
            print(f"STALE: {TARGET.name} is missing", file=sys.stderr)
            return 1
        if current != expected:
            print(f"STALE: {TARGET.name} differs from regeneration", file=sys.stderr)
            return 1
        print(f"OK: {TARGET.name} is up to date")
        return 0
    TARGET.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {expected.count(chr(10)) - 1} rows to {TARGET.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())