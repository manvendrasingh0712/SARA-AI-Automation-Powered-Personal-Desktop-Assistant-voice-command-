"""
sara.core.security.detector

Heuristic prompt-injection detector for UNTRUSTED text (web pages, files,
notes, clipboard, calendar titles, search results, OCR/vision output).

Offline, dependency-free, deterministic. Each rule has a weight; matched
rules are combined with a noisy-OR so several weak signals can add up to a
flag while one innocent phrase cannot:

    score = 1 - prod(1 - weight_i)      flagged = score >= threshold

Covers English, romanized Hinglish and Devanagari Hindi, plus
character-level tricks (zero-width characters, bidi controls, Unicode "tag"
characters used to smuggle hidden ASCII).

Only rule IDs are ever reported, never the matched text, so a detection
result is safe to log.

Config keys (see config.py): SECURITY_MODE, SECURITY_INJECTION_THRESHOLD,
SECURITY_SCAN_MAX_CHARS. SECURITY_LLM_JUDGE is a documented hook for a
future LLM judge and is NOT used here.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_VALID_MODES = ("off", "standard", "strict")


# ── config access ───────────────────────────────────────────────────────────

def _default_config() -> Any:
    try:
        from config import Config
    except ImportError:
        return None
    return Config


def get_setting(name: str, default: Any, cfg: Any = None) -> Any:
    """Read a setting from `cfg` (any object with attributes) or Config."""
    source = cfg if cfg is not None else _default_config()
    if source is None:
        return default
    return getattr(source, name, default)


def security_mode(cfg: Any = None) -> str:
    """'off' | 'standard' | 'strict'. Unknown values fall back to standard."""
    mode = str(get_setting("SECURITY_MODE", "standard", cfg) or "standard")
    mode = mode.strip().lower()
    return mode if mode in _VALID_MODES else "standard"


# ── character-level helpers ─────────────────────────────────────────────────

# Everything we strip before matching.
_STRIP_CHARS = frozenset(
    "\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e"          # zero-width / soft hyphen
    "\u200e\u200f\u202a\u202b\u202c\u202d\u202e"          # bidi marks / overrides
    "\u2066\u2067\u2068\u2069"                            # bidi isolates
)
# Characters that count as "hidden" evidence. ZWJ/ZWNJ (200c/200d) are
# legitimate inside Devanagari conjuncts, so they are stripped for matching
# but never counted.
HIDDEN_CHARS = _STRIP_CHARS - {"\u200c", "\u200d"}

_NUKTA = "\u093c"  # stripped so "नज़र" and "नजर" match the same pattern


def _is_tag_char(ch: str) -> bool:
    return 0xE0000 <= ord(ch) <= 0xE007F


def _decode_tag_chars(text: str) -> str:
    """Unicode 'tag' characters (U+E0020..E007E) mirror ASCII; attackers use
    them to hide instructions that render as nothing."""
    return "".join(
        chr(ord(c) - 0xE0000) for c in text if 0xE0020 <= ord(c) <= 0xE007E
    )


def normalize_for_scan(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = "".join(
        ch for ch in t
        if ch not in _STRIP_CHARS and ch != _NUKTA and not _is_tag_char(ch)
    )
    t = t.casefold()
    return re.sub(r"[ \t\r\f\v\u00a0]+", " ", t)


# ── rules ───────────────────────────────────────────────────────────────────
# (rule_id, weight, regex). Matching runs on normalize_for_scan() output
# (NFKC, casefolded, invisible characters removed, nukta removed).
# Latin patterns use \b; Devanagari patterns do not (combining marks break
# \b in Python's re).

_RULE_SPECS = (
    # ── English ──
    ("override_en", 0.70,
     r"\b(?:ignore|disregard|forget|override|bypass|discard)\b[^.\n]{0,25}?"
     r"\b(?:previous|prior|above|earlier|preceding|all|any|your|system|original)\b"
     r"[^.\n]{0,20}?\b(?:instructions?|rules?|prompts?|guidelines?|directions?|"
     r"commands?|programming)\b"),
    ("override_en_after", 0.70,
     r"\b(?:ignore|disregard|forget|override)\s+(?:the\s+)?"
     r"(?:instructions?|rules?|prompts?)\s+(?:above|before|you were given|"
     r"you've been given|you have been given)\b"),
    ("forget_everything", 0.65,
     r"\b(?:forget|ignore|disregard)\s+(?:everything|all)\b[^.\n]{0,25}?"
     r"\b(?:above|before|told|said|learned|instructed)\b"),
    ("new_instructions", 0.40,
     r"\b(?:new|updated|revised|real|actual|hidden|secret)\s+"
     r"(?:instructions?|system prompt|directives?)\b"),
    ("role_hijack", 0.45,
     r"\b(?:you are now|you're now|from now on,? you|your new (?:role|task|"
     r"purpose|job)|pretend (?:to be|you are)|act as if you|developer mode|"
     r"jailbreak|dan mode)\b"),
    ("fake_role_tag", 0.60,
     r"(?:<\s*/?\s*(?:system|assistant|instructions?|prompt)\s*>|"
     r"\[\s*/?\s*(?:system|inst|assistant)\s*\]|"
     r"<\|(?:im_start|im_end|system|endoftext)\|>|<<\s*/?\s*sys\s*>>)"),
    ("role_prefix", 0.35,
     r"^\s*(?:system|assistant)\s*:"),
    ("address_ai", 0.50,
     r"\b(?:attention|note to|message for|instructions? for|important "
     r"(?:message|note|instruction)s? (?:for|to)|hey|dear)\s+(?:the\s+)?"
     r"(?:ai|a\.i\.|assistant|llm|language model|chatbot|sara|model)\b"),
    ("exfil", 0.50,
     r"\b(?:send|email|e-mail|mail|forward|upload|post|leak|exfiltrate|"
     r"transmit|share)\b[^.\n]{0,60}?\b(?:passwords?|api[ _-]?keys?|tokens?|"
     r"secrets?|credentials?|clipboard|private (?:data|files?|keys?)|"
     r"conversation|chat history|system prompt)\b"),
    ("command_to_ai", 0.45,
     r"\b(?:sara|assistant|ai|the model|the assistant)\b[^.\n]{0,20}?"
     r"\b(?:must|should|have to|need to|are required to|shall)\b[^.\n]{0,40}?"
     r"\b(?:open|run|execute|delete|erase|format|send|email|download|install|"
     r"visit|navigate|click|type|reveal|disable|ignore)\b"),
    ("shell_commands", 0.50,
     r"\b(?:powershell(?:\.exe)?\s+-|cmd(?:\.exe)?\s+/c|rm\s+-rf|del\s+/[fsq]|"
     r"curl\s+[^|\n]{0,80}\|\s*(?:sh|bash)|invoke-webrequest|iex\s*\(|"
     r"format\s+c:)"),
    ("conceal", 0.55,
     r"\b(?:do not|don't|dont|never|without)\s+(?:tell(?:ing)?|inform(?:ing)?|"
     r"mention(?:ing)?|reveal(?:ing)?|alert(?:ing)?|notify(?:ing)?|"
     r"ask(?:ing)?)\b[^.\n]{0,30}?\b(?:the )?(?:user|human|owner|anyone)\b"),
    ("reveal_prompt", 0.50,
     r"\b(?:reveal|print|show|repeat|output|display|leak|tell me)\b[^.\n]{0,25}?"
     r"\b(?:your|the)\s+(?:system prompt|instructions|initial prompt|"
     r"hidden prompt|rules)\b"),

    # ── romanized Hinglish ──
    ("override_hinglish", 0.70,
     r"\b(?:pichle|pichli|purane|puraane|pehle\s+(?:ke|wale|wali)|"
     r"upar\s+(?:ke|wale|wali)|sabhi|saare|sare|sab)\b[^.\n]{0,30}?"
     r"\b(?:instructions?|nirdesh|niyam|rules?|commands?|aadesh|adesh)\b"
     r"[^.\n]{0,25}?\b(?:ignore|bhool|bhul|bhoolo|bhulo|nazarandaz|"
     r"nazar\s*andaz|mat\s*maan|na\s*maan|hata|hatao|cancel)"),
    ("override_hinglish_rev", 0.70,
     r"\b(?:ignore|bhool|bhul|bhoolo|bhulo|nazarandaz|nazar\s*andaz)\b"
     r"[^.\n]{0,30}?\b(?:pichle|pichli|purane|puraane|sabhi|saare|sare|sab|"
     r"upar\s+(?:ke|wale|wali)|apne|apni)\b[^.\n]{0,30}?"
     r"\b(?:instructions?|nirdesh|niyam|rules?|commands?|aadesh|adesh)\b"),
    ("role_hijack_hinglish", 0.40,
     r"\b(?:ab\s+se\s+(?:tum|tu|aap)|tum\s+ab|aap\s+ab|ab\s+tum)\b"
     r"[^.\n]{0,25}?\b(?:ho|hain|ban|bano|banoge|ban\s*jao|ban\s*ja)\b"),
    ("conceal_hinglish", 0.55,
     r"\b(?:user|yuser|malik)\s+ko\s+(?:mat|na|nahi|nahin)\s+"
     r"(?:batana|batao|bata|bolna|dikhana)\b"),
    ("exfil_hinglish", 0.50,
     r"\b(?:password|passwd|api\s*key|token|secret|clipboard)\b[^.\n]{0,40}?"
     r"\b(?:bhej|bhejo|bhejna|send|mail|email|upload|share)\b"),
    ("exfil_hinglish_rev", 0.50,
     r"\b(?:bhej|bhejo|bhejna|mail|email|upload)\b[^.\n]{0,40}?"
     r"\b(?:password|passwd|api\s*key|token|secret)\b"),

    # ── Devanagari Hindi (no \b; nukta already stripped) ──
    ("override_hindi", 0.70,
     r"(?:पिछले|पिछली|पुराने|पुरानी|ऊपर\s+के|ऊपर\s+दिए|सभी|सारे|सारी)"
     r"[^.\n।]{0,25}?(?:निर्देश|आदेश|नियम|इंस्ट्रक्शन)"
     r"[^.\n।]{0,25}?(?:अनदेखा|नजरअंदाज|भूल|इग्नोर|हटा|रद्द|न\s+मान|मत\s+मान)"),
    ("override_hindi_rev", 0.70,
     r"(?:अनदेखा|नजरअंदाज|इग्नोर|भूल\s+जा)[^.\n।]{0,25}?"
     r"(?:पिछले|पिछली|पुराने|सभी|सारे|सारी|अपने)[^.\n।]{0,25}?"
     r"(?:निर्देश|आदेश|नियम|इंस्ट्रक्शन)"),
    ("role_hijack_hindi", 0.40,
     r"अब\s+से\s+(?:तुम|आप|तू)"),
    ("conceal_hindi", 0.55,
     r"(?:यूजर|उपयोगकर्ता)\s*को\s*(?:मत|न|नहीं)\s*(?:बता|बोल|दिखा)"),
    ("exfil_hindi", 0.50,
     r"(?:पासवर्ड|टोकन|एपीआई\s*की|एपीआई\s*कुंजी|गोपनीय)[^.\n।]{0,40}?"
     r"(?:भेज|मेल|अपलोड|शेयर)"),
)

_RULES = tuple(
    (rule_id, weight, re.compile(pattern, re.M))
    for rule_id, weight, pattern in _RULE_SPECS
)

_HIDDEN_CHARS_MIN = 3        # zero-width/bidi characters before it counts
_SMUGGLED_MIN_CHARS = 8      # decoded tag-chars; real emoji flags use <= 6


# ── result type ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Detection:
    score: float
    flagged: bool
    reasons: Tuple[str, ...] = ()
    hidden_chars: int = 0
    scanned_chars: int = 0


_CLEAN = Detection(0.0, False, (), 0, 0)


# ── public API ──────────────────────────────────────────────────────────────

def scan(
    text: Optional[str],
    *,
    threshold: Optional[float] = None,
    max_chars: Optional[int] = None,
    cfg: Any = None,
) -> Detection:
    """Score `text` for prompt-injection content.

    Texts longer than SECURITY_SCAN_MAX_CHARS are scanned head + tail
    (injections like to sit at the very end of a page).
    """
    if text is None:
        return _CLEAN
    if not isinstance(text, str):
        text = str(text)
    if not text.strip():
        return _CLEAN

    if threshold is None:
        threshold = float(get_setting("SECURITY_INJECTION_THRESHOLD", 0.6, cfg))
    if max_chars is None:
        max_chars = int(get_setting("SECURITY_SCAN_MAX_CHARS", 51200, cfg))

    if max_chars > 0 and len(text) > max_chars:
        half = max_chars // 2
        text = text[:half] + "\n" + text[-half:]

    hidden = sum(1 for ch in text if ch in HIDDEN_CHARS)
    smuggled = _decode_tag_chars(text)
    haystack = normalize_for_scan(text)
    if smuggled:
        haystack += "\n" + normalize_for_scan(smuggled)

    weights = {}
    for rule_id, weight, pattern in _RULES:
        if pattern.search(haystack):
            weights[rule_id] = weight
    if len(smuggled) >= _SMUGGLED_MIN_CHARS:
        weights["unicode_tags"] = 0.70
    if hidden >= _HIDDEN_CHARS_MIN:
        weights["hidden_chars"] = 0.30

    if not weights:
        return Detection(0.0, False, (), hidden, len(text))

    survive = 1.0
    for weight in weights.values():
        survive *= 1.0 - weight
    score = round(1.0 - survive, 3)
    reasons = tuple(sorted(weights, key=lambda r: (-weights[r], r)))
    return Detection(score, score >= threshold, reasons, hidden, len(text))
