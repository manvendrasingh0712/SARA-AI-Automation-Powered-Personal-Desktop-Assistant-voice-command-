import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import sara.skills._framework  # noqa: F401
from bench import dataset
from sara.core.intent import patterns

english = {r.expected_intent for r in dataset.load_rows() if r.lang == "en" and r.kind == "positive"}
lines = []
for name, spec in patterns._INTENT_PATTERNS:
    if name not in english:
        lines.append(f"{name}\t{repr(spec)[:500]}")
out = "\n".join(lines) or "nothing missing"
print(out)
(ROOT / "missing_intents.txt").write_text(out, encoding="utf-8")