"""Tools: the date/time and a safe calculator.

Sockets
  in : register(registry) adds get_current_time, calculate
  out: get_current_time -> a spoken sentence (say=True: the engine speaks it as is, the model never paraphrases a date); calculate -> the number as text
calculate parses with `ast` (numbers, + - * / // % **, parentheses, sqrt/abs/round/min/max, pi, e) and never uses eval; anything else is bad_args. Not `say`: the model
words the answer ("about 3.3 billion"), the result stays exact.
Self-check: python tools/clock.py
"""
import ast
import math
import operator
import re
import sys
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.registry import ToolError, ToolSpec

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max}
_NAMES = {"pi": math.pi, "e": math.e}


def _ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
        a, b = _ev(n.left), _ev(n.right)
        if isinstance(n.op, ast.Pow) and abs(b) > 1000:
            raise ToolError("bad_args", "That exponent is too large.")
        return _OPS[type(n.op)](a, b)
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        return +_ev(n.operand) if isinstance(n.op, ast.UAdd) else -_ev(n.operand)
    if isinstance(n, ast.Name) and n.id in _NAMES:
        return _NAMES[n.id]
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
        return _FUNCS[n.func.id](*[_ev(a) for a in n.args])
    raise ToolError("bad_args", "I can only do plain arithmetic.", "unsupported expression; use numbers, + - * / // % **, parentheses, sqrt, abs, round, min, max, pi, e")


def calculate(expression: str) -> str:
    try:
        v = _ev(ast.parse(re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expression).replace("^", "**").replace("×", "*").replace("÷", "/"), mode="eval").body)
    except ToolError:
        raise
    except ZeroDivisionError:
        raise ToolError("bad_args", "That divides by zero.")
    except (SyntaxError, ValueError, TypeError, OverflowError) as ex:
        raise ToolError("bad_args", "I couldn't read that sum.", f"cannot evaluate {expression!r}: {ex}")
    return str(int(v)) if isinstance(v, float) and v.is_integer() and abs(v) < 1e15 else str(round(v, 10) if isinstance(v, float) else v)


def get_current_time() -> str:
    n = datetime.now()
    return f"It's {n.strftime('%I:%M %p').lstrip('0')} on {n.strftime('%A')}, {n.day} {n.strftime('%B %Y')}."


def register(reg):
    reg.register(ToolSpec("get_current_time", get_current_time, description="Get the current local time and date. Use it for any question about today's date or how far away a date is.", group="clock", say=True,
                          cues=(r"what(?:'s| is) the (?:time|date|day)", r"what time", r"what day", r"today's date", r"current (?:time|date)", r"what is today", r"how many days", r"days? (?:until|till|left|since)", r"how long (?:until|till|since)"),
                          examples=("what time is it", "what day is it today", "what's the date", "how many days until christmas")))
    reg.register(ToolSpec("calculate", calculate, {"type": "object", "required": ["expression"], "properties": {"expression": {"type": "string", "description": "Arithmetic, e.g. 17 * 300 / 100"}}},
                          "Evaluate an arithmetic expression exactly. Use for any sum, percentage or unit arithmetic instead of working it out yourself.", group="calc",
                          cues=(r"calculate", r"how much is", r"\d+\s*(?:%|percent|times|plus|minus|divided)", r"what(?:'s| is) \d+", r"square root", r"percent of"),
                          examples=("what's 17 percent of 300", "how much is 45 times 3", "add these numbers up")))


def _selfcheck():
    assert calculate("17 * 300 / 100") == "51" and calculate("2^10") == "1024" and calculate("1,200 + 30") == "1230" and calculate("sqrt(16) + abs(-2)") == "6"
    assert calculate("10 / 4") == "2.5" and calculate("-3 + 5") == "2" and calculate("round(7 / 3, 2)") == "2.33"
    for bad, kind in (("__import__('os')", "bad_args"), ("1/0", "bad_args"), ("2 ** 99999", "bad_args"), ("hello", "bad_args"), ("", "bad_args"), ("(1", "bad_args"), ("open('x')", "bad_args")):
        try:
            calculate(bad)
            raise AssertionError(bad)
        except ToolError as ex:
            assert ex.kind == kind and ex.speakable, bad
    from tools.registry import Registry
    r = Registry()
    register(r)
    assert r.call("calculate", {"expression": "3 + 4"}).data == "7" and r.call("calculate", {"expression": "import os"}).error_kind == "bad_args"
    t = r.call("get_current_time")
    assert t.ok and t.data.startswith("It's ") and str(datetime.now().year) in t.data
    print("clock ok")


if __name__ == "__main__":
    _selfcheck()
