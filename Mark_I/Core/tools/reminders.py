"""Tools: timers and reminders, persisted. Firing them is the L4 scheduler's job (P6); this module owns the store and the time parsing.

Sockets
  in : register(registry) adds set_timer, schedule_reminder, set_alarm, schedule_recurring, list_reminders, cancel_reminder
       Store(path=None).add(kind, text, due) / .due(now) -> [items due and not fired] / .fire(id) / .items() / .cancel(match)    (<data_dir>/reminders.json, outside the repo)
       parse_when(text, now=None) -> datetime | None;   parse_duration(text) -> seconds | None
  out: spoken-style confirmations that state the exact resolved time (so a misheard time is audible at once); a time that cannot be parsed is bad_args, never a guess
parse_when understands: "in 10 minutes", "in an hour and a half", "tomorrow at 9", "5:55 pm", "friday 6pm", "2026-10-09 14:30"; a bare clock time already past today means tomorrow.
Self-check: python tools/reminders.py
"""
import json
import re
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from tools.registry import ToolError, ToolSpec

_UNIT = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "d": 86400, "day": 86400}
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "sixty": 60}
_FULL = r"seconds?|secs?|minutes?|mins?|hours?|hrs?|days?"
_PART = re.compile(rf"(\d+(?:\.\d+)?)\s*({_FULL}|[smhd])(?![a-z])|(?<![a-z-])([a-z-]+)\s+({_FULL})(?![a-z])", re.I)   # "10m" / "10 minutes" / "an hour"; a bare letter unit only after digits
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_duration(text: str):
    """'10 minutes', 'an hour and a half', '1h30m', '90 sec' -> seconds."""
    t = (text or "").lower().replace("and a half", "and 0.5 hours" if "hour" in (text or "").lower() else "and 30 seconds")
    total, hit = 0.0, False
    for n1, u1, n2, u2 in _PART.findall(t):
        n, u = n1 or n2, u1 or u2
        v = float(n) if re.fullmatch(r"\d+(?:\.\d+)?", n) else _NUM.get(n)
        if v is None:
            continue
        total, hit = total + v * _UNIT[u.rstrip("s") if u.rstrip("s") in _UNIT else u[0]], True
    return int(total) if hit and total > 0 else None


def parse_when(text: str, now: datetime | None = None):
    now = now or datetime.now()
    t = (text or "").strip().lower()
    if not t:
        return None
    m = re.match(r"^(?:in|after)\s+(.+)$", t)
    if m and (d := parse_duration(m.group(1))):
        return now + timedelta(seconds=d)
    day, rest = None, t
    if m := re.search(r"\btomorrow\b", t):
        day, rest = (now + timedelta(days=1)).date(), t.replace("tomorrow", " ")
    elif m := re.search(r"\b(?:on |next )?(" + "|".join(_DAYS) + r")\b", t):
        ahead = (_DAYS.index(m.group(1)) - now.weekday()) % 7 or 7
        day, rest = (now + timedelta(days=ahead)).date(), t.replace(m.group(0), " ")
    elif re.search(r"\btoday\b|\btonight\b", t):
        day, rest = now.date(), re.sub(r"\btoday\b|\btonight\b", " ", t)
    rest = re.sub(r"\b(?:at|on|by|around)\b", " ", rest).strip()
    rest = re.sub(r"^(\d{1,2})$", r"\1:00", rest)                                  # a bare "9" is an hour, not a day of the month
    explicit_date =bool(re.search(r"\d{4}-\d{2}-\d{2}|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", rest))
    try:
        from dateutil import parser as dp
        base = datetime.combine(day, datetime.min.time()) if day else now.replace(second=0, microsecond=0)
        if not rest:
            return base.replace(hour=9, minute=0) if day else None                    # a bare day ("friday") means 9 am that day
        dt = dp.parse(rest, default=base.replace(hour=9, minute=0) if day and not re.search(r"\d", rest) else base)
    except (ValueError, OverflowError):
        return None
    if day is None and not explicit_date and dt <= now:
        dt += timedelta(days=1)                                                    # "5:55 pm" said at 6 pm means tomorrow
    return dt


