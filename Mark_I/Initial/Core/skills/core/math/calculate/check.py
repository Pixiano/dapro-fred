"""Self-check for calculate: results, spoken-style input, and everything that must be refused."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import skill


def _selfcheck():
    reg, problems = skill("calculate", {})
    assert not problems and not reg.spec("calculate").say
    for expr, want in (("17 * 300 / 100", "51"), ("2^10", "1024"), ("1,200 + 30", "1230"), ("sqrt(16) + abs(-2)", "6"), ("10 / 4", "2.5"), ("-3 + 5", "2"), ("round(7 / 3, 2)", "2.33"),
                       ("12 x 8", "96"), ("12 × 8", "96"), ("144 ÷ 12", "12"), ("2 ** 0.5 * 2 ** 0.5", "2"), ("7 // 2 + 7 % 2", "4"), ("pi * 2", "6.2831853072")):
        assert reg.call("calculate", {"expression": expr}).data == want, (expr, reg.call("calculate", {"expression": expr}))
    for bad in ("__import__('os')", "1/0", "2 ** 99999", "hello", "", "(1", "open('x')", "[1,2]", "True + 1", "lambda: 1", "1e999 * 1e999 ** 2", "9" * 400 + " ** 9"):
        r = reg.call("calculate", {"expression": bad or " "})
        assert not r.ok and r.error_kind == "bad_args", (bad[:30], r)
    print("calculate check ok")


if __name__ == "__main__":
    _selfcheck()
