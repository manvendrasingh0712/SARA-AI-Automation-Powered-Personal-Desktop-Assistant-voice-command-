"""
sara.orchestrator.calc_utils
Safe arithmetic-expression evaluation (calculator intent) and
natural-language duration parsing (timer intent).
"""

import ast
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
        return f"I couldn't calculate that. Error: {e}"


def _parse_duration_to_seconds(text: str):
    text = text.lower().strip()
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
