"""Audit part 2, D4: no intent regex may take long on adversarial input, and very long input skips the regex layer."""
import re
import time

import sara.skills  # noqa: F401
from sara.core.intent import engine as E
from sara.core.intent.engine import detect_intent

ADVERSARIAL = ["open " + "a" * 295, "play " + "the " * 95, "x " * 150, "set a timer for " + "1 " * 140,
               "remind me to " + "w " * 130 + "at 5", "a" * 300, "kholo " * 50, "ab " * 100 + "band karo"]


def test_every_pattern_finishes_fast_on_300_char_adversarial_input():
    slow = []
    for name, patterns in E._INTENT_PATTERNS:
        for p in patterns:
            rx = re.compile(p, re.IGNORECASE | re.UNICODE)
            for text in ADVERSARIAL:
                t = time.perf_counter()
                rx.search(text)
                ms = (time.perf_counter() - t) * 1000
                if ms > 20:
                    slow.append((round(ms), name, p[:50], text[:12]))
    assert not slow, slow


def test_long_input_goes_straight_to_chat_in_a_few_ms():
    for text in ("open " + "a" * 3000 + " band karo", "play " + "the " * 400, "x " * 2000):
        detect_intent.cache_clear()
        t = time.perf_counter()
        assert detect_intent(text)[0] == "chat"
        assert (time.perf_counter() - t) * 1000 < 20


def test_stop_words_still_work_when_the_text_is_long():
    assert E._LONG_TEXT_ALLOWED_RE.match("stop " + "blah " * 100)
    assert not E._LONG_TEXT_ALLOWED_RE.match("open chrome " + "blah " * 100)


def test_normal_commands_unchanged():
    assert detect_intent("open chrome")[0] == "open_app"
    assert detect_intent("volume 50 kar do")[0] == "set_volume"