def _say_when(due: datetime, now: datetime) -> str:
    clock = due.strftime("%I:%M %p").lstrip("0")
    if due.date() == now.date():
        return f"today at {clock}"
    if due.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {clock}"
    return f"{due.strftime('%A %d %B')} at {clock}"


REPEATS = ("daily", "weekdays", "weekly", "monthly")


def next_due(due: datetime, repeat: str) -> datetime:
    """The next occurrence after `due` for a repeat rule (same clock time). "weekdays" skips Saturday and Sunday; "monthly" keeps the day, clamped to the month length."""
    if repeat == "daily":
        return due + timedelta(days=1)
    if repeat == "weekly":
        return due + timedelta(days=7)
    if repeat == "weekdays":
        n = due + timedelta(days=1)
        while n.weekday() >= 5:
            n += timedelta(days=1)
        return n
    if repeat == "monthly":
        import calendar
        y, m = due.year + (due.month == 12), due.month % 12 + 1
        return due.replace(year=y, month=m, day=min(due.day, calendar.monthrange(y, m)[1]))
    raise ValueError(repeat)


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else config.data_dir() / "reminders.json"

    def items(self) -> list:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, kind: str, text: str, due: datetime, repeat: str = "") -> dict:
        it = {"id": uuid.uuid4().hex[:8], "kind": kind, "text": text, "due": due.isoformat(timespec="seconds"), "fired": False, "repeat": repeat}
        self._save(self.items() + [it])
        return it

    def due(self, now: datetime | None = None) -> list:
        now = now or datetime.now()
        return [i for i in self.items() if not i["fired"] and datetime.fromisoformat(i["due"]) <= now]

    def fire(self, id: str):
        """Mark fired; a repeating item moves to its next occurrence instead (still pending)."""
        def one(i):
            if i["id"] != id:
                return i
            if i.get("repeat"):
                return {**i, "due": next_due(datetime.fromisoformat(i["due"]), i["repeat"]).isoformat(timespec="seconds")}
            return {**i, "fired": True}
        self._save([one(i) for i in self.items()])

    def pending(self) -> list:
        return sorted((i for i in self.items() if not i["fired"]), key=lambda i: i["due"])

    def cancel(self, match: str):
        """Cancel by id or by words in the text. Several matches -> ToolError so the caller asks which (never cancels a guess)."""
        m = match.strip().lower()
        hits = [i for i in self.pending() if i["id"] == m or (m and m in i["text"].lower())]
        if not hits:
            raise ToolError("not_found", "I don't have a reminder like that.", f"no pending item matches {match!r}")
        if len(hits) > 1:
            raise ToolError("bad_args", "More than one reminder matches. Which one?", "ambiguous: " + "; ".join(f"{i['id']}={i['text']}" for i in hits))
        self._save([i for i in self.items() if i["id"] != hits[0]["id"]])
        return hits[0]


def set_timer(duration: str, label: str = "") -> str:
    s = parse_duration(duration)
    if not s:
        raise ToolError("bad_args", "I didn't catch how long.", f"cannot read a duration from {duration!r}; give e.g. '10 minutes'")
    now = datetime.now()
    Store().add("timer", label or "timer", now + timedelta(seconds=s))
    mins, secs = divmod(s, 60)
    span = (f"{mins} minute{'s' if mins != 1 else ''}" if mins else "") + (f" {secs} seconds" if secs and mins else (f"{secs} seconds" if secs else ""))
    return f"Timer set for {span.strip()}{(' for ' + label) if label else ''}."


def set_reminder(text: str, when: str) -> str:
    now = datetime.now()
    due = parse_when(when, now)
    if due is None:
        raise ToolError("bad_args", "I didn't catch when.", f"cannot read a time from {when!r}; give e.g. 'in 20 minutes', 'tomorrow at 9', 'friday 6pm'")
    Store().add("reminder", text, due)
    return f"Reminder set for {_say_when(due, now)}: {text}."


