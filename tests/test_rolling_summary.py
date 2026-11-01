"""Part 3 / P8: dropped turns are summarised in batches; the summary line is stable between updates and bounded."""
import threading
from collections import deque

from sara.core.llm import engine as E
from sara.core.rolling_summary import RollingSummary


def test_batches_and_bounds():
    calls = []

    def fn(prev, batch):
        calls.append((prev, list(batch)))
        return "x" * 1000 if len(calls) == 2 else f"summary {len(calls)}"

    r = RollingSummary(fn, every=3, max_chars=100, run_async=False)
    for i in range(2):
        r.note_dropped(f"q{i}", f"a{i}")
    assert r.line == "" and not calls                      # not enough dropped turns yet
    r.note_dropped("q2", "a2")
    assert r.line == "summary 1" and [b[0] for b in calls[0][1]] == ["q0", "q1", "q2"]
    for i in range(3, 6):
        r.note_dropped(f"q{i}", f"a{i}")
    assert calls[1][0] == "summary 1"                      # the old summary is folded in, not replaced blindly
    assert len(r.line) == 100                              # hard length cap: the prompt cannot grow


def test_failure_keeps_old_line_and_retries_the_batch():
    state = {"fail": False}

    def fn(prev, batch):
        if state["fail"]:
            raise RuntimeError("ollama down")
        return "ok " + str(len(batch))

    r = RollingSummary(fn, every=2, run_async=False)
    r.note_dropped("a", "b"); r.note_dropped("c", "d")
    assert r.line == "ok 2"
    state["fail"] = True
    r.note_dropped("e", "f"); r.note_dropped("g", "h")
    assert r.line == "ok 2"
    state["fail"] = False
    r.note_dropped("i", "j")
    assert r.line == "ok 3"                                # the failed pair was kept and retried


def test_async_runs_off_the_caller_thread():
    seen = {}
    done = threading.Event()

    def fn(prev, batch):
        seen["thread"] = threading.current_thread().name
        done.set()
        return "s"

    r = RollingSummary(fn, every=1)
    r.note_dropped("a", "b")
    assert done.wait(2) and seen["thread"] == "rolling-summary"


def _brain(enabled=True, maxlen=2):
    b = E.SaraLLM.__new__(E.SaraLLM)
    b._history = deque(maxlen=maxlen)
    b._history_lock = threading.Lock()
    b.system_instruction = "SYSTEM"
    b._rolling = RollingSummary(lambda p, bt: "user is Rahul, wants a call tomorrow", every=1, run_async=False) if enabled else None
    return b


def test_engine_feeds_dropped_turns_and_prompt_stays_flat():
    b = _brain()
    for i in range(6):
        b._append_history(f"question {i}", f"answer {i}")
    assert len(b._history) == 2 and b._rolling.updates == 4          # 6 appended, 2 kept, 4 dropped
    b._turn_language_note = lambda: ""
    m1 = b._build_messages_ollama("next", list(b._history))
    assert m1[0]["content"].startswith("SYSTEM") and "Rahul" in m1[0]["content"]
    b._append_history("question 7", "answer 7")
    m2 = b._build_messages_ollama("next", list(b._history))
    assert len(m2[0]["content"]) == len(m1[0]["content"])             # same summary text -> same length: no growth per turn


def test_disabled_changes_nothing():
    b = _brain(enabled=False)
    for i in range(5):
        b._append_history(f"q{i}", f"a{i}")
    b._turn_language_note = lambda: ""
    assert b._build_messages_ollama("x", list(b._history))[0]["content"] == "SYSTEM"


def test_dedup_pop_does_not_count_as_a_drop():
    b = _brain(maxlen=2)
    b._append_history("a", "1"); b._append_history("b", "2")
    b._append_history("b", "2 again")                                  # same prompt replaces the last entry
    assert b._rolling.updates == 0 and list(b._history)[-1] == ("b", "2 again")
