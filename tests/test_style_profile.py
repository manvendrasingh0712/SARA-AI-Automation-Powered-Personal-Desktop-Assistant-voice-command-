"""Audit part 2, O6: learned answer style, stable prompt prefix."""
from sara.core.llm import engine as E
from sara.core.style_profile import StyleProfile


class Store:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


def _sp():
    s = Store()
    return StyleProfile(s.get, s.set), s


def test_no_line_until_there_is_a_pattern():
    sp, _ = _sp()
    for t in ("what is the capital of france", "samjhao gravity", "kya hai ye"):
        sp.observe(t)
    assert sp.line() == ""


def test_repeated_brevity_requests_make_answers_shorter():
    sp, _ = _sp()
    for _ in range(3):
        sp.observe("short me batao ye kya hai")
    assert "very short" in sp.line()


def test_repeated_detail_requests_make_answers_fuller():
    sp, _ = _sp()
    for _ in range(3):
        sp.observe("detail me samjhao")
    assert "fuller" in sp.line()


def test_hinglish_writer_gets_a_hinglish_line_after_enough_turns():
    sp, _ = _sp()
    for _ in range(12):
        sp.observe("mujhe batao yaar kya hai ye")
    assert "Hinglish" in sp.line()


def test_prompt_is_rebuilt_only_when_the_line_changes():
    b = E.SaraLLM.__new__(E.SaraLLM)
    b._cfg = type("C", (), {"SARA_NAME": "Sara"})()
    b._tod, b._lang, b.user_name = "It is evening.", "hinglish", None
    b._build_and_cache_system_instruction()
    base = b.system_instruction
    assert b.set_style_line("This user prefers very short answers.") is True
    assert b.system_instruction == base + " This user prefers very short answers."
    assert b.set_style_line("This user prefers very short answers.") is False   # unchanged: no rebuild
    assert b.set_style_line("") is True and b.system_instruction == base
