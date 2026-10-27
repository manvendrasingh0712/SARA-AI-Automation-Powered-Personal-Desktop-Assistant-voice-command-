"""Scenario loader and validator for the memory benchmark (data: bench/memory/scenarios.jsonl)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH: Path = ROOT / "bench" / "memory" / "scenarios.jsonl"
QUESTION_TYPES = frozenset({
    "single", "contradiction", "contradiction_history", "temporal", "multihop", "abstain", "forget",
})
EXPECT_KEYS = ("any", "abstain", "none_of")


def to_epoch(value: str) -> float:
    """ISO timestamp -> epoch seconds (naive values are read as UTC)."""
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_stamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        to_epoch(value)
    except ValueError:
        return False
    return True


def _is_texts(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_is_text(item) for item in value)


def _check_turn(turn: Any, where: str) -> None:
    if not isinstance(turn, dict) or not _is_text(turn.get("text")) or not _is_stamp(turn.get("ts")):
        raise ValueError(f"{where}: invalid turn")
    facts, events = turn.get("facts", []), turn.get("events", [])
    if not isinstance(facts, list) or not isinstance(events, list):
        raise ValueError(f"{where}: facts/events must be lists")
    for fact in facts:
        if not (isinstance(fact, dict) and _is_text(fact.get("subject"))
                and _is_text(fact.get("predicate")) and fact.get("object") not in (None, "")):
            raise ValueError(f"{where}: invalid fact")
    for event in events:
        if not (isinstance(event, dict) and _is_text(event.get("summary")) and _is_stamp(event.get("ts"))):
            raise ValueError(f"{where}: invalid event")


def _check_question(question: Any, where: str, seen: Set[str]) -> None:
    if not isinstance(question, dict):
        raise ValueError(f"{where}: invalid question")
    qid = question.get("id")
    if not _is_text(qid) or qid in seen:
        raise ValueError(f"{where}: missing or duplicate question id")
    seen.add(qid)
    label = f"question {qid}"
    if not _is_text(question.get("q")):
        raise ValueError(f"{label}: missing q")
    if question.get("type") not in QUESTION_TYPES:
        raise ValueError(f"{label}: unknown type")
    if not _is_stamp(question.get("at")):
        raise ValueError(f"{label}: invalid 'at'")
    expect = question.get("expect")
    if not isinstance(expect, dict) or sum(key in expect for key in EXPECT_KEYS) != 1:
        raise ValueError(f"{label}: expect needs exactly one of any / abstain / none_of")
    if "abstain" in expect:
        if expect["abstain"] is not True:
            raise ValueError(f"{label}: abstain must be true")
    elif not _is_texts(expect.get("any", expect.get("none_of"))):
        raise ValueError(f"{label}: expect lists must be non-empty strings")
    forbid = question.get("forbid")
    if forbid is not None and not (isinstance(forbid, list) and all(_is_text(s) for s in forbid)):
        raise ValueError(f"{label}: invalid forbid")


def _check_forget(op: Any, where: str) -> None:
    after = op.get("after_turn") if isinstance(op, dict) else None
    if not (isinstance(after, int) and not isinstance(after, bool) and _is_text(op.get("phrase"))):
        raise ValueError(f"{where}: invalid forget op")
    forbid = op.get("forbid", [])
    if not (isinstance(forbid, list) and all(_is_text(s) for s in forbid)):
        raise ValueError(f"{where}: invalid forget forbid")


def _check_scenario(row: Any, line: int, seen: Set[str], seen_q: Set[str]) -> None:
    if not isinstance(row, dict):
        raise ValueError(f"line {line}: not an object")
    sid = row.get("id")
    if not _is_text(sid) or sid in seen:
        raise ValueError(f"line {line}: missing or duplicate scenario id")
    seen.add(sid)
    where = f"scenario {sid}"
    if not _is_text(row.get("lang")):
        raise ValueError(f"{where}: missing lang")
    turns, questions = row.get("turns"), row.get("questions")
    if not isinstance(turns, list) or not isinstance(questions, list) or not questions:
        raise ValueError(f"{where}: turns and questions must be lists (questions non-empty)")
    for index, turn in enumerate(turns, start=1):
        _check_turn(turn, f"{where} turn {index}")
    for question in questions:
        _check_question(question, where, seen_q)
    forgets = row.get("forget", [])
    if not isinstance(forgets, list):
        raise ValueError(f"{where}: forget must be a list")
    for op in forgets:
        _check_forget(op, where)


def load_scenarios(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read and validate the scenario file; raise ValueError (ids and line numbers only) when invalid."""
    target = path or DEFAULT_PATH
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        raise ValueError(f"cannot read {target.name}") from None
    scenarios: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    seen_q: Set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise ValueError(f"line {number}: invalid JSON") from None
        _check_scenario(row, number, seen, seen_q)
        scenarios.append(row)
    if not scenarios:
        raise ValueError(f"{target.name}: no scenarios")
    return scenarios