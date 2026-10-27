"""
sara.core.security.detector_rules
Second rule table for the injection detector: tool hijacks, hidden-markup
instructions, exfiltration URLs, fake transcripts / system headers, role plays
and more Hinglish / Hindi phrasings.

Each rule is (rule_id, weight, regex, needles). `needles` are plain substrings
checked first (C speed) so most rules cost nothing on ordinary text: a rule's
regex only runs when one of its needles occurs in the normalised text.
Matching runs on detector.normalize_for_scan() output (NFKC, casefolded,
invisible characters removed). Weights combine with the noisy-OR in scan();
a single rule below the threshold never flags on its own.
"""
from __future__ import annotations

import re
from typing import Tuple

_SNAKE = r"[a-z]+(?:_[a-z]+)+"

_SPECS = (
    # ── tool / action hijacks ──
    ("tool_cmd", 0.45,
     r"\b(?:call|run|execute|invoke|trigger|launch)\s+(?:the\s+)?(?:tool\s+)?" + _SNAKE + r"\b"
     r"|\b(?:use|using)\s+the\s+" + _SNAKE + r"\s+tool\b",
     ("_",)),
    ("skip_confirm", 0.40,
     r"\b(?:with(?:out)?\s+(?:any\s+)?confirm\w*|no\s+confirmation|(?:skip|skipping|ignore|ignoring|"
     r"disable|disabling|bypass)\s+(?:all\s+|any\s+|the\s+)?confirm\w*|do\s+not\s+ask|don't\s+ask|"
     r"without\s+asking|silently)\b",
     ("confirm", "ask", "silent")),
    ("false_auth", 0.45,
     r"\b(?:the\s+)?user\s+(?:has\s+)?(?:already\s+)?(?:agreed|approved|authori[sz]ed|consented|confirmed|said\s+yes)\b"
     r"|\b(?:this\s+is|it\s+is|that\s+is)\s+(?:required|mandatory|authori[sz]ed|approved)\s+by\s+(?:the\s+)?"
     r"(?:website|site|page|developer|system|admin\w*)\b"
     r"|\b(?:automated|scheduled)\s+maintenance\s+task\b",
     ("user ", "required by", "maintenance")),
    ("before_you_must", 0.50,
     r"\bbefore\s+(?:summari[sz]ing|reading|answering|continuing|responding|you\s+(?:answer|continue|summari[sz]e))"
     r"\b[^.\n]{0,40}\byou\s+(?:must|should|need\s+to|have\s+to)\b",
     ("before ",)),
    ("key_chord", 0.50,
     r"\b(?:alt\s*\+\s*f4|win\s*\+\s*r|ctrl\s*\+\s*alt\s*\+\s*del(?:ete)?)\b",
     ("+",)),
    ("destroy_memory", 0.40,
     r"\b(?:erase|wipe|delete|clear|forget)\s+(?:all|every|everything)\b[^.\n]{0,25}\b(?:stored|saved|user|your)?\s*"
     r"(?:facts|memories|memory|data|notes|reminders|files)\b",
     ("erase", "wipe", "delete", "clear", "forget")),
    ("type_secret", 0.45,
     r"\b(?:type|enter|input|paste|fill\s+in|provide)\b[^.\n]{0,40}\b(?:password|passcode|pin|otp|credentials?|"
     r"api[ _-]?key|token)\b",
     ("password", "passcode", "credential", "api key", "api_key", "token", " otp", " pin")),
    # ── talking to the AI / ignoring the user ──
    ("ignore_user", 0.55,
     r"\b(?:ignore|disregard|do\s+not\s+listen\s+to|don't\s+listen\s+to|override)\s+(?:the\s+)?(?:user|human|owner)"
     r"(?:'s|’s)?\b"
     r"|\b(?:ignore|disregard|forget)\s+what\s+(?:the\s+)?(?:user|human|owner)\s+(?:said|asked|wants?|told)\b",
     ("user", "human", "owner")),
    ("disregard_above", 0.55,
     r"\b(?:ignore|disregard|forget)\s+(?:the\s+|all\s+)?(?:above|previous|prior|preceding)\b"
     r"(?=\s*(?:and\b|,|\.|$|then\b))",
     ("above", "previous", "prior", "preceding")),
    ("ai_vocative", 0.40,
     r"(?:^|[>.!?:\n])\s*(?:hey\s+|dear\s+|attention\s+)?(?:ai\s+assistant|assistant|ai|sara)\s*[,:]",
     ("assistant", "ai", "sara")),
    ("ai_agents_directive", 0.50,
     r"\b(?:ai|llm)\s+(?:agents?|assistants?|systems?|models?|bots?|tools?)\b[^.\n]{0,30}\b(?:reading|processing|"
     r"summari[sz]ing|parsing|visiting|analy[sz]ing)\b"
     r"|\b(?:if|when)\s+you\s+are\s+an?\s+(?:ai|assistant|llm|language\s+model|chatbot)\b",
     ("ai ", "llm", "you are a")),
    ("address_ai_note", 0.50,
     r"\b(?:note|message|instructions?|memo|notice)\s+(?:for|to)\s+(?:the\s+|any\s+)?(?:ai|assistant|llm|chatbot|model|agent)s?\b",
     ("note for", "note to", "message for", "message to", "instruction for", "instructions for", "memo", "notice")),
    ("html_comment_to_ai", 0.60,
     r"<!--\s*(?:note\s+to\s+)?(?:ai|assistant|llm|chatbot|sara|system)\b",
     ("<!--",)),
    ("bracket_instruction", 0.60,
     r"\[\s*(?:assistant|ai|system|llm)\s+(?:instructions?|message|note|commands?)\s*\]",
     ("[",)),
    ("system_prompt_header", 0.50,
     r"\bsystem\s+prompt\s*(?:override|:)|\boverride\s+(?:the\s+)?system\s+prompt\b",
     ("system prompt",)),
    ("conceal_b", 0.50,
     r"\b(?:tell|inform|notify|alert)\s+(?:the\s+)?(?:user|human|anyone|no\s*one|nobody)\s+(?:nothing|anything)\b"
     r"|\b(?:tell|inform)\s+(?:no\s*one|nobody)\b"
     r"|\bkeep\s+(?:this|it|that)\s+(?:a\s+)?(?:secret|hidden|private)\b"
     r"|\b(?:do\s+not|don't|dont|never)\s+(?:mention|say|tell|reveal)\s+(?:it|this|that|anything)\b"
     r"|\bsay\s+nothing\b"
     r"|\b(?:do\s+not|don't|never)\s+let\s+(?:the\s+)?(?:user|human|anyone)\s+know\b",
     ("tell", "keep ", "mention", "say nothing", "reveal")),
    # ── fake system / developer / transcript markers ──
    ("header_banner", 0.60,
     r"(?:#{2,}|\*{2,}|={3,}|-{3,})\s*(?:developer|system|admin|root|operator)\s+(?:message|prompt|instructions?|note)",
     ("developer", "system", "admin", "root", "operator")),
    ("higher_priority", 0.55,
     r"\b(?:higher|top|highest|override)\s+priority\s+(?:than|over)\s+(?:the\s+)?(?:user|system|other)\b",
     ("priority",)),
    ("authority_claim", 0.50,
     r"\b(?:as|i\s+am)\s+(?:the\s+)?(?:developer|administrator|admin|system|owner|operator)\b[^.\n]{0,30}\b"
     r"(?:tell|order|instruct|command)(?:ing|s)?\s+you\b",
     ("developer", "admin", "system", "owner", "operator")),
    ("fake_transcript", 0.60,
     r"(?:^|\n)\s*(?:assistant|ai|sara)\s*:[^\n]{0,300}\n\s*user\s*:",
     ("\nuser", "user:")),
    ("prompt_fence", 0.60,
     r"\b(?:begin|start|end)\s+(?:of\s+)?(?:system|developer|admin|administrator|root|operator)\s+"
     r"(?:prompt|message|instructions?)\b",
     ("begin", "start", "end")),
    # ── role play ──
    ("no_restrictions", 0.25,
     r"\bno\s+(?:restrictions|rules|limits|filters|guidelines|safety)\b",
     ("no restrictions", "no rules", "no limits", "no filters", "no guidelines", "no safety")),
    ("named_jailbreak", 0.50,
     r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be)\s+dan\b|\bdan\s+(?:mode|can|always)\b",
     ("dan",)),
    ("role_agent", 0.40,
     r"\b(?:act\s+as|you\s+are|you're|become|play\s+the\s+role\s+of)\s+(?:my\s+|an?\s+|the\s+)?"
     r"(?:personal\s+|new\s+|helpful\s+|unrestricted\s+|evil\s+)?(?:agent|admin(?:istrator)?|developer|root|jarvis|dan|"
     r"system(?:\s+administrator)?)\b",
     ("act as", "you are", "you're", "become", "role of")),
    ("open_url_now", 0.40,
     r"\b(?:opens?|visit|navigate\s+to|go\s+to|browse\s+to)\b[^.\n]{0,20}https?://\S+[^.\n]{0,40}\b(?:without|silently|"
     r"immediately|right\s+now|automatically)\b",
     ("http",)),
    # ── exfiltration ──
    ("md_url_long_param", 0.55,
     r"!?\[[^\]\n]{0,80}\]\(\s*https?://[^)\s]*[?&][a-z0-9_]+=[^)\s&]{32,}",
     ("](http",)),
    ("md_url_secret_param", 0.50,
     r"!?\[[^\]\n]{0,80}\]\(\s*https?://[^)\s]*[?&][^)\s]*(?:conversation|chat_?history|clipboard|api_?key|secret|"
     r"password|token|credential)",
     ("](http",)),
    ("secret_placeholder", 0.50,
     r"\buser_(?:api_key|secret|token|password)\b|<\s*(?:user_name|api_key|password|token|secret)\s*>|"
     r"\{\{?\s*(?:api_key|token|password|secret)\s*\}?\}",
     ("user_", "<", "{")),
    ("tracking_pixel", 0.40,
     r"\b(?:tracking\s+pixel|beacon)\b",
     ("pixel", "beacon")),
    ("append_to_url", 0.60,
     r"\b(?:append|add|attach|include|put)\b[^.\n]{0,40}\b(?:clipboard|password|token|api[ _-]?key|secret|history|"
     r"conversation|notes?|contents?)\b[^.\n]{0,40}\b(?:url|link|address|http)",
     ("append", "attach")),
    ("exfil_to_url", 0.55,
     r"\b(?:send|upload|post|forward|submit|transmit|copy|paste|append|attach)\b[^.\n]{0,70}\b(?:to|into|onto|at|via)\s+"
     r"(?:this\s+|the\s+)?(?:url\s+|link\s+|site\s+|address\s+)?https?://",
     ("http",)),
    # ── Hinglish ──
    ("ask_skip_hinglish", 0.55,
     r"\b(?:user|usse|unse|mujhse)\s+(?:se\s+)?(?:puch\w*|pooch\w*|confirm\w*)[^.\n]{0,20}"
     r"\b(?:zarurat|jarurat|need)\s+(?:nahi|nahin|nhi)\b",
     ("zarurat", "jarurat")),
    ("delete_all_hinglish", 0.40,
     r"\b(?:sab|sabhi|saare|sare)\s+(?:\w+\s+){0,2}?(?:delete|hata|mita|erase|clear|band)\w*",
     ("sab ", "sabhi", "saare", "sare ")),
    ("ai_ko_nirdesh", 0.60,
     r"\bai\s+ko\s+(?:nirdesh|instruction|aadesh|adesh)\b",
     ("ai ko",)),
    # ── Hindi (Devanagari) ──
    ("ai_prefix_hindi", 0.40,
     r"(?:सहायक|एआई|असिस्टेंट)\s*[:,]",
     ("सहायक", "एआई", "असिस्टेंट")),
    ("conceal_anyone_hindi", 0.50,
     r"किसी\s*को\s*(?:मत|न|नहीं)\s*(?:बता|कह|बोल)",
     ("किसी",)),
)

