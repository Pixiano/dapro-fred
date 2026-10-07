"""Self-check for the days_until skill: holidays, month-day forms, ISO, weekday, past, bad input."""
import sys
from datetime import date as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import skill


def _selfcheck():
    reg, problems = skill("days_until", {})
    assert not problems
    import importlib.util
    spec = importlib.util.spec_from_file_location("du", Path(__file__).with_name("run.py"))
    du = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(du)
    T = D(2026, 10, 6)                                                              # a Tuesday
    for ask, want in (("christmas", "80 days until Christmas."), ("december 25", "80 days until 25 December."), ("25 December", "80 days until 25 December."), ("25th of dec", "80 days until 25 December."),
                      ("new year", "87 days until New Year."), ("2026-10-07", "1 day until 7 October."), ("2026-10-06", "That's today."), ("2026-10-01", "5 days since 1 October."),
                      ("friday", "3 days until Friday."), ("tuesday", "7 days until Tuesday."), ("october 1", "360 days until 1 October.")):
        assert du.run(ask, T) == want, (ask, du.run(ask, T))
    assert du.run("halloween", D(2026, 11, 1)) == "364 days until Halloween."
    for bad in ("sometime", "", "february 30", "2026-13-40", "my birthday"):
        assert reg.call("days_until", {"date": bad or " "}).error_kind == "bad_args", bad
    assert reg.call("days_until", {"date": "christmas"}).ok
    print("days_until check ok")


if __name__ == "__main__":
    _selfcheck()
