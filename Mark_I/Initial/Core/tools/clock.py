"""Tools: the date/time and a safe calculator.

Sockets
  in : register(registry) adds get_current_time, calculate, days_until
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


NAMED = {"christmas": (12, 25), "new year": (1, 1), "new years": (1, 1), "new year's": (1, 1), "independence day": (8, 15), "republic day": (1, 26), "halloween": (10, 31), "valentine's day": (2, 14), "valentines day": (2, 14)}


def days_until(date: str, today=None) -> str:
    """Whole days from today to a date: a name (christmas), or anything parse_when reads (\"december 25\", \"2027-01-05\", \"friday\"). Past dates roll to next year only for named days."""
    from datetime import date as D
    from tools.reminders import parse_when
    today = today or D.today()
    key = date.strip().lower().rstrip(".!?")
    if key in NAMED:
        m, d = NAMED[key]
        t = D(today.year, m, d)
        t = t if t >= today else D(today.year + 1, m, d)
        label = key.title()
    else:
        p = parse_when(key, datetime.combine(today, datetime.min.time()))
        if p is None:
            raise ToolError("bad_args", "I didn't catch which date.", f"cannot read a date from {date!r}; give a name like christmas or a date like december 25")
        t, label = p.date(), f"{p.day} {p.strftime('%B')}"
    n = (t - today).days
    return "That's today." if n == 0 else (f"{n} day{'s' if n != 1 else ''} until {label}." if n > 0 else f"{-n} day{'s' if n != -1 else ''} since {label}.")


def register(reg):
    reg.register(ToolSpec("get_current_time", get_current_time, description="Get the current local time and date. Use it for any question about today's date or how far away a date is.", group="clock", say=True,
                          cues=(r"what(?:'s| is) the (?:time|date|day)", r"what time", r"what day", r"today's date", r"current (?:time|date)", r"what is today", r"how many days", r"days? (?:until|till|left|since)", r"how long (?:until|till|since)"),
                          examples=("what time is it", "what day is it today", "what's the date")))
    reg.register(ToolSpec("days_until", days_until, {"type": "object", "required": ["date"], "properties": {"date": {"type": "string", "description": "a name (christmas, new year) or a date (december 25, 2027-01-05, friday)"}}},
                          "Count the days from today to a date or named holiday. Use for 'how many days until ...'.", group="clock", say=True,
                          cues=(r"how many days", r"days? (?:until|till|left|to go)", r"how long (?:until|till)", r"countdown"), examples=("how many days until christmas", "how long till my birthday on march 3")))
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
    from datetime import date as D
    T = D(2026, 10, 6)
    assert days_until("christmas", T) == "80 days until Christmas." and days_until("december 25", T) == "80 days until 25 December." and days_until("new year", T).startswith("87 days")
    assert days_until("2026-10-07", T) == "1 day until 7 October." and days_until("2026-10-06", T) == "That's today." and days_until("2026-10-01", T) == "5 days since 1 October."
    assert days_until("halloween", D(2026, 11, 1)) == "364 days until Halloween."
    try:
        days_until("sometime", T)
        raise AssertionError
    except ToolError as ex:
        assert ex.kind == "bad_args"
    from tools.registry import Registry
    r = Registry()
    register(r)
    assert r.call("calculate", {"expression": "3 + 4"}).data == "7" and r.call("calculate", {"expression": "import os"}).error_kind == "bad_args"
    t = r.call("get_current_time")
    assert t.ok and t.data.startswith("It's ") and str(datetime.now().year) in t.data
    print("clock ok")


if __name__ == "__main__":
    _selfcheck()
