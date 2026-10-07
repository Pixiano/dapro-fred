"""calculate: exact arithmetic through `ast`, never `eval`.

Sockets
  in : run(expression)   numbers, + - * / // % ** (also ^, x, ÷), parentheses, sqrt abs round min max, pi, e; thousands commas are dropped ("1,200")
  out: the number as text ("51", "2.5"); not `say`: the model words it ("about 3.3 billion"), the result stays exact
       anything else, a divide by zero, or an exponent over 1000 is bad_args with a reason the model can act on
"""
import ast
import math
import operator
import re

from tools.registry import ToolError

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max}
NAMES = {"pi": math.pi, "e": math.e}


def _ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in OPS:
        a, b = _ev(n.left), _ev(n.right)
        if isinstance(n.op, ast.Pow) and abs(b) > 1000:
            raise ToolError("bad_args", "That exponent is too large.")
        r = OPS[type(n.op)](a, b)
        if isinstance(r, int) and r.bit_length() > 4000:                             # stops nested powers from growing without bound (each step is checked)
            raise ToolError("bad_args", "That number is too large.")
        return r
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        return +_ev(n.operand) if isinstance(n.op, ast.UAdd) else -_ev(n.operand)
    if isinstance(n, ast.Name) and n.id in NAMES:
        return NAMES[n.id]
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and not n.keywords:
        return FUNCS[n.func.id](*[_ev(a) for a in n.args])
    raise ToolError("bad_args", "I can only do plain arithmetic.", "unsupported expression; use numbers, + - * / // % **, parentheses, sqrt, abs, round, min, max, pi, e")


def run(expression: str) -> str:
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expression).replace("^", "**").replace("×", "*").replace("÷", "/")
    text = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", text)
    try:
        v = _ev(ast.parse(text.strip(), mode="eval").body)
    except ToolError:
        raise
    except ZeroDivisionError:
        raise ToolError("bad_args", "That divides by zero.")
    except (SyntaxError, ValueError, TypeError, OverflowError, MemoryError) as ex:
        raise ToolError("bad_args", "I couldn't read that sum.", f"cannot evaluate {expression!r}: {ex}")
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ToolError("bad_args", "That number is too large.", f"{expression!r} is not a finite number")
        v = round(v, 10)                                                            # 2 ** 0.5 * 2 ** 0.5 is 2.0000000000000004: say 2
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
    return str(v)
