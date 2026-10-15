"""
sara.audio.stt.barge_decode
Bounded, single-flight decode of the newest processed-ring audio with the
small preview model, used to classify barge-in speech.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("sara.core_logic")

DECODE_TIMEOUT_S = 0.4
_MIN_MS = 100
_MAX_MS = 2000
_BUSY = threading.Lock()


def decode_recent(stt: Any, max_ms: int = 700) -> Optional[str]:
    """Transcribe the newest `max_ms` of `stt._ring`. None on failure,
    no model, a decode already in flight, or timeout. Never raises."""
    try:
        model = getattr(stt, "_wake_whisper_model", None)
        if model is None or getattr(stt, "_closed", False):
            return None
        ms = max(_MIN_MS, min(_MAX_MS, int(max_ms)))
        rate = float(getattr(stt, "SAMPLE_RATE", 16000))
        size = float(getattr(stt, "CHUNK_SIZE", 512))
        n = max(1, int(rate / size * ms / 1000))
        chunks = stt._ring.peek_latest(n=n)
        if not chunks:
            return None
        snapshot = b"".join(chunks)
    except Exception as exc:
        logger.warning("[BargeDecode] snapshot failed: %s", type(exc).__name__)
        return None

    if not _BUSY.acquire(blocking=False):
        return None
    done = threading.Event()
    box: list[str] = []

    def _run() -> None:
        try:
            out = stt._transcribe(
                snapshot, beam_size_override=1, model_override=model
            )
            box.append(str(out).strip() if out else "")
        except Exception:
            pass
        finally:
            done.set()
            _BUSY.release()

    try:
        threading.Thread(target=_run, daemon=True, name="stt-barge-decode").start()
    except Exception:
        _BUSY.release()
        return None
    if not done.wait(DECODE_TIMEOUT_S):
        logger.info("[BargeDecode] timeout after %d ms window", ms)
        return None
    return (box[0] or None) if box else None