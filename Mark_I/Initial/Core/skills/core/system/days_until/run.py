"""days_until: whole days from today to a holiday name, "december 25" / "25 december", an ISO date or a weekday (next one). Read-only.

Sockets
  in : run(date, today=None)   `today` is for the self-check only
  out: "80 days until Christmas." / "That's today." / "5 days since 1 October."; a date it cannot read is bad_args naming what it accepts
Named holidays roll to next year once passed; explicit dates and weekdays do not (a past explicit date counts "since"). A weekday means the next one, never today.
"""
import re
from datetime import date as D, timedelta

from tools.registry import ToolError

NAMED = {"christmas": (12, 25), "new year": (1, 1), "new years": (1, 1), "new year's": (1, 1), "republic day": (1, 26), "independence day": (8, 15), "halloween": (10, 31), "valentine's day": (2, 14), "valentines day": (2, 14)}
MONTHS = {m: i + 1 for i, m in enumerate("january february march april may june july august september october november december".split())}
DAYS = {d: i for i, d in enumerate("monday tuesday wednesday thursday friday saturday sunday".split())}


def _month(w):
    return next((v for k, v in MONTHS.items() if k == w or (len(w) >= 3 and k.startswith(w))), None)


def _target(key: str, today: D):
    if key in NAMED:
        m, d = NAMED[key]
        t = D(today.year, m, d)
        return (t if t >= today else D(today.year + 1, m, d)), key.title()
    if key in DAYS:
        return today + timedelta(days=(DAYS[key] - today.weekday()) % 7 or 7), key.title()
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", key)
    if iso:
        return D(*map(int, iso.groups())), None
    for pat, order in ((r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?", "md"), (r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)", "dm")):
        m = re.fullmatch(pat, key)
        if m:
            a, b = m.groups()
            mon, day = (_month(a), int(b)) if order == "md" else (_month(b), int(a))
            if mon:
                t = D(today.year, mon, day)
                return (t if t >= today else D(today.year + 1, mon, day)), None                    # a month and day with no year means the coming one
    return None, None


def run(date: str, today=None) -> str:
    today = today or D.today()
    try:
        t, label = _target(date.strip().lower().rstrip(".!?"), today)
    except ValueError:
        t = None
    if t is None:
        raise ToolError("bad_args", "I didn't catch which date.", f"cannot read a date from {date!r}; give a holiday like christmas, a date like december 25 or 2027-01-05, or a weekday")
    label = label or f"{t.day} {t.strftime('%B')}"
    n = (t - today).days
    return "That's today." if n == 0 else (f"{n} day{'s' if n != 1 else ''} until {label}." if n > 0 else f"{-n} day{'s' if n != -1 else ''} since {label}.")
