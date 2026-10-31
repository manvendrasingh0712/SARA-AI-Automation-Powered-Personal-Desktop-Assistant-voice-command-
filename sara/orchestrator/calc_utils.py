"""
sara.orchestrator.calc_utils
Safe arithmetic-expression evaluation (calculator intent) and
natural-language duration parsing (timer intent).
"""

import ast
import logging
import operator
import re

from sara.core.text.spoken_numbers import ENGLISH_ONES, ENGLISH_TENS, words_to_digits

from sara.orchestrator._constants import (
    _CALC_EXPR_RE,
    _CALC_MAX_LEN,
    _CALC_MAX_NUMBER_DIGITS,
    _CALC_MAX_POW_OPS,
    _CALC_MAX_EXPONENT_VALUE,
    _CALC_EXPONENT_RE,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Calculator
# ----------------------------------------------------------------------------


class _CalcError(Exception):
    """Raised for any disallowed node or unsafe construct in an expression."""


_CALC_MAX_AST_DEPTH = 20

_CALC_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_CALC_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_node(node, depth: int = 0):
    if depth > _CALC_MAX_AST_DEPTH:
        raise _CalcError("expression is nested too deeply")

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, depth + 1)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _CalcError("only numeric constants are allowed")
        return node.value

    if isinstance(node, ast.BinOp):
        op = _CALC_BIN_OPS.get(type(node.op))
        if op is None:
            raise _CalcError(f"operator {type(node.op).__name__} is not allowed")
        left = _eval_node(node.left, depth + 1)
        right = _eval_node(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and abs(right) > _CALC_MAX_EXPONENT_VALUE:
            raise _CalcError("exponent is too large")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = _CALC_UNARY_OPS.get(type(node.op))
        if op is None:
            raise _CalcError(f"operator {type(node.op).__name__} is not allowed")
        return op(_eval_node(node.operand, depth + 1))

    raise _CalcError(f"{type(node).__name__} is not allowed")


def _ast_eval(expr: str):
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree)


_NUM = r"(\d+(?:\.\d+)?)"
_PCT = r"\s*(?:%|percent|per cent|pratishat)"
# "15 percent of 2400" / "15% of 2400"  ->  (15/100*2400)
_PERCENT_OF_RE = re.compile(_NUM + _PCT + r"\s+of\s+" + _NUM, re.IGNORECASE)
# Hinglish "2400 ka 15 percent"  ->  (15/100*2400)
_KA_PERCENT_RE = re.compile(_NUM + r"\s+ka\s+" + _NUM + _PCT, re.IGNORECASE)


def _normalize_percent(expr: str) -> str:
    expr = _PERCENT_OF_RE.sub(lambda m: f"({m.group(1)}/100*{m.group(2)})", expr)
    return _KA_PERCENT_RE.sub(lambda m: f"({m.group(2)}/100*{m.group(1)})", expr)


_SPOKEN_OPS = (
    (re.compile(r"\bsquare root of\s+(\d+(?:\.\d+)?)", re.I), lambda m: f"({m.group(1)}**0.5)"),
    (re.compile(r"(\d+(?:\.\d+)?)\s+squared\b", re.I), lambda m: f"({m.group(1)}**2)"),
    (re.compile(r"(\d+(?:\.\d+)?)\s+cubed\b", re.I), lambda m: f"({m.group(1)}**3)"),
    (re.compile(r"\s+(?:raised to the power of|to the power of|raised to|to the power|power)\s+", re.I), lambda m: "**"),
    (re.compile(r"\s+(?:multiplied by|times|into)\s+", re.I), lambda m: "*"),
    (re.compile(r"\s+divided by\s+|\s+over\s+", re.I), lambda m: "/"),
    (re.compile(r"\s+plus\s+", re.I), lambda m: "+"),
    (re.compile(r"\s+minus\s+", re.I), lambda m: "-"),
    (re.compile(r"\s+(?:modulo|mod)\s+", re.I), lambda m: "%"),
    (re.compile(r"(?<=\d)\s*x\s*(?=\d)", re.I), lambda m: "*"),
)


def _normalize_spoken_ops(expr: str) -> str:
    """"7 times 8 plus 3" -> "7*8+3"; spoken numbers ("five") become digits first."""
    expr = _digits_for_number_words(expr)
    for pattern, repl in _SPOKEN_OPS:
        expr = pattern.sub(repl, expr)
    return expr