def set_alarm(when: str, label: str = "") -> str:
    now = datetime.now()
    due = parse_when(when, now)
    if due is None:
        raise ToolError("bad_args", "I didn't catch what time.", f"cannot read a time from {when!r}; give e.g. '6 am tomorrow'")
    Store().add("alarm", label or "alarm", due)
    return f"Alarm set for {_say_when(due, now)}{(': ' + label) if label else ''}."


def schedule_recurring(text: str, when: str, repeat: str) -> str:
    now = datetime.now()
    due = parse_when(when, now)
    if due is None:
        raise ToolError("bad_args", "I didn't catch when.", f"cannot read a time from {when!r}; give the first occurrence, e.g. 'monday 9am'")
    Store().add("reminder", text, due, repeat)
    return f"I'll remind you {repeat}, starting {_say_when(due, now)}: {text}."


def list_reminders() -> str:
    now, p = datetime.now(), Store().pending()
    return "\n".join(f"{i['kind']}: {i['text']} ({_say_when(datetime.fromisoformat(i['due']), now)}{', ' + i['repeat'] if i.get('repeat') else ''})" for i in p) or "You have no reminders or timers."


def cancel_reminder(match: str) -> str:
    return f"Cancelled: {Store().cancel(match)['text']}."


def register(reg):
    cues = (r"every (?:day|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|evening)", r"daily", r"weekly", r"remind(?:er)?s?", r"timers?", r"alarm", r"don't let me forget", r"wake me", r"in \d+ (?:seconds?|minutes?|hours?)", r"yaad dila\w*", r"yaad rakh\w*")
    reg.register(ToolSpec("set_timer", set_timer, {"type": "object", "required": ["duration"], "properties": {"duration": {"type": "string", "description": "e.g. '10 minutes'"}, "label": {"type": "string"}}},
                          "Start a countdown timer.", group="reminders", say=True, cues=cues, examples=("set a timer for five minutes", "timer ten minutes for the pasta")))
    reg.register(ToolSpec("schedule_reminder", set_reminder, {"type": "object", "required": ["text", "when"], "properties": {
        "text": {"type": "string", "description": "what to remind about"}, "when": {"type": "string", "description": "e.g. 'in 20 minutes', 'tomorrow at 9am', 'friday 6pm', '2026-10-09 14:30'"}}},
        "Remind the user of something at a time. Use for every 'remind me ...' request, one call per reminder.", group="reminders", say=True, cues=cues, examples=("remind me to call the dentist tomorrow at nine", "remind me in twenty minutes", "remind me at 7:45 pm to take my meds", "mujhe 5 baje yaad dilana ki gym jaana hai")))
    reg.register(ToolSpec("set_alarm", set_alarm, {"type": "object", "required": ["when"], "properties": {"when": {"type": "string", "description": "e.g. '6 am tomorrow', '7:30'"}, "label": {"type": "string"}}},
                          "Set an ALARM (wake-up / clock alarm) for a time. Only when the user says alarm or wake me; for 'remind me ...' use schedule_reminder.", group="reminders", say=True, cues=cues, examples=("set an alarm for six am", "wake me up at seven")))
    reg.register(ToolSpec("schedule_recurring", schedule_recurring, {"type": "object", "required": ["text", "when", "repeat"], "properties": {"text": {"type": "string"}, "when": {"type": "string", "description": "the FIRST occurrence, e.g. 'monday 9am'"}, "repeat": {"type": "string", "enum": list(REPEATS)}}},
                          "A reminder that repeats: every day, every weekday, every week, every month.", group="reminders", say=True, cues=cues, examples=("remind me every monday at nine to submit the timesheet", "every morning remind me to take my pills")))
    reg.register(ToolSpec("list_reminders", list_reminders, description="List pending reminders and timers.", group="reminders", cues=cues, examples=("what reminders do I have", "any timers running")))
    reg.register(ToolSpec("cancel_reminder", cancel_reminder, {"type": "object", "required": ["match"], "properties": {"match": {"type": "string", "description": "words from the reminder, or its id"}}},
                          "Cancel a pending reminder or timer by words from it.", group="reminders", say=True, cues=cues, examples=("cancel the dentist reminder", "stop the timer")))


def _selfcheck():
    import tempfile
    N = datetime(2026, 10, 6, 18, 0)                                                # a Tuesday, 6 pm
    assert parse_duration("10 minutes") == 600 and parse_duration("an hour and a half") == 5400 and parse_duration("1h30m") == 5400 and parse_duration("90 sec") == 90 and parse_duration("soon") is None
    assert parse_duration("two minutes and 30 seconds") == 150
    assert parse_when("in 20 minutes", N) == N + timedelta(minutes=20)
    assert parse_when("tomorrow at 9", N) == datetime(2026, 10, 7, 9, 0) and parse_when("tomorrow 9:30 am", N) == datetime(2026, 10, 7, 9, 30)
    assert parse_when("5:55 pm", N) == datetime(2026, 10, 7, 17, 55)                # already past today -> tomorrow
    assert parse_when("8 pm", N) == datetime(2026, 10, 6, 20, 0)
    assert parse_when("friday 6pm", N) == datetime(2026, 10, 9, 18, 0) and parse_when("on tuesday at 10am", N) == datetime(2026, 10, 13, 10, 0)
    assert parse_when("2026-10-09 14:30", N) == datetime(2026, 10, 9, 14, 30)
    assert parse_when("friday", N) == datetime(2026, 10, 9, 9, 0) and parse_when("tomorrow", N) == datetime(2026, 10, 7, 9, 0)
    assert parse_when("whenever", N) is None and parse_when("", N) is None
    with tempfile.TemporaryDirectory() as t:
        real = config.data_dir()
        config.set_data_dir(t)
        s = Store()
        a = s.add("reminder", "call the dentist", N)
        s.add("reminder", "call the plumber", N + timedelta(days=1))
        assert [i["id"] for i in s.due(N)] == [a["id"]] and not s.due(N - timedelta(hours=1))
        s.fire(a["id"])
        assert not s.due(N) and len(s.pending()) == 1
        w = s.add("reminder", "timesheet", datetime(2026, 10, 12, 9, 0), "weekly")
        s.fire(w["id"])                                                               # a repeating item moves on, it is not done
        assert [i["due"] for i in s.pending() if i["id"] == w["id"]] == ["2026-10-19T09:00:00"]
        s.cancel("timesheet")
        assert next_due(datetime(2026, 10, 9, 9, 0), "weekdays") == datetime(2026, 10, 12, 9, 0) and next_due(datetime(2026, 12, 31, 8, 0), "monthly") == datetime(2027, 1, 31, 8, 0)
        assert next_due(datetime(2026, 1, 31, 8, 0), "monthly") == datetime(2026, 2, 28, 8, 0) and next_due(datetime(2026, 10, 6, 8, 0), "daily").day == 7
        try:
            s.add("reminder", "call mum", N)
            s.cancel("call")
            raise AssertionError
        except ToolError as ex:
            assert ex.kind == "bad_args"                                              # ambiguous: asks, never guesses
        assert s.cancel("plumber")["text"] == "call the plumber" and len(s.pending()) == 1
        from tools.registry import Registry
        r = Registry()
        register(r)
        assert "5 minutes" in r.call("set_timer", {"duration": "5 minutes", "label": "pasta"}).data
        assert r.call("set_timer", {"duration": "soon"}).error_kind == "bad_args" and r.call("schedule_reminder", {"text": "x", "when": "whenever"}).error_kind == "bad_args"
        assert "Reminder set for" in r.call("schedule_reminder", {"text": "stretch", "when": "in 2 hours"}).data
        assert "pasta" in r.call("list_reminders").data and r.call("cancel_reminder", {"match": "pasta"}).ok and r.call("cancel_reminder", {"match": "nothing"}).error_kind == "not_found"
        config.set_data_dir(real)
    print("reminders ok")


if __name__ == "__main__":
    _selfcheck()
