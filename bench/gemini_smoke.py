"""One tiny generate_content call per configured Gemini model, with and without the sampling parameters.

Prints: model, variant, OK / error class, latency. Never prints the API key.
Usage: python bench/gemini_smoke.py [--reasoning]    (--reasoning also asks a 4-sentence question to check truncation)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402
from sara.core.llm.clients import _build_gemini_generate_config, _get_gemini_client, _model_rejects_sampling  # noqa: E402


def models() -> list[str]:
    seen, out = set(), []
    for name in ("GEMINI_MODEL", "GEMINI_FAST_MODEL", "GEMINI_PRO_MODEL", "VISION_MODEL"):
        m = (getattr(Config, name, "") or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def main() -> int:
    from google.genai import types

    client = _get_gemini_client(Config)
    if client is None:
        print("no Gemini client (GEMINI_API_KEY missing?)")
        return 1
    reasoning = "--reasoning" in sys.argv
    prompt = "Explain why the sky is blue in 4 short sentences." if reasoning else "Say the word ok."
    print(f"{'model':32} {'variant':26} {'result':28} ms   chars")
    for model in models():
        for variant, kw in (("with sampling params", dict(temperature=0.7, top_p=0.9, top_k=40)),
                            ("builder (guarded)", dict(temperature=0.7, top_p=0.9, top_k=40))):
            if variant == "with sampling params":
                cfg = types.GenerateContentConfig(max_output_tokens=400 if reasoning else 20, **kw)
            else:
                cfg = _build_gemini_generate_config(types, 15, model=model, max_output_tokens=400 if reasoning else 20, **kw)
            t0 = time.perf_counter()
            try:
                r = client.models.generate_content(model=model, contents=prompt, config=cfg)
                res, n = "OK", len((r.text or ""))
            except Exception as e:  # noqa: BLE001
                res, n = type(e).__name__ + (f" {getattr(e, 'code', '')}" if getattr(e, "code", "") else ""), 0
            print(f"{model:32} {variant:26} {res:28} {(time.perf_counter() - t0) * 1000:5.0f} {n}")
        print(f"   guard drops sampling for {model}: {_model_rejects_sampling(model)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