def _safe_calc(expression: str) -> str:
    expr = _normalize_percent(_normalize_spoken_ops(expression.strip()))
    expr = (
        expr.replace("^", "**")
        .replace("\u00d7", "*")
        .replace("\u00f7", "/")
        .replace(",", "")
    )
    if len(expr) > _CALC_MAX_LEN:
        return "That expression is too long for me to calculate safely."
    if not _CALC_EXPR_RE.match(expr):
        return f"I can only calculate numeric expressions. I got: {expression}"

    if expr.count("**") > _CALC_MAX_POW_OPS:
        return "That expression has too many exponents for me to calculate safely."
    for exp_match in _CALC_EXPONENT_RE.finditer(expr):
        if abs(int(exp_match.group(1))) > _CALC_MAX_EXPONENT_VALUE:
            return "That exponent is too large for me to calculate safely."
    for num in re.findall(r"\d+", expr):
        if len(num) > _CALC_MAX_NUMBER_DIGITS:
            return "Those numbers are too large for me to calculate safely."

    try:
        result = _ast_eval(expr)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        elif isinstance(result, float):
            result = round(result, 6)  # spoken aloud: not 16 digits
        return f"The answer is {result}."
    except ZeroDivisionError:
        return "That's a division by zero \u2014 undefined."
    except Exception as e:
        logger.error(f"Failed to evaluate expression '{expression}': {e}")
        return "I couldn't calculate that \u2014 please check the expression."


# ── D77: spoken numbers ─────────────────────────────────────────────────
#
# _parse_duration_to_seconds() was digit-only, so every duration said out
# loud failed outright:
#
#     "set a timer for ten minutes"   -> "Sorry, I couldn't understand
#                                         the duration 'ten minutes'."
#     "set a timer for half an hour"  -> same
#
# The router matched set_timer correctly in both cases, which is why the
# bench never showed it: bench/golden checks the INTENT, not the slot. The
# adversarial row "long rambling sentence with one embedded command" is
# recorded as passing while the timer it embeds does nothing -- its slot
# captures "fifteen minutes because the tea is on the stove", and the
# number word is what defeats the parser, not the trailing words (a slot
# with a digit tolerates trailing text fine).
#
# NOTE ON DUPLICATION: sara/core/memory2/timeparse.py has its own smaller
# _NUMWORD (1-12, plus Hindi "ek"/"एक") for relative DATE windows. Merging
# them would couple memory2 to the orchestrator for no benefit, so they
# stay separate -- but a test pins that they agree wherever they overlap,
# because two tables that must not contradict each other is exactly the
# shape of the D74/D75 defects.
_NUMBER_WORDS = ENGLISH_ONES
_NUMBER_TENS = ENGLISH_TENS


# Devanagari units and operators, matched as whole whitespace-separated tokens (\w splits Devanagari at matras)
_HI_WORDS = {
    "घंटे": "hours", "घंटा": "hours", "घण्टे": "hours", "घण्टा": "hours", "मिनट": "minutes", "सेकंड": "seconds",
    "सेकेंड": "seconds", "गुणा": "*", "जमा": "+", "प्लस": "+", "घटा": "-", "माइनस": "-", "भाग": "/",
}


def _hindi_words_to_english(text: str) -> str:
    """Devanagari number / unit / operator words -> digits and English ("दो घंटे" -> "2 hours")."""
    if not isinstance(text, str) or not re.search("[\u0900-\u097f]", text):
        return text
    out = []
    for tok in text.split():
        if tok in _HI_WORDS:
            out.append(_HI_WORDS[tok])
        else:
            out.append(tok)
    return " ".join(out)


def _digits_for_number_words(text: str) -> str:
    """
    Rewrite spoken number words as digits, leaving everything else alone. The tables and the rules live in
    sara.core.text.spoken_numbers; this keeps the arithmetic / duration use (every number word converts, "a" and
    "an" only before a unit) plus the Devanagari operator and unit words.
    """
    if not isinstance(text, str) or not text:
        return text
    return words_to_digits(_hindi_words_to_english(text), unit_aware=False)


def _parse_duration_to_seconds(text: str):
    text = _digits_for_number_words(str(text or "")).lower().strip()
    total = 0
    found = False
    for pattern, multiplier in [
        (r"(\d+(?:\.\d+)?)\s*(?:hour|hr|ghanta|ghante|ghanton|ghantey)s?", 3600),
        (r"(\d+(?:\.\d+)?)\s*(?:minute|min|mint|minat)s?", 60),
        (r"(\d+(?:\.\d+)?)\s*(?:second|sec|sekand)s?", 1),
    ]:
        m = re.search(pattern, text)
        if m:
            total += int(round(float(m.group(1)) * multiplier))
            found = True
    if not found:
        m = re.match(r"^(\d+)$", text)
        if m:
            total = int(m.group(1)) * 60
            found = True
    return total if found and total > 0 else None
