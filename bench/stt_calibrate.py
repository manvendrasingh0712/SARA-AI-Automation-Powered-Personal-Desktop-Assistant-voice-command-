"""
Guided calibration of the speech recogniser with YOUR voice and YOUR microphone.

    python -m bench.stt_calibrate            # ~4 minutes: you read 38 short sentences, then it tunes the settings
    python -m bench.stt_calibrate --replay   # tune again from the audio saved last time (no speaking)
    python -m bench.stt_calibrate --apply    # also write the result into .env

Why: the rule that decides when the Hindi/Hinglish model's text replaces the main model's text
(WHISPER_HINGLISH_MIN_EN_PROB / WHISPER_HINGLISH_MIN_LOGPROB, see sara/audio/stt/helpers.py choose_transcript) was
tuned on synthetic speech. Real microphones and real voices give different confidence numbers, so the thresholds are
re-fitted here: every threshold pair is replayed over your recordings and the one that understands the most
sentences while calling the (slower) Hinglish model least is chosen.

Close SARA first (it uses the microphone and the GPU). Recordings stay in logs/stt_calib on this PC.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

# prompts: (kind, text you read)   kind: en_cmd | hi_cmd | en_chat | hi_chat
PROMPTS = [
    ("en_cmd", "open Chrome"), ("en_cmd", "what is the weather in Mumbai today"), ("en_cmd", "set a timer for ten minutes"),
    ("en_cmd", "play Arijit Singh songs on YouTube"), ("en_cmd", "turn the volume down a bit"), ("en_cmd", "take a screenshot"),
    ("en_cmd", "pause the music"), ("en_cmd", "next track"), ("en_cmd", "close notepad"), ("en_cmd", "what time is it"),
    ("en_cmd", "mute the volume"), ("en_cmd", "search for python tutorials"), ("en_cmd", "lock the computer"),
    ("en_cmd", "increase the brightness"),
    ("hi_cmd", "Chrome kholo"), ("hi_cmd", "volume badha do"), ("hi_cmd", "gaana pause karo"), ("hi_cmd", "Jaipur ka mausam kaisa hai"),
    ("hi_cmd", "Spotify par Kesariya chalao"), ("hi_cmd", "brightness kam karo"), ("hi_cmd", "screenshot le lo"),
    ("hi_cmd", "agla gaana chalao"), ("hi_cmd", "notepad band karo"), ("hi_cmd", "mere baare me kya kya jante ho"),
    ("hi_cmd", "aaj ka schedule batao"), ("hi_cmd", "awaaz kam karo"), ("hi_cmd", "mute karo"), ("hi_cmd", "battery kitni hai"),
    ("en_chat", "who is the prime minister of India"), ("en_chat", "tell me a story about a king"), ("en_chat", "how are you today"),
    ("en_chat", "what is the capital of France"), ("en_chat", "I want to plan a trip to Goa"),
    ("hi_chat", "tum kaisi ho"), ("hi_chat", "mujhe ek kahani sunao"), ("hi_chat", "bharat ke pradhan mantri kaun hain"),
    ("hi_chat", "aaj mausam kitna accha hai"), ("hi_chat", "mujhe goa ghumne jana hai"),
]

SR = 16000
OUT = None


def _out_dir() -> Path:
    from config import app_data_subdir

    p = Path(str(app_data_subdir("logs"))) / "stt_calib"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _record_one(sd, gate_floor: float) -> np.ndarray:
    """Record until the speaker stops: start above `gate`, stop after 0.9 s below half of it (max 9 s)."""
    block = 320
    frames = []
    pre = []
    started = False
    quiet = 0
    t0 = time.time()
    with sd.InputStream(device=None, samplerate=SR, channels=1, dtype="int16", blocksize=block) as stream:
        while time.time() - t0 < 14:
            data, _ = stream.read(block)
            x = data[:, 0].copy()
            rms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2)))
            if not started:
                pre.append(x)
                pre = pre[-10:]
                if rms > gate_floor:
                    started = True
                    frames = pre[:]
                    t_start = time.time()
            else:
                frames.append(x)
                quiet = quiet + 1 if rms < gate_floor * 0.5 else 0
                if quiet * block / SR > 0.9 or time.time() - t_start > 9:
                    break
    return np.concatenate(frames) if frames else np.zeros(0, np.int16)


def _save_wav(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.astype(np.int16).tobytes())


def _load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def record_session(out: Path) -> list:
    import sounddevice as sd

    try:
        import winsound

        beep = lambda: winsound.Beep(900, 120)
    except Exception:
        beep = lambda: None
    print(f"You will read {len(PROMPTS)} short sentences (about 4 minutes). A beep means: speak now, at your normal")
    print("volume and distance; say it once, then wait for the next sentence. Keep other audio off.")
    input("\nPress Enter when you are ready ... ")
    print("Room check: stay silent for 2 seconds ...")
    amb = sd.rec(int(2 * SR), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    noise = float(np.sqrt(np.mean(amb.astype(np.float32) ** 2)))
    gate = max(60.0, noise * 6)
    print(f"room noise RMS {noise:.0f}  ->  speech gate {gate:.0f}\n")
    meta = []
    for i, (kind, text) in enumerate(PROMPTS):
        print(f"[{i + 1:2d}/{len(PROMPTS)}]  Say:  {text}")
        time.sleep(0.4)
        beep()
        pcm = _record_one(sd, gate)
        if len(pcm) < SR * 0.3:
            print("          (nothing heard - skipped; speak a little louder or closer)")
            continue
        f = out / f"{i:02d}_{kind}.wav"
        _save_wav(f, pcm)
        meta.append({"i": i, "kind": kind, "ref": text, "file": f.name})
        time.sleep(0.5)
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


# ------------------------------------------------------------------ recognition + scoring
_norm = lambda s: re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def _wer(ref: str, hyp: str) -> float:
    r, h = _norm(ref), _norm(hyp)
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for a in range(len(r) + 1):
        d[a][0] = a
    for b in range(len(h) + 1):
        d[0][b] = b
    for a in range(1, len(r) + 1):
        for b in range(1, len(h) + 1):
            d[a][b] = min(d[a - 1][b] + 1, d[a][b - 1] + 1, d[a - 1][b - 1] + (r[a - 1] != h[b - 1]))
    return d[-1][-1] / max(1, len(r))


def _sim(ref: str, hyp: str) -> float:
    return difflib.SequenceMatcher(None, " ".join(_norm(ref)), " ".join(_norm(hyp))).ratio()


def _add_cuda_dll_dirs() -> None:
    """The CUDA runtime DLLs ship inside the pip `nvidia-*` packages; Windows only finds them once added."""
    try:
        import nvidia

        for root in list(nvidia.__path__):
            for sub in os.listdir(root):
                for d in (os.path.join(root, sub, "bin"), os.path.join(root, sub, "bin", "x86_64")):
                    if os.path.isdir(d):
                        os.add_dll_directory(d)
                        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    except Exception:
        pass


def recognise(meta: list, out: Path) -> list:
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    from config import Config

    print("\nLoading the recognisers (same models and devices as the app) ...")
    a_dev = str(Config.WHISPER_MAIN_DEVICE or "cuda").lower()
    a_model = WhisperModel(Config.WHISPER_MODEL_SIZE, device=a_dev,
                           compute_type="int8_float16" if a_dev == "cuda" else "int8", **({} if a_dev == "cuda" else {"cpu_threads": 8}))
    b_name = Config.WHISPER_HINGLISH_MODEL
    b_dev = str(Config.WHISPER_HINGLISH_DEVICE or "cpu").lower()
    if b_dev == "auto":
        b_dev = "cpu"
    try:
        b_model = (WhisperModel(b_name, device=b_dev, compute_type="int8_float16" if b_dev == "cuda" else "int8",
                                **({} if b_dev == "cuda" else {"cpu_threads": 8})) if b_name else None)
    except Exception as e:
        print(f"  (Hinglish model on {b_dev} failed: {e}; using the CPU)")
        b_model = WhisperModel(b_name, device="cpu", compute_type="int8", cpu_threads=8)
    rows = []
    for m in meta:
        pcm = _load_wav(out / m["file"])
        raw = pcm.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(raw ** 2))) or 1e-6
        leveled = np.clip(raw * min(0.08 / rms, 30.0), -1.0, 1.0) if getattr(Config, "WHISPER_MAIN_GAIN", True) else raw
        segs, info = a_model.transcribe(leveled, beam_size=2, vad_filter=False, condition_on_previous_text=False)
        segs = list(segs)
        a_text = "".join(s.text for s in segs).strip()
        a_lp = float(np.mean([s.avg_logprob for s in segs])) if segs else -9.0
        row = dict(m, a_text=a_text, a_lang=info.language, a_prob=float(info.language_probability), a_lp=a_lp,
                   rms=float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))), seconds=len(pcm) / SR)
        if b_model is not None:
            t0 = time.perf_counter()
            bs, _ = b_model.transcribe(raw, beam_size=2, vad_filter=False, condition_on_previous_text=False,
                                       temperature=0.0, compression_ratio_threshold=2.0, log_prob_threshold=-1.0)
            bs = list(bs)
            row["b_text"] = "".join(s.text for s in bs).strip()
            row["b_lp"] = float(np.mean([s.avg_logprob for s in bs])) if bs else -9.0
            row["b_ms"] = (time.perf_counter() - t0) * 1000
        rows.append(row)
        print(f"  {m['ref']!r:42} Hinglish={row.get('b_text', '')[:34]!r:38} main={a_text[:34]!r}")
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def _score(rows: list, chooser):
    """Mean score over the sentences and the share of them that needed the second (slow) model."""
    from sara.core.intent.engine import detect_intent

    total, n, second = 0.0, 0, 0
    for r in rows:
        out = chooser(r)
        text = out[1]
        second += bool(out[2]) if len(out) > 2 else 0
        if r["kind"].endswith("_cmd"):
            s = 1.0 if detect_intent(text)[0] == detect_intent(r["ref"])[0] else 0.0
        elif r["kind"] == "en_chat":
            s = max(0.0, 1.0 - _wer(r["ref"], text))
        else:
            s = _sim(r["ref"], text)
        total += s
        n += 1
    return total / max(1, n), second / max(1, n)


def tune(rows: list) -> dict:
    from sara.audio.stt.helpers import (_is_degenerate_text, _is_hallucinated_repetition, need_main_model,
                                        pick_after_hinglish_first)
    from sara.core.intent.engine import detect_intent

    routes = lambda t: detect_intent(t)[0] != "chat"

    def usable_b(r):
        b = r.get("b_text", "")
        bad = b and (_is_hallucinated_repetition(b, 3) or _is_degenerate_text(b) or r.get("b_lp", -9) < -1.0)
        return "" if bad else b

    def make(p, lp, clp):
        def chooser(r):
            b = usable_b(r)
            if not need_main_model(b, r.get("b_lp", -9.0), routes, clp):
                return "B", b, False
            which, text = pick_after_hinglish_first(b, r["a_text"], r["a_lang"], r["a_prob"], r["a_lp"], routes, p, lp)
            return which, text, True
        return chooser

    sa, _ = _score(rows, lambda r: ("A", r["a_text"], False))
    sb, _ = _score(rows, lambda r: ("B", usable_b(r), False))
    print(f"\nMain model alone: {100 * sa:.0f} %   |   Hinglish model alone: {100 * sb:.0f} %   (score over {len(rows)} sentences)")
    grid = []
    for p in (0.5, 0.7, 0.85, 0.9, 0.95):
        for lp in (-1.0, -0.8, -0.6, -0.5, -0.4, -0.2):
            for clp in (-0.2, -0.3, -0.4, -0.6, 0.0):
                s_, frac = _score(rows, make(p, lp, clp))
                grid.append((p, lp, clp, s_, frac))
    # Ties are common on a small sample: best score, then (within half a point) the pair that needs the slow main
    # model least, then the pair nearest the shipped defaults.
    top = max(g[3] for g in grid)
    near = [g for g in grid if g[3] >= top - 0.005]
    cheapest = min(g[4] for g in near)
    near = [g for g in near if g[4] <= cheapest + 0.03]
    p, lp, clp, s_, frac = min(near, key=lambda g: abs(g[0] - 0.9) + abs(g[1] + 0.5) + abs(g[2] + 0.3))
    print("\nbest threshold sets:")
    for g in sorted(grid, key=lambda g: (-round(g[3], 4), g[4]))[:5]:
        print(f"   min_en_prob={g[0]:.2f}  min_logprob={g[1]:+.1f}  confident_lp={g[2]:+.1f}   score {100 * g[3]:5.1f} %   main model needed for {100 * g[4]:3.0f} %")
    cur = (float(os.getenv("WHISPER_HINGLISH_MIN_EN_PROB", "0.9")), float(os.getenv("WHISPER_HINGLISH_MIN_LOGPROB", "-0.5")),
           float(os.getenv("WHISPER_HINGLISH_CONFIDENT_LP", "-0.3")))
    cs, cf = _score(rows, make(*cur))
    print(f"\ncurrent settings {cur}: {100 * cs:.1f} % ({100 * cf:.0f} % need the main model)  ->  recommended ({p}, {lp}, {clp}): "
          f"{100 * s_:.1f} % ({100 * frac:.0f} %)")
    rms = np.array([r["rms"] for r in rows])
    print(f"your speech level: RMS median {np.median(rms):.0f}, 10th percentile {np.percentile(rms, 10):.0f}")
    return {"WHISPER_HINGLISH_MIN_EN_PROB": p, "WHISPER_HINGLISH_MIN_LOGPROB": lp, "WHISPER_HINGLISH_CONFIDENT_LP": clp,
            "score": s_, "current_score": cs}


def apply_env(values: dict) -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    text = env.read_text(encoding="utf-8")
    for key in ("WHISPER_HINGLISH_MIN_EN_PROB", "WHISPER_HINGLISH_MIN_LOGPROB", "WHISPER_HINGLISH_CONFIDENT_LP"):
        line = f"{key}={values[key]}"
        if re.search(rf"^{key}=.*$", text, flags=re.M):
            text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"
    env.write_text(text, encoding="utf-8")
    print(f"written to {env}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", action="store_true", help="re-tune from the recordings saved last time")
    ap.add_argument("--apply", action="store_true", help="write the recommended values into .env")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    out = _out_dir()
    if args.replay:
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    else:
        meta = record_session(out)
    if not meta:
        print("No recordings - nothing to tune.")
        return 1
    rows = recognise(meta, out)
    best = tune(rows)
    if args.apply and len(rows) < 20:
        print(f"Only {len(rows)} usable recordings (need 20): not changing .env. Run again and speak closer to the microphone.")
    elif args.apply:
        apply_env(best)
    else:
        print("\nRun again with --replay --apply to write these values into .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
