"""Tests for sara.audio.tts.chunker and its wiring in TextToSpeech._chunker_worker."""
import queue
import threading

import pytest

from sara.audio.tts import chunker, engine
from sara.audio.tts.chunker import ReplyRecorder, first_chunk_end
from sara.audio.voice_flags import reset_flags, set_flag


@pytest.fixture(autouse=True)
def _flags():
    reset_flags()
    yield
    reset_flags()


_CASES = [
    ('Hello there. How are you ', False, 'Hello there.'),
    ('Open notepad please! Then wait ', False, 'Open notepad please!'),
    ('Are you sure about that? I think so ', False, 'Are you sure about that?'),
    ('Okay. Opening notepad now for you. ', False, 'Okay. Opening notepad now for you.'),
    ('Yes. ', False, None),
    ('Hi. ', True, 'Hi.'),
    ('Hello there my friend, how are you doing today ', False, None),
    ('Well, ok ', False, None),
    ('First we open the file; then we edit it ok ', False, None),
    ('Note: the file is large and will take time ', False, None),
    ('This sentence has no punctuation but it keeps going on and on and on until sixty chars ', False, 'This sentence has no punctuation but it keeps going on and on'),
    ('short no punctuation here ', False, None),
    ('The value of pi is 3.14 and that is all we need ok', False, None),
    ('It costs 1,000 rupees for the whole thing and nothing more than that ok', False, 'It costs 1,000 rupees for the whole thing and nothing more than'),
    ('The meeting is at 12:30 tomorrow and it will be long so come ready ok', False, 'The meeting is at 12:30 tomorrow and it will be long so come'),
    ('Dr. Smith is here today. Please come ', False, 'Dr. Smith is here today.'),
    ('Mr. and Mrs. Sharma arrived at 5 p.m. and they sat down quietly ok ', False, 'Mr. and Mrs. Sharma arrived at 5 p.m. and they sat down quietly'),
    ('We bought apples, pears, etc. Then we left for the market today ', False, 'We bought apples, pears, etc. Then we left for the market today'),
    ('Use tools e.g. hammers and nails. Then build ', False, 'Use tools e.g. hammers and nails.'),
    ('That is i.e. the same thing as before. Next ', False, 'That is i.e. the same thing as before.'),
    ('Visit https://example.com/path.html for details about the very long product page ok', False, 'Visit https://example.com/path.html for details about the very'),
    ('Mail me at john.doe@example.com. Then call me ', False, 'Mail me at john.doe@example.com.'),
    ('Mail john.doe@example.co.in and then wait for the reply from the team here ok', False, 'Mail john.doe@example.co.in and then wait for the reply from'),
    ('नमस्ते दोस्तो। आज मौसम अच्छा है ', False, 'नमस्ते दोस्तो।'),
    ('क्या आप ठीक हैं? मैं ठीक हूँ ', False, 'क्या आप ठीक हैं?'),
    ('यह बहुत अच्छा है! चलो चलें ', False, 'यह बहुत अच्छा है!'),
    ('मुझे आज बाज़ार जाना है, फिर घर आकर खाना बनाना है ', False, None),
    ('Ek second, main check karti hoon abhi ', False, None),
    ('Haan bilkul, main abhi karti hoon aapka kaam. Phir batati hoon ', False, 'Haan bilkul, main abhi karti hoon aapka kaam.'),
    ('Main notepad khol rahi hoon aur phir volume badha rahi hoon abhi ', False, 'Main notepad khol rahi hoon aur phir volume badha rahi hoon abhi'),
    ('Sure! नमस्ते। How are you ', False, 'Sure! नमस्ते।'),
    ('1. Open the file and then close it properly. Next ', False, '1. Open the file and then close it properly.'),
    ('Wait... what happened? ', False, 'Wait... what happened?'),
    ('He said "Go home." Then he left ', False, 'He said "Go home."'),
    ('It is (really) great. Next ', False, 'It is (really) great.'),
    ('Version 2. Next part follows here ', False, 'Version 2.'),
    ('Hello there.', False, None),
    ('Hello there?', False, 'Hello there?'),
    ('Hello there. ', True, 'Hello there.'),
    ('no punctuation tail', True, 'no punctuation tail'),
    ('', True, None),
    ('   ', False, None),
    ('   Leading spaces sentence here. Next ', False, '   Leading spaces sentence here.'),
    ('J. Smith went to the market today. Next ', False, 'J. Smith went to the market today.'),
    ('Prof. Rao arrived. Next ', False, 'Prof. Rao arrived.'),
    ("St. Mary's church is nearby so we walked there slowly and happily ok ", False, "St. Mary's church is nearby so we walked there slowly and happily"),
    ('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx', False, None),
    ('xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx', True, 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'),
]


@pytest.mark.parametrize("buffer,final,expected", _CASES)
def test_first_chunk_end_cases(buffer, final, expected):
    cut = first_chunk_end(buffer, final)
    if expected is None:
        assert cut is None
    else:
        assert cut is not None
        assert buffer[:cut] == expected


def test_case_table_is_large_enough():
    assert len(_CASES) >= 40


def test_cut_never_splits_a_number():
    for buf in ("Pi is 3.14 and we move on quickly to the next topic ok 1,000 more",
                "It is 1,000 rupees exactly for the complete package deal at 12:30 ok"):
        cut = first_chunk_end(buf, True)
        assert cut is not None
        inside = cut < len(buf) and buf[cut - 1].isdigit() and buf[cut].isdigit()
        assert not inside
        assert not (buf[cut - 1] in ".,:" and cut < len(buf) and not buf[cut].isspace())


def test_safe_cut_protects_devanagari_conjuncts():
    word = "\u0915\u094d\u0937"  # kssa conjunct: ka + virama + ssa
    assert chunker._safe_cut(word, 2) is False  # right after the virama
    assert chunker._safe_cut(word, 1) is False  # before the virama (combining)
    assert chunker._safe_cut("\u0915\u093e ", 1) is False  # before a matra
    assert chunker._safe_cut("\u0915 \u0916", 2) is True
    assert chunker._safe_cut("abc", 0) is False
    assert chunker._safe_cut("abc", 4) is False
    assert chunker._safe_cut("a\u200d b", 2) is False  # after a joiner


def test_streaming_prefix_never_loses_text():
    text = "Dr. Rao said hello, and then walked to the shop. He bought 3.5 kg rice."
    for end in range(1, len(text) + 1):
        buf = text[:end]
        cut = first_chunk_end(buf, False)
        if cut is not None:
            assert 0 < cut <= len(buf)
            assert buf[:cut] == text[:cut]


def test_final_flag_flushes_remaining_text():
    assert first_chunk_end("tail without punctuation", False) is None
    assert first_chunk_end("tail without punctuation", True) == len("tail without punctuation")


def test_empty_and_blank_buffers():
    for buf in ("", " ", "\n\t "):
        assert first_chunk_end(buf, False) is None
        assert first_chunk_end(buf, True) is None


# ── ReplyRecorder ────────────────────────────────────────────────────────────

def test_recorder_passes_through_and_records():
    rec = ReplyRecorder(iter(["a b", "c d"]))
    assert list(rec) == ["a b", "c d"]
    assert rec.whole_reply(drain=False) == "a b c d"
    assert rec.error is False


def test_recorder_flags_source_error_and_reraises():
    def gen():
        yield "first"
        raise RuntimeError("boom")

    rec = ReplyRecorder(gen())
    with pytest.raises(RuntimeError):
        list(rec)
    assert rec.error is True
    assert rec.whole_reply(drain=True) == "first"


def test_recorder_drain_consumes_remaining_source():
    rec = ReplyRecorder(iter(["one", "two", "three"]))
    it = iter(rec)
    assert next(it) == "one"
    assert rec.whole_reply(drain=True) == "one two three"


def test_recorder_is_bounded():
    rec = ReplyRecorder(iter(["x" * 10] * 5), max_chars=25)
    assert len(list(rec)) == 5
    assert rec.overflow is True
    assert len(rec.whole_reply(drain=False)) <= 25 + 2


def test_recorder_survives_non_iterable_source():
    rec = ReplyRecorder(None)  # type: ignore[arg-type]
    assert rec.error is True
    assert list(rec) == []


# ── Wiring: flag off == legacy, flag on == first_chunk_end ──────────────────

def _bare_tts():
    tts = engine.TextToSpeech.__new__(engine.TextToSpeech)
    tts._stop = threading.Event()
    return tts


def _collect(tts, pieces):
    q = queue.Queue()
    tts._chunker_worker(iter(pieces), q)
    out = []
    while True:
        item = q.get_nowait()
        if item is None:
            return out
        out.append(item)


def _legacy_chunks(tts, pieces):
    """Literal copy of the pre-T13 _chunker_worker loop (reference)."""
    out = []
    buf = ""
    first_pushed = False
    for raw in pieces:
        buf += raw + " "
        if not first_pushed and len(buf.strip()) >= engine._FIRST_TRIGGER:
            m = tts._first_flush_match(buf)
            if m:
                part = buf[: m.end()].strip()
                if part and len(part) >= engine._MIN_CHUNK:
                    out.append(part)
                    first_pushed = True
                    buf = buf[m.end():]
                    continue
        segs = engine._split_adaptive(buf)
        if len(segs) > 1:
            for s in segs[:-1]:
                s = s.strip()
                if s:
                    out.append(s)
                    first_pushed = True
            buf = segs[-1]
    tail = buf.strip()
    if tail:
        out.append(tail)
    return out


_STREAMS = [
    ["Hello there. How are you doing today?", "I am fine, thanks."],
    ["Dr.", "Smith", "is", "here", "today.", "Please", "come", "in."],
    ["Open", "notepad", "now,", "then", "type", "something", "nice", "for", "me."],
    ["Pi is 3.14.", "It is a number!", "Really? Yes."],
    ["नमस्ते दोस्तो।", "आज मौसम अच्छा है।", "चलो चलें।"],
    ["Ek second, main check karti hoon.", "Haan, ho gaya!"],
    ["short"],
    [""],
    [],
    ["a " * 120],
    ["Sure! Okay. Done.", "Visit https://example.com now.", "Bye."],
]


@pytest.mark.parametrize("pieces", _STREAMS)
def test_flag_off_matches_legacy(pieces):
    set_flag("VOICE_CHUNKER_V2", False)
    tts = _bare_tts()
    assert _collect(tts, pieces) == _legacy_chunks(tts, pieces)


def test_flag_on_does_not_cut_after_abbreviation():
    pieces = ["Dr.", "Smith", "is", "here", "today.", "Please", "come", "in."]
    tts = _bare_tts()
    set_flag("VOICE_CHUNKER_V2", False)
    legacy = _collect(tts, pieces)
    set_flag("VOICE_CHUNKER_V2", True)
    v2 = _collect(tts, pieces)
    assert legacy[0] == "Dr."
    assert v2[0] == "Dr. Smith is here today."
    assert " ".join(v2).split() == " ".join(pieces).split()


def test_flag_on_preserves_all_text_and_order():
    pieces = ["Open notepad now,", "then type a very long sentence that keeps going", "and going. Done!", "Bye."]
    set_flag("VOICE_CHUNKER_V2", True)
    out = _collect(_bare_tts(), pieces)
    assert " ".join(out).split() == " ".join(pieces).split()


def test_flag_is_read_per_call():
    pieces = ["Dr.", "Smith", "is", "here", "today.", "Please", "come", "in."]
    tts = _bare_tts()
    set_flag("VOICE_CHUNKER_V2", True)
    first = _collect(tts, pieces)
    set_flag("VOICE_CHUNKER_V2", False)
    second = _collect(tts, pieces)
    assert first != second


def test_v2_exception_falls_back_to_legacy(monkeypatch, capsys):
    pieces = ["Dr.", "Smith", "is", "here", "today.", "Please", "come", "in."]
    tts = _bare_tts()
    set_flag("VOICE_CHUNKER_V2", False)
    legacy = _collect(tts, pieces)

    def boom(buf, final=False):
        raise ValueError("secret spoken text")

    monkeypatch.setattr(engine, "first_chunk_end", boom)
    set_flag("VOICE_CHUNKER_V2", True)
    assert _collect(tts, pieces) == legacy
    out = capsys.readouterr().out
    assert out.count("chunker v2 failed") == 1
    assert "secret" not in out