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
    for expr, want in (("17 percent of 300", "51"), ("17% of 300", "51"), ("20% of 150 + 5", "35"), ("cbrt(27) + root(16, 4)", "5"), ("1234567 * 3", "3703701 (about 37 lakh)"), ("2000000 / 2", "1000000 (exactly 10 lakh)"),
                       ("100 km/h to mph", "100 km/h = 62.1371 mph (also 27.7778 m/s, 53.9957 knots)"), ("98.6 F to C", "98.6 °F = 37 °C (also 310.15 K)"), ("convert 10 kg", "10 kg = 10000 g, 22.0462 lb, 352.74 oz")):
        assert reg.call("calculate", {"expression": expr}).data == want, (expr, reg.call("calculate", {"expression": expr}))
    r = reg.call("calculate", {"expression": "5 km in miles"})
    assert r.ok and r.data.startswith("5 km = 3.10686 mi (also ") and "5000 m" in r.data, r
    for bad in ("10 kg in liters", "50 usd to inr", "5 km to gaj", "root(-4, 2)", "root(5, 0)"):
        r = reg.call("calculate", {"expression": bad})
        assert not r.ok and r.error_kind == "bad_args", (bad, r)
    for bad in ("__import__('os')", "1/0", "2 ** 99999", "hello", "", "(1", "open('x')", "[1,2]", "True + 1", "lambda: 1", "1e999 * 1e999 ** 2", "9" * 400 + " ** 9"):
        r = reg.call("calculate", {"expression": bad or " "})
        assert not r.ok and r.error_kind == "bad_args", (bad[:30], r)
    print("calculate check ok")


if __name__ == "__main__":
    _selfcheck()
