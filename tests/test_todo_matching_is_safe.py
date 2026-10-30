"""
Completing or deleting a to-do must never act on the wrong item.

E2E audit 2026-10-30: "delete the todo: call the bank" deleted "call about
invoice ca958edf" (similarity ratio 0.45 was enough), and "mark buy
groceries as done" style requests could hit lookalikes.
"""
import pytest

from sara.tools.system import files_notes as fn


def _todos(*texts):
    return [{"id": f"id{i}", "text": t, "done": False} for i, t in enumerate(texts)]


LIST = _todos("call about invoice ca958edf", "buy milk and eggs", "finish the physics assignment",
              "pack bag for trip", "water the plants")


@pytest.mark.parametrize("said, expected", [
    ("buy milk", "buy milk and eggs"),
    ("buy milk and eggs", "buy milk and eggs"),
    ("the physics assignment", "finish the physics assignment"),
    ("physics assignment", "finish the physics assignment"),
    ("water plants", "water the plants"),
    ("buy mlik and eggs", "buy milk and eggs"),          # typo tolerated
    ("invoice", "call about invoice ca958edf"),
    ("id1", "buy milk and eggs"),                         # by id
])
def test_matches_what_was_said(said, expected):
    assert fn._resolve_todo_target(said, LIST)["text"] == expected


@pytest.mark.parametrize("said", [
    "call the bank",            # shares only the word "call"
    "buy groceries",            # shares only "buy"
    "pack the car",
    "book a flight",
    "zzz_completely_unrelated_gibberish",
])
def test_lookalikes_are_refused(said):
    assert fn._resolve_todo_target(said, LIST) is None


def test_ties_ask_instead_of_guessing():
    twins = _todos("call mom tonight", "call dad tonight")
    assert fn._resolve_todo_target("call tonight", twins) is None
    assert fn._resolve_todo_target("call mom", twins)["text"] == "call mom tonight"


def test_delete_and_complete_report_instead_of_acting(monkeypatch):
    store = [dict(t) for t in LIST]
    monkeypatch.setattr(fn, "_read_todos", lambda: store)
    monkeypatch.setattr(fn, "_write_todos", lambda todos: store.__setitem__(slice(None), todos) or True)
    assert "couldn't find" in fn.delete_todo("call the bank")
    assert "couldn't find" in fn.complete_todo("book a flight")
    assert len(store) == 5 and not any(t["done"] for t in store)
    assert fn.delete_todo("buy milk") == "Deleted 'buy milk and eggs' from your to-do list."
    assert len(store) == 4


def test_a_long_list_is_capped_when_spoken(monkeypatch):
    items = _todos(*[f"task number {i}" for i in range(20)])
    monkeypatch.setattr(fn, "_read_todos", lambda: items)
    spoken = fn.list_todos()
    assert spoken.startswith("You have 20 to-dos: task number 0;")
    assert spoken.endswith(", and 14 more.") and "task number 6" not in spoken
