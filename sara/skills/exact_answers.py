"""
sara.skills.exact_answers
Unit conversions, date arithmetic, percentages and square roots answered by code (sara/core/exact.py), not by the
chat model (audit part 2, O3). The pattern only decides whether the sentence LOOKS like one of these; the handler
returns None when it cannot compute it, and the dispatcher then falls through to normal chat.
"""
from __future__ import annotations

from sara.core import exact

from ._framework import SkillContext, skill


@skill(
    name="exact_answer",
    patterns=[
        r"^\W*(?:please\s+)?(?:convert\s+)?-?\d+(?:\.\d+)?\s*[a-zA-Z]+(?:/[a-zA-Z]+)?\s+(?:in|to|into|ko|mein|me|kitne|kitni|kitna)\s+[a-zA-Z]+(?:/[a-zA-Z]+)?.*$",
        r"^\W*how\s+many\s+[a-zA-Z/]+\s+(?:are\s+)?(?:there\s+)?(?:in|is)\s+.+$",
        r"^\W*(?:the\s+)?square\s+root\s+of\s+-?\d+(?:\.\d+)?\W*$",  # "what is the square root of" is the calculator's
        r"^\W*-?\d+(?:\.\d+)?\s+ka\s+(?:square\s+root|vargmool).*$",
        r"^\W*(?:what\s+)?(?:percentage|percent|kitna\s+percent)\s+(?:is|of)\s+\d.*out\s+of\s+\d.*$",
        r"^\W*\d+(?:\.\d+)?\s+out\s+of\s+\d+(?:\.\d+)?\s+.*(?:percent|percentage).*$",
        r"^\W*\d+(?:\.\d+)?\s*(?:%|percent)\s+(?:tip|gst|tax|discount|vat)\s+(?:on|of|par)\s+\d.*$",
        r"^\W*(?:what(?:'s| is)\s+(?:the\s+)?date\s+)?\d+\s+days?\s+(?:from\s+(?:today|now)|later).*$",
        r"^\W*aaj\s+se\s+\d+\s+din\s+(?:baad|pehle).*$",
        r"^\W*(?:kal|parso|aaj)\s+(?:kitni\s+(?:tarikh|date)|kya\s+(?:tarikh|date)|kaun\s+sa\s+din).*$",
        r"^\W*what\s+day\s+(?:of\s+the\s+week\s+)?is\s+(?:it\s+on\s+)?\d.*$",
        r"^\W*how\s+many\s+days\s+(?:are\s+)?in\s+[A-Za-z]+.*$",
        r"^\W*[A-Za-z]+\s+(?:mein|me)\s+kitne\s+din.*$",
    ],
    gate=("convert", "how many", "square root", "vargmool", "out of", "percent", "%", "days from", "days later", "din baad", "din pehle",
          "tarikh", "date", "kaun sa din", "what day", "kitne", " in ", " to ", " into "),
    description="Exact unit, date, percentage and square-root answers without the language model",
    category="maths",
    examples=("5 km kitne miles", "37 ka square root", "25 days from today"),
)
def handle_exact(match, ctx: SkillContext):
    return exact.answer(ctx.user_input)
