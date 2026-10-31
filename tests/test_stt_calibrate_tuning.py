from bench import stt_calibrate as cal


def _row(kind, ref, a_text, a_lang="en", a_prob=0.9, a_lp=-0.3, b_text=""):
    return {"kind": kind, "ref": ref, "a_text": a_text, "a_lang": a_lang, "a_prob": a_prob, "a_lp": a_lp,
            "b_text": b_text, "b_lp": -0.3, "rms": 1500.0, "seconds": 2.0}


ROWS = (
    [_row("en_cmd", "open Chrome", "Open Chrome.") for _ in range(8)]
    + [_row("hi_cmd", "Chrome kholo", "Drum kolu.", a_prob=0.5, a_lp=-1.1, b_text="Chrome kholo.") for _ in range(8)]
    + [_row("en_chat", "how are you today", "How are you today?", b_text="Hau aar yu tu de") for _ in range(4)]
    + [_row("hi_chat", "tum kaisi ho", "Tun che sigo", a_lang="es", a_prob=0.3, a_lp=-1.0, b_text="Tum kaisi ho") for _ in range(4)]
)


def test_scores_prefer_the_pair_of_models_over_either_alone(capsys):
    best = cal.tune(ROWS)
    out = capsys.readouterr().out
    assert "Main model alone" in out
    assert best["score"] > 0.95 and best["score"] >= best["current_score"]


def test_chosen_thresholds_are_in_the_search_grid():
    best = cal.tune(ROWS)
    assert 0.5 <= best["WHISPER_HINGLISH_MIN_EN_PROB"] <= 0.95
    assert -1.2 <= best["WHISPER_HINGLISH_MIN_LOGPROB"] <= -0.2


def test_apply_env_updates_or_adds_both_keys(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\nWHISPER_HINGLISH_MIN_EN_PROB=0.85\n", encoding="utf-8")
    monkeypatch.setattr(cal, "__file__", str(tmp_path / "bench" / "stt_calibrate.py"))
    cal.apply_env({"WHISPER_HINGLISH_MIN_EN_PROB": 0.7, "WHISPER_HINGLISH_MIN_LOGPROB": -0.8, "WHISPER_HINGLISH_CONFIDENT_LP": -0.2})
    text = env.read_text(encoding="utf-8")
    assert "WHISPER_HINGLISH_MIN_EN_PROB=0.7" in text and "WHISPER_HINGLISH_MIN_LOGPROB=-0.8" in text
    assert "WHISPER_HINGLISH_CONFIDENT_LP=-0.2" in text and "A=1" in text
