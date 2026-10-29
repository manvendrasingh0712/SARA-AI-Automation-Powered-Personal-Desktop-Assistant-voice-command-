"""
sara.orchestrator.calc_utils
Safe arithmetic-expression evaluation (calculator intent) and
natural-language duration parsing (timer intent).
"""

import ast
import logging
import operator
import re

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


def _safe_calc(expression: str) -> str:
    expr = expression.strip()
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
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_NUMBER_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40,  # common misspelling
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _digits_for_number_words(text: str) -> str:
    """
    Rewrite spoken number words as digits, leaving everything else alone.

    Handles the compound forms a duration actually uses -- "forty five",
    "forty-five", "twenty two" -- by combining a tens word with a
    following unit word. "half an hour" and "half hour" are rewritten to
    "30 minutes" before anything else, because "half" is not a count.

    Deliberately conservative: a digit already in the text is never
    touched, and a word that is not a number is copied through unchanged.
    """
    if not isinstance(text, str) or not text:
        return text

    out = re.sub(r"\bhalf\s+(?:an?\s+)?hour\b", "30 minutes", text, flags=re.I)
    out = re.sub(r"\bhalf\s+(?:a\s+)?minute\b", "30 seconds", out, flags=re.I)

    tokens = re.split(r"(\W+)", out)  # keeps separators, so spacing survives
    result = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if low in _NUMBER_TENS:
            value = _NUMBER_TENS[low]
            # look past one separator for a unit word: "forty five", "forty-five"
            if (
                i + 2 < len(tokens)
                and tokens[i + 1].strip() in ("", "-")
                and tokens[i + 2].lower() in _NUMBER_WORDS
                and _NUMBER_WORDS[tokens[i + 2].lower()] < 10
            ):
                value += _NUMBER_WORDS[tokens[i + 2].lower()]
                i += 3
            else:
                i += 1
            result.append(str(value))
            continue
        if low in _NUMBER_WORDS:
            # "a"/"an" only count as 1 in front of a unit ("an hour"), never
            # on their own -- otherwise "a bit" would become "1 bit".
            if low in ("a", "an"):
                nxt = tokens[i + 2].lower() if i + 2 < len(tokens) else ""
                if not re.match(r"^(?:hour|hr|minute|min|second|sec)s?$", nxt):
                    result.append(tok)
                    i += 1
                    continue
            result.append(str(_NUMBER_WORDS[low]))
            i += 1
            continue
        result.append(tok)
        i += 1
    return "".join(result)


def _parse_duration_to_seconds(text: str):
    text = _digits_for_number_words(str(text or "")).lower().strip()
    total = 0
    found = False
    for pattern, multiplier in [
        (r"(\d+)\s*(?:hour|hr)s?", 3600),
        (r"(\d+)\s*(?:minute|min)s?", 60),
        (r"(\d+)\s*(?:second|sec)s?", 1),
    ]:
        m = re.search(pattern, text)
        if m:
            total += int(m.group(1)) * multiplier
            found = True
    if not found:
        m = re.match(r"^(\d+)$", text)
        if m:
            total = int(m.group(1)) * 60
            found = True
    return total if found and total > 0 else None
