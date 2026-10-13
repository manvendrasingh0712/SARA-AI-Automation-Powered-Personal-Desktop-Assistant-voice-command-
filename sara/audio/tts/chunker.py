"""
sara.audio.tts.chunker
Pure helpers for the streaming TTS pipeline: the stricter first-chunk rule
(first_chunk_end) and a bounded recorder of the incoming reply text.
"""
from __future__ import annotations

import unicodedata
from typing import Iterable, Iterator

MIN_FIRST_CHARS = 8  # mirrors engine._MIN_CHUNK: never flush a tiny fragment
CLAUSE_MIN_CHARS = 25
WORD_MIN_CHARS = 60
MAX_RECORD_CHARS = 20000

_TERMINATORS = frozenset(".?!\u0964\u0965\u2026")
_STRONG_TERMINATORS = frozenset("?!\u0964\u0965\u2026")
_CLAUSE = frozenset(",;:\u2014\u2013")
_CLOSERS = frozenset("\"'\u201d\u2019)]}\u00bb")
_OPENERS = "([{\"'\u201c\u2018"
_VIRAMA = "\u094d"
_JOINERS = frozenset("\u200c\u200d")
_ABBREVIATIONS = frozenset(
    {
        "dr", "mr", "mrs", "ms", "st", "vs", "etc", "e.g", "i.e", "a.m",
        "p.m", "prof", "sr", "jr",
    }
)


def _joins_previous(ch: str) -> bool:
    """True for combining marks / joiners that must stay with the previous char."""
    return ch in _JOINERS or unicodedata.category(ch).startswith("M")


def _safe_cut(buffer: str, idx: int) -> bool:
    """False when cutting at idx would split a Devanagari conjunct or cluster."""
    if idx <= 0 or idx > len(buffer):
        return False
    prev = buffer[idx - 1]
    if prev == _VIRAMA or prev in _JOINERS:
        return False
    return idx == len(buffer) or not _joins_previous(buffer[idx])


def _token_start(buffer: str, end: int) -> int:
    start = end
    while start > 0 and not buffer[start - 1].isspace():
        start -= 1
    return start


def _is_abbreviation(token: str) -> bool:
    bare = token.lstrip(_OPENERS)
    if bare.lower() in _ABBREVIATIONS:
        return True
    return len(bare) == 1 and bare.isascii() and bare.isalpha() and bare.isupper()


def _is_list_marker(buffer: str, tok_start: int, token: str) -> bool:
    if not token.isdigit():
        return False
    before = buffer[:tok_start].rstrip(" \t")
    return before == "" or before.endswith("\n")


def _sentence_cut(buffer: str, i: int, final: bool) -> tuple[int | None, int]:
    """Examine a terminator run starting at i. Returns (cut or None, next index)."""
    n = len(buffer)
    k = i
    while k < n and buffer[k] in _TERMINATORS:
        k += 1
    run = buffer[i:k]
    while k < n and buffer[k] in _CLOSERS:
        k += 1
    if k < n and not buffer[k].isspace():
        return None, k
    if k == n and not final and run[-1] not in _STRONG_TERMINATORS:
        return None, k
    if run == ".":
        start = _token_start(buffer, i)
        token = buffer[start:i]
        if _is_abbreviation(token) or _is_list_marker(buffer, start, token):
            return None, k
    return (k if _safe_cut(buffer, k) else None), k


def first_chunk_end(buffer: str, final: bool = False) -> int | None:
    """Index at which the first TTS chunk should be cut, or None to keep buffering.

    Earliest of: a sentence end (. ? ! ।) that leaves >= 8 characters; a clause
    boundary (, ; : — –) after >= 25 characters; a word boundary after >= 60
    characters. Never cuts inside numbers, abbreviations, URLs, e-mail
    addresses or Devanagari conjuncts. final=True means the stream is over:
    when no rule fires, the whole non-empty buffer is returned.
    """
    n = len(buffer)
    if not buffer.strip():
        return None
    start = n - len(buffer.lstrip())
    i = start
    while i < n:
        ch = buffer[i]
        if ch in _TERMINATORS:
            cut, nxt = _sentence_cut(buffer, i, final)
            if cut is not None and (final or len(buffer[start:cut].strip()) >= MIN_FIRST_CHARS):
                return cut
            i = max(nxt, i + 1)
            continue
        if ch in _CLAUSE:
            if (
                i + 1 < n
                and buffer[i + 1].isspace()
                and i + 1 - start >= CLAUSE_MIN_CHARS
                and _safe_cut(buffer, i + 1)
            ):
                return i + 1
        elif ch.isspace() and i - start >= WORD_MIN_CHARS and not buffer[i - 1].isspace():
            tok_start = _token_start(buffer, i)
            token = buffer[tok_start:i]
            guarded = token.endswith(".") and _is_abbreviation(token[:-1])
            if not guarded and _safe_cut(buffer, i):
                return i
        i += 1
    return n if final else None


class ReplyRecorder:
    """Iterator wrapper that remembers the chunks it hands out (bounded)."""

    def __init__(self, source: Iterable[str], max_chars: int = MAX_RECORD_CHARS) -> None:
        self.error = False
        self.overflow = False
        self._seen: list[str] = []
        self._chars = 0
        self._max = max_chars
        try:
            self._it: Iterator[str] = iter(source)
        except Exception:
            self._it = iter(())
            self.error = True

    def __iter__(self) -> Iterator[str]:
        while True:
            try:
                raw = next(self._it)
            except StopIteration:
                return
            except Exception:
                self.error = True
                raise
            self._remember(raw)
            yield raw

    def _remember(self, raw: str) -> None:
        if self.overflow:
            return
        if self._chars + len(raw) > self._max:
            self.overflow = True
            return
        self._seen.append(raw)
        self._chars += len(raw)

    def whole_reply(self, drain: bool) -> str:
        """Everything received so far; drain=True also consumes the rest of the source."""
        if drain and not self.error:
            try:
                for raw in self._it:
                    self._remember(raw)
            except Exception:
                self.error = True
        return " ".join(part for part in self._seen if part).strip()