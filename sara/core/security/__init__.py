"""
sara.core.security

Untrusted-content handling for SARA: detector, wrapping, redaction, taint.
See untrusted.py for the entry points used at ingestion points.
"""
from .detector import Detection, scan, security_mode
from .redact import redact
from .taint import mark_turn, turn_flagged, turn_sources, turn_tainted
from .untrusted import (
    UNTRUSTED_RULE,
    drop_flagged_hits,
    guard_spoken,
    note_untrusted,
    prepare_for_llm,
    security_rule,
    strip_hidden_elements,
    wrap_untrusted,
)

__all__ = [
    "Detection", "scan", "security_mode", "redact",
    "mark_turn", "turn_flagged", "turn_sources", "turn_tainted",
    "UNTRUSTED_RULE", "drop_flagged_hits", "guard_spoken", "note_untrusted",
    "prepare_for_llm", "security_rule", "strip_hidden_elements", "wrap_untrusted",
]
