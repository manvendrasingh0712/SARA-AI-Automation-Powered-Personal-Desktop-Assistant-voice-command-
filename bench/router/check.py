"""
bench.router.check
Deterministic answer checkers for the router benchmark. Pure functions: no I/O, no network.

check_answer(text, check) returns True when the reply satisfies the check. Malformed input or an
unknown kind returns False and never raises. Reply text is never logged.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable, Mapping

KNOWN_KINDS = frozenset({
    "number", "contains_any", "equals", "json_fields", "one_word",
    "all_lower", "bullets", "max_words", "starts_with", "all",
})

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_BULLET_PREFIXES = ("- ", "* ", "\u2022")
_WRAP_CHARS = "\"'`"
_TRAIL_CHARS = ".,;:!?"
_TOL = 1e-9


def _as_number(value: Any) -> float | None:
    """Finite float from an int, float or numeric string; None for bools and anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _as_count(value: Any) -> int | None:
    """Non-negative int (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _edges(text: str) -> str:
    """Strip whitespace, wrapping quotes/backticks and trailing punctuation from both ends."""
    previous = None
    current = text.strip()
    while current != previous:
        previous = current
        current = current.strip()
        if len(current) >= 2 and current[0] in _WRAP_CHARS and current[-1] in _WRAP_CHARS:
            current = current[1:-1]
        current = current.rstrip(_TRAIL_CHARS)
    return current.strip()


def _norm(text: str) -> str:
    """Normalised form for equality: edges dropped, lower-cased, spaces collapsed."""
    return " ".join(_edges(text).lower().split())


def _parse_first_object(text: str) -> dict | None:
    """First complete JSON object in the text (code fences tolerated), or None."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _same(actual: Any, expected: Any) -> bool:
    """Field comparison: numbers numerically, strings case-insensitively, others exactly."""
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        got = _as_number(actual)
        return got is not None and abs(got - float(expected)) <= _TOL
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.strip().casefold() == expected.strip().casefold()
    return actual == expected


def _check_number(text: str, check: Mapping[str, Any]) -> bool:
    value = _as_number(check.get("value"))
    tol = _as_number(check.get("tol", 0))
    if value is None or tol is None or tol < 0:
        return False
    match = _NUMBER_RE.search(text.replace(",", ""))
    return match is not None and abs(float(match.group()) - value) <= tol


def _check_contains_any(text: str, check: Mapping[str, Any]) -> bool:
    values = check.get("values")
    if not isinstance(values, list):
        return False
    low = text.casefold()
    return any(v.casefold() in low for v in values if isinstance(v, str) and v)


def _check_equals(text: str, check: Mapping[str, Any]) -> bool:
    value = check.get("value")
    return isinstance(value, str) and _norm(text) == _norm(value)


def _check_json_fields(text: str, check: Mapping[str, Any]) -> bool:
    fields = check.get("fields")
    if not isinstance(fields, dict) or not fields:
        return False
    obj = _parse_first_object(text)
    if obj is None:
        return False
    return all(key in obj and _same(obj[key], expected) for key, expected in fields.items())


def _check_one_word(text: str, check: Mapping[str, Any]) -> bool:
    word = _edges(text)
    return bool(word) and len(word.split()) == 1


def _check_all_lower(text: str, check: Mapping[str, Any]) -> bool:
    return any(c.isalpha() for c in text) and not any(c.isupper() for c in text)


def _check_bullets(text: str, check: Mapping[str, Any]) -> bool:
    n = _as_count(check.get("n"))
    if n is None:
        return False
    count = sum(1 for line in text.splitlines() if line.lstrip().startswith(_BULLET_PREFIXES))
    return count == n


def _check_max_words(text: str, check: Mapping[str, Any]) -> bool:
    n = _as_count(check.get("n"))
    return n is not None and len(text.split()) <= n


def _check_starts_with(text: str, check: Mapping[str, Any]) -> bool:
    value = check.get("value")
    if not isinstance(value, str) or not value.strip():
        return False
    return text.strip().casefold().startswith(value.strip().casefold())


def _check_all(text: str, check: Mapping[str, Any]) -> bool:
    checks = check.get("checks")
    if not isinstance(checks, list) or not checks:
        return False
    return all(check_answer(text, sub) for sub in checks)


_DISPATCH: dict[str, Callable[[str, Mapping[str, Any]], bool]] = {
    "number": _check_number,
    "contains_any": _check_contains_any,
    "equals": _check_equals,
    "json_fields": _check_json_fields,
    "one_word": _check_one_word,
    "all_lower": _check_all_lower,
    "bullets": _check_bullets,
    "max_words": _check_max_words,
    "starts_with": _check_starts_with,
    "all": _check_all,
}


def check_answer(text: str, check: Mapping[str, Any]) -> bool:
    """True when the reply text satisfies the check. Never raises."""
    if not isinstance(text, str) or not isinstance(check, Mapping):
        return False
    handler = _DISPATCH.get(check.get("kind"))
    if handler is None:
        return False
    try:
        return bool(handler(text, check))
    except (TypeError, ValueError, AttributeError):
        return False


def is_valid_check(check: Any) -> bool:
    """True when the check, including nested `all` checks, uses only known kinds."""
    if not isinstance(check, Mapping) or check.get("kind") not in KNOWN_KINDS:
        return False
    if check.get("kind") == "all":
        subs = check.get("checks")
        return isinstance(subs, list) and bool(subs) and all(is_valid_check(s) for s in subs)
    return True