EXTRA_RULES: Tuple[tuple, ...] = tuple(
    (rule_id, weight, re.compile(pattern, re.M), needles)
    for rule_id, weight, pattern, needles in _SPECS
)

# Substrings that must occur in the normalised text for a base rule (detector.py
# _RULE_SPECS) to be able to match; the regex is skipped otherwise. None marks a
# Devanagari-only rule (skipped for pure-ASCII text). Rules not listed always run.
BASE_NEEDLES = {
    "override_en": ("ignore", "disregard", "forget", "override", "bypass", "discard"),
    "override_en_after": ("ignore", "disregard", "forget", "override"),
    "forget_everything": ("forget", "ignore", "disregard"),
    "new_instructions": ("instruction", "system prompt", "directive"),
    "role_hijack": ("you are now", "you're now", "from now on", "your new", "pretend", "act as if",
                    "developer mode", "jailbreak", "dan mode"),
    "fake_role_tag": ("<", "["),
    "role_prefix": ("system", "assistant"),
    "address_ai": ("attention", "note to", "message for", "instruction", "important", "hey", "dear"),
    "exfil": ("send", "email", "e-mail", "mail", "forward", "upload", "post", "leak", "exfiltrate",
              "transmit", "share"),
    "command_to_ai": ("sara", "assistant", "the model", "ai ", " ai"),
    "shell_commands": ("powershell", "cmd", "rm -rf", "del /", "curl", "invoke-webrequest", "iex", "format c"),
    "conceal": ("do not", "don't", "dont", "never", "without"),
    "reveal_prompt": ("reveal", "print", "show", "repeat", "output", "display", "leak", "tell me"),
    "override_hinglish": ("pichle", "pichli", "purane", "puraane", "pehle", "upar", "sabhi", "saare", "sare", "sab"),
    "override_hinglish_rev": ("ignore", "bhool", "bhul", "nazar"),
    "role_hijack_hinglish": ("ab se", "tum ab", "aap ab", "ab tum"),
    "conceal_hinglish": ("user ko", "yuser ko", "malik ko"),
    "exfil_hinglish": ("password", "passwd", "api key", "apikey", "token", "secret", "clipboard"),
    "exfil_hinglish_rev": ("bhej", "mail", "email", "upload"),
    "override_hindi": None,
    "override_hindi_rev": None,
    "role_hijack_hindi": None,
    "conceal_hindi": None,
    "exfil_hindi": None,
}
