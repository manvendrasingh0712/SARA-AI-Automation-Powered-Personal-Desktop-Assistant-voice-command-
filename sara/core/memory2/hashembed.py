"""Deterministic offline text embedding (hashed word unigrams + character 3-grams)."""
from __future__ import annotations

import hashlib
import unicodedata

import numpy as np


def _tokens(text: str) -> list[str]:
    """Split on anything that is not a letter, digit or combining mark (keeps Devanagari intact)."""
    words: list[str] = []
    current: list[str] = []
    for ch in unicodedata.normalize("NFKC", text).lower():
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            current.append(ch)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def _add(vec: np.ndarray, token: str, weight: float) -> None:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    number = int.from_bytes(digest, "big")
    sign = -1.0 if (number >> 63) & 1 else 1.0
    vec[number % vec.size] += sign * weight


def hash_embed(text: str, dim: int = 512) -> np.ndarray | None:
    """L2-normalised float32 vector for ``text``; None for empty text."""
    if not isinstance(text, str) or dim <= 0:
        return None
    words = _tokens(text)
    if not words:
        return None
    vec = np.zeros(dim, dtype=np.float32)
    for word in words:
        _add(vec, "w:" + word, 1.0)
    padded = " " + " ".join(words) + " "
    for i in range(len(padded) - 2):
        _add(vec, "c:" + padded[i : i + 3], 0.5)
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return None
    return vec / norm