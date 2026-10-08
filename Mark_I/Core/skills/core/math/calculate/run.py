"""calculate: exact arithmetic through `ast`, never `eval`, plus unit conversion (units.py).

Sockets
  in : run(expression)   numbers, + - * / // % ** (also ^, x, ÷), parentheses, sqrt cbrt root(x, n) abs round min max floor ceil, pi, e; thousands commas are dropped ("1,200");
                         "17 percent of 300" and "17% of 300"; a conversion "5 km in miles", "98.6 F to C", "2.5 crore to million", "convert 10 kg" (see units.py: SI, common and Indian units, 1-3 alternatives)
  out: the number as text ("51", "2.5"), a big whole result also in lakh/crore ("1234567 (about 12.3 lakh)"); a conversion as "5 km = 3.10686 mi (also ...)"; not `say`: the model words it, the result stays exact
       anything else, a divide by zero, or an exponent over 1000 is bad_args with a reason the model can act on
"""
import ast
import importlib.util
import math
import operator
import re
from pathlib import Path

from tools.registry import ToolError

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
def _root(x, n=2):
    if n == 0 or (x < 0 and int(n) % 2 == 0):
        raise ToolError("bad_args", "That root isn't a real number.")
    return -((-x) ** (1 / n)) if x < 0 else x ** (1 / n)


FUNCS = {"sqrt": math.sqrt, "cbrt": lambda x: _root(x, 3), "root": _root, "abs": abs, "round": round, "min": min, "max": max, "floor": math.floor, "ceil": math.ceil}
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


def _load_units():
    spec = importlib.util.spec_from_file_location("calculate_units", Path(__file__).with_name("units.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_units = _load_units()                                                              # at import: the loader may delete the skill's folder copy after loading it (testkit does)


def _lakh(v: float) -> str:
    a = abs(v)
    if a < 1e5 or a != int(a):
        return ""
    for scale, name in ((1e7, "crore"), (1e5, "lakh")):
        if a >= scale:
            return f" (about {a / scale:.3g} {name})".replace("about 1 ", "exactly 1 ") if a % scale else f" (exactly {a / scale:.0f} {name})"
    return ""


PERCENT = re.compile(r"([-+]?\d[\d.,]*)\s*(?:%|percent|per cent)\s*of\s*([-+]?\d[\d.,]*)", re.I)


def run(expression: str) -> str:
    u = _units
    parsed = u.parse(expression)
    if parsed:
        num, src, dst = parsed
        try:
            return u.convert(_number(num), src, dst)
        except ValueError as ex:
            raise ToolError("bad_args", str(ex), f"conversion failed for {expression!r}: {ex}")
    return _plain(PERCENT.sub(lambda m: f"({m.group(1)}) / 100 * ({m.group(2)})", expression))


def _number(text: str) -> float:
    return float(_value(text))


def _value(expression: str):
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
            v = int(v)
    return v


def _plain(expression: str) -> str:
    v = _value(expression)
    return str(v) + _lakh(v)
