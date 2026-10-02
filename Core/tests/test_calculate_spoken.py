# Core/tests/test_calculate_spoken.py
#
# Spoken maths the calculate tool could not read (Mark II benchmark,
# 2026-09-30): "the square root of 144" and "847 times 23 minus 15 percent".

import pytest

from orchestrator.dispatcher import Dispatcher
from tools.assist_tools import calculate


@pytest.mark.parametrize("expr,answer", [
    ("the square root of 144", "12"),
    ("what's the square root of 144", "12"),
    ("sqrt(144)", "12"),
    ("847 times 23 minus 15 percent", "16558.85"),
    ("200 plus 10%", "220"),
    ("17 percent of 300", "51"),
    ("5 squared", "25"),
    ("3 cubed", "27"),
    ("2 to the power of 10", "1024"),
    ("1234 divided by 7", "176.2857"),
    ("12 times 8", "96"),
])
def test_spoken_forms(expr, answer):
    assert answer in calculate(expr)


def test_still_refuses_code():
    assert "couldn't read" in calculate("__import__('os').system('echo hi')")


def test_dispatcher_sends_roots_and_powers_to_calculate():
    d = Dispatcher()
    for q in ("what's the square root of 144", "what is 5 squared",
              "what's 847 times 23 minus 15 percent"):
        assert d.match(q)["tool"] == "calculate"
