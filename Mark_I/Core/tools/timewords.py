"""Time phrases in a person's own words -> a date range, shared by skills (find_files, email_read).

Sockets
  in : parse_when(text, today=None) -> (start, end) epoch seconds (end exclusive) | None      "yesterday", "last week", "this month", "March", "March 2024", "2023", "last friday", "3 days ago", "last 3 days"
       strip_when(text) -> text without the words parse_when understood (untouched if it holds no time phrase)
  out: pure functions, no I/O.
Self-check: the skills that use it test it (skills/extra/files/find_files/check.py).
"""
import calendar
import datetime
import re
import time

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}


def parse_when(text: str, today: datetime.date | None = None):
    """(start, end) as epoch seconds, or None. end is exclusive."""
    t = (text or "").lower().strip()
    if not t:
        return None
    today = today or datetime.date.today()
    day = lambda d: time.mktime(d.timetuple())
    span = lambda a, b: (day(a), day(b))
    one = datetime.timedelta(days=1)
    if "yesterday" in t:
        return span(today - one, today)
    if re.search(r"\btoday\b|\bthis morning\b|\btonight\b", t):
        return span(today, today + one)
    m = re.search(r"(\d+)\s*(day|week|month)s?\s+ago", t)
    if m:                                                           # "3 days ago" is that day; "2 weeks ago" a week around it
        n, unit = int(m.group(1)), m.group(2)
        centre = today - datetime.timedelta(days=n * {"day": 1, "week": 7, "month": 30}[unit])
        pad = {"day": 0, "week": 3, "month": 7}[unit]
        return span(centre - datetime.timedelta(days=pad), centre + datetime.timedelta(days=pad + 1))
    m = re.search(r"(?:last|past)\s+(\d+)\s*(day|week|month)s?", t)
    if m:
        days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
        return span(today - datetime.timedelta(days=days), today + one)
    if "this week" in t:
        start = today - datetime.timedelta(days=today.weekday())
        return span(start, today + one)
    if "last week" in t:
        start = today - datetime.timedelta(days=today.weekday() + 7)
        return span(start, start + datetime.timedelta(days=7))
    if "this month" in t:
        return span(today.replace(day=1), today + one)
    if "last month" in t:
        first = today.replace(day=1)
        prev = (first - one).replace(day=1)
        return span(prev, first)
    if "this year" in t:
        return span(today.replace(month=1, day=1), today + one)
    if "last year" in t:
        return span(datetime.date(today.year - 1, 1, 1), datetime.date(today.year, 1, 1))
    for name, wd in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            back = (today.weekday() - wd) % 7 or 7
            d = today - datetime.timedelta(days=back)
            return span(d, d + one)
    y = re.search(r"\b(19|20)\d\d\b", t)
    for name, mo in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{name}\b", t):
            year = int(y.group(0)) if y else (today.year if mo <= today.month else today.year - 1)
            first = datetime.date(year, mo, 1)
            nxt = datetime.date(year + (mo == 12), mo % 12 + 1, 1)
            return span(first, nxt)
    if y:
        return span(datetime.date(int(y.group(0)), 1, 1), datetime.date(int(y.group(0)) + 1, 1, 1))
    return None


TIME_WORDS = {"yesterday", "today", "tonight", "morning", "ago", "last", "this", "past", "week", "weeks", "month", "months", "year", "years", "day", "days", "in", "on", "from"} | set(WEEKDAYS) | set(MONTHS)


def strip_when(query: str) -> str:
    """The query without the time phrase that parse_when understood ("invoice from last week" -> "invoice"); untouched when it holds no time phrase."""
    if parse_when(query) is None:
        return query
    return " ".join(w for w in query.split() if re.sub(r"\W", "", w).lower() not in TIME_WORDS and not re.fullmatch(r"\d+|(19|20)\d\d", re.sub(r"\W", "", w)))


