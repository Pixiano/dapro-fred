"""Deadline watch: notices a deadline on the screen, asks once, and on a yes reminds the evening before and on the day. Plain code, no model.

Finder: a cue word (due, deadline, submit by, last date, closes, ends, expires, exam, test, quiz, assignment ...) within 6 words of a date phrase (14 Oct, Oct 14th, 14/10 read
day-first, 2026-10-14, 14-10-2026, tomorrow, tonight, today, Friday, next Monday, in 3 days, in 5 hours) on the same line or the next. A time (11:59 pm, 17:30, noon, midnight, EOD) in
the same place sets the hour; a date without a time means 23:59. A date in the past is dropped; a date without a year that has already passed this year rolls to next year.

Sockets
  in : find_deadlines(text, now) -> [Candidate(line, due, key)]                          pure; now = aware datetime (config.IST)
       DeadlineWatch(bus, reader, speak, gate, notify=None, memory=None, clock=time.time, path=None, now=None).start() / stop() / tick()
         speak(text) says a line;  gate = proactive.gate.Gate;  notify(title, text) = toast (default ui.toast.show; it has no buttons, so the text says "say yes");  memory = manager.Memory or None
         reader.add(fn): the foreground window's text arrives through proactive.screenread.Reader (not while the pause switch is on)
       answer(text) -> reply | None     a yes/no within deadline_answer_s of the question: handled here and the reply to speak returned, not routed to the model
  out: a spoken question + a toast; each distinct deadline (key) is asked about ONCE, ever (the answer is remembered across restarts in <data_dir>/deadlines.json)
       on yes: reminders 18:00 the evening before and 06:00 on the day (12:00 on a weekend), spoken/toasted through the speak gate (a deadline under deadline_urgent_h away overrides
       quiet hours and the caps); a memory event "deadline" (importance 0.6) when memory is given
       log.session carries counts and keys only, never the screen text
Self-check: python proactive/deadlines.py (invented lines, fake clock, speaker and toast)
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log
from proactive import gate as gatemod

IST = config.IST
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
DAYS = "monday tuesday wednesday thursday friday saturday sunday".split()
_MON = lambda n: rf"(?P<{n}>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DEV = re.compile(r"claude|powershell|terminal|command prompt|git bash|cmd\.exe|visual studio|vs ?code|pycharm|cursor|FRED", re.I)     # windows full of code and FRED's own lines: never deadlines
ECHO = re.compile(r"deadline on your screen|shall i remind", re.I)                                                                      # FRED's own offer, seen again on screen
CUE = re.compile(r"\b(due|deadline|submit(?:ted)?(?: by)?|submission|hand[- ]?in|last date|closes?|closing|ends?|expires?|exams?|tests?|quiz|midterm|assignment|before)\b", re.I)
DATE = re.compile(
    rf"(?P<iso>\b(?P<iy>\d{{4}})-(?P<im>\d\d)-(?P<id>\d\d)\b)"
    rf"|(?P<dmy>\b(?P<dd>\d{{1,2}})(?:st|nd|rd|th)?[ -]{_MON('dm')}(?:,?\s+(?P<dy>\d{{4}}))?\b)"
    rf"|(?P<mdy>\b{_MON('mm')}\s+(?P<md>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<my>\d{{4}}))?)"
    r"|(?P<num>\b(?P<nd>\d{1,2})/(?P<nm>\d{1,2})(?:/(?P<ny>\d{2,4}))?\b|\b(?P<ad>\d{1,2})-(?P<am>\d{1,2})-(?P<ay>\d{2,4})\b)"
    r"|(?P<rel>\b(?:tomorrow|tonight|today)\b)"
    r"|(?P<wd>\b(?:(?:next|this|on)\s+)?(?P<wn>mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:rs?|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b)"
    r"|(?P<inn>\bin\s+(?P<n>\d{1,3}|a|an|one|two|three|four|five|six|seven|ten)\s+(?P<u>hours?|days?|weeks?)\b)", re.I)
TIME = re.compile(r"\b(?:(?P<h>\d{1,2})(?::(?P<m>\d\d))?\s*(?P<ap>[ap]\.?m\.?)|(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)\b|(?P<w>noon|midnight|eod|end of (?:the )?day))", re.I)
WORDN = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10}


@dataclass
class Candidate:
    line: str
    due: datetime
    key: str


def _clock(m, d):
    """The time in a TIME match applied to date d; None when the match is not a plausible time."""
    if m.group("w"):
        w = m.group("w").lower()
        return d.replace(hour=12 if w == "noon" else 17 if w == "eod" or w.startswith("end") else 23, minute=59 if w == "midnight" else 0, second=0)
    if m.group("ap"):
        h, mi = int(m.group("h")), int(m.group("m") or 0)
        if not (1 <= h <= 12 and mi < 60):
            return None
        return d.replace(hour=h % 12 + (12 if m.group("ap").lower().startswith("p") else 0), minute=mi, second=0)
    return d.replace(hour=int(m.group("h24")), minute=int(m.group("m24")), second=0)


def _date(m, now):
    """(datetime at 23:59, has_year_rolled, explicit_time) for a DATE match, or None; relative forms may carry an hour already."""
    day = now.replace(hour=23, minute=59, second=0, microsecond=0)
    try:
        if m.group("iso"):
            return day.replace(year=int(m.group("iy")), month=int(m.group("im")), day=int(m.group("id"))), False
        if m.group("dd") or m.group("md"):
            dd, mon, yy = ("dd", "dm", "dy") if m.group("dd") else ("md", "mm", "my")
            year = m.group(yy)
            return day.replace(year=int(year or now.year), month=MONTHS[m.group(mon).lower()[:3]], day=int(m.group(dd))), not year
        if m.group("nd"):
            d = day.replace(year=int(m.group("ny") or now.year) + (2000 if m.group("ny") and len(m.group("ny")) == 2 else 0), month=int(m.group("nm")), day=int(m.group("nd")))
            return d, not m.group("ny")
        if m.group("ad"):
            y = int(m.group("ay"))
            return day.replace(year=y + (2000 if y < 100 else 0), month=int(m.group("am")), day=int(m.group("ad"))), False
        if m.group("rel"):
            return (day + timedelta(days=1) if m.group("rel").lower() == "tomorrow" else day), False
        if m.group("wn"):
            ahead = ([d[:3] for d in DAYS].index(m.group("wn").lower()[:3]) - now.weekday()) % 7
            if ahead == 0 and m.group("wd").lower().startswith("next"):
                ahead = 7
            return day + timedelta(days=ahead), False
        if m.group("inn"):
            n = int(m.group("n")) if m.group("n").isdigit() else WORDN[m.group("n").lower()]
            u = m.group("u").lower()
            if u.startswith("hour"):
                return (now + timedelta(hours=n)).replace(second=0, microsecond=0), False
            return day + timedelta(days=n * (7 if u.startswith("week") else 1)), False
    except (ValueError, KeyError):
        return None
    return None


def find_deadlines(text: str, now: datetime) -> list:
    out, seen = [], set()
    lines = [l.strip() for l in (text or "")[:6000].split("\n") if l.strip() and not ECHO.search(l)]
    for i, line in enumerate(lines):
        for scope in (line, line + " " + lines[i + 1] if i + 1 < len(lines) and CUE.search(line) and not DATE.search(line) else None):      # a cue line without a date may take its date from the next line
            if not scope:
                continue
            cues = [m for m in CUE.finditer(scope)]
            ms = list(DATE.finditer(scope))
            for k, m in enumerate(ms):
                if m.group("wn") and k + 1 < len(ms) and ms[k + 1].start() - m.end() <= 3:
                    continue                                                                  # "Fri 17 Oct": the date says it, the weekday is only a label
                if not any(len(scope[min(c.end(), m.end()):max(c.start(), m.start())].split()) <= 6 and (c.end() <= m.start() or m.end() <= c.start()) for c in cues):
                    continue
                r = _date(m, now)
                if not r:
                    continue
                due, rolled = r
                if m.group("inn") and m.group("u").lower().startswith("hour"):
                    pass
                else:
                    tm = TIME.search(scope[m.end():m.end() + 30]) or TIME.search(scope[max(0, m.start() - 20):m.start()])
                    t = tm and _clock(tm, due)
                    due = t or due
                if rolled and due < now and due + timedelta(days=366) - now < timedelta(days=90) :
                    due = due.replace(year=due.year + 1)
                if due <= now:
                    continue
                shown = line[:140]
                norm = re.sub(r"[^a-z0-9]+", " ", (scope[:m.start()] + scope[m.end():]).lower()).strip()
                key = hashlib.sha1(f"{norm[:120]}|{due.isoformat()}".encode()).hexdigest()[:16]
                if key not in seen:
                    seen.add(key)
                    out.append(Candidate(shown, due, key))
    return sorted(out, key=lambda c: c.due)


def reminder_times(due: datetime, now: datetime) -> list:
    """[(when, label)] still in the future and before the deadline: 18:00 the evening before, 06:00 on the day (12:00 on a weekend)."""
    eve = (due - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    day = due.replace(hour=12 if due.weekday() >= 5 else 6, minute=0, second=0, microsecond=0)
    return [(t, l) for t, l in ((eve, "the evening before"), (day, "on the day")) if now < t < due]


def _say(dt: datetime) -> str:
    return f"{dt:%A} {dt.day} {dt:%B}" + (f" at {dt:%I:%M %p}".replace(" 0", " ").replace(":00", "") if (dt.hour, dt.minute) != (23, 59) else "")


class DeadlineWatch:
    def __init__(self, bus, reader, speak, gate, notify=None, memory=None, clock=time.time, path=None, now=None):
        self.bus, self.reader, self.speak, self.gate, self._notify, self.mem, self.clock = bus, reader, speak, gate, notify, memory, clock
        self._now = now or (lambda: datetime.fromtimestamp(self.clock(), IST))
        self.path = Path(path) if path else config.data_dir() / "deadlines.json"
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.state = {}
        self.state.setdefault("asked", {})
        self.state.setdefault("reminders", [])
        self.pending: dict = {}                               # key -> Candidate seen but not asked yet
        self.cur, self.cur_until = None, 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._t = None

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        os.replace(tmp, self.path)

    def toast(self, title, text):
        try:
            if self._notify is None:
                from ui import toast
                self._notify = toast.show
            self._notify(title, text)
        except Exception as ex:
            log.session("deadline_toast_error", error=type(ex).__name__)

    def on_reading(self, text, info=None):
        if not config.get().deadline_watch_enabled or DEV.search((info or {}).get("title") or ""):
            return
        with self._lock:
            for c in find_deadlines(text, self._now()):
                if c.key not in self.state["asked"] and c.key not in self.pending:
                    self.pending[c.key] = c
        self.tick()

    def _urgent(self, due_ts) -> bool:
        return due_ts - self.clock() < config.get().deadline_urgent_h * 3600

    def tick(self):
        with self._lock:
            now = self.clock()
            if self.cur and now > self.cur_until:
                self.cur = None
            self.pending = {k: c for k, c in self.pending.items() if c.due.timestamp() > now}
            if self.pending and not self.cur:                                          # one question at a time, the nearest deadline first
                c = min(self.pending.values(), key=lambda c: c.due)
                act = self.gate.decide(urgent=self._urgent(c.due.timestamp()))
                if act != "hold":
                    del self.pending[c.key]
                    self.state["asked"][c.key] = {"state": "asked", "due": c.due.isoformat(), "day": self._now().date().isoformat()}
                    self.cur, self.cur_until = c, now + config.get().deadline_answer_s
                    self._save()
                    q = f"I see a deadline on your screen: {c.line}. That is {_say(c.due)}. Shall I remind you?"
                    log.session("deadline_offer", key=c.key, how=act)
                    self.toast("Deadline on screen", f"{c.line[:120]}\n{_say(c.due)}. Say yes to be reminded.")
                    if act == "speak":
                        self.gate.note_spoken()
                        self.speak(q)
            keep, fire = [], []
            for r in self.state["reminders"]:
                (fire if r["at"] <= now else keep).append(r)
            fired = False
            for r in sorted(fire, key=lambda r: r["at"]):
                if r["due"] < now - 3600:
                    fired = True                                                     # long gone
                    continue
                act = self.gate.decide(urgent=self._urgent(r["due"]))
                if act == "hold":
                    keep.append(r)
                    continue
                fired = True
                text = ("Sorry, I missed this one. " if now - r["at"] > 300 else "") + f"Reminder: {r['line']}. It is due {_say(datetime.fromtimestamp(r['due'], IST))}."
                self.toast("Deadline reminder", text)
                if act == "speak":
                    self.gate.note_spoken()
                    self.speak(text)
            if fired:
                self.state["reminders"] = keep
                self._save()

    def answer(self, text: str):
        with self._lock:
            if not self.cur or self.clock() > self.cur_until:
                return None
            t = re.sub(r"[^a-z ]", "", text.lower()).strip()
            yes = re.match(r"^(yes|yeah|yep|yup|sure|ok|okay|please|go ahead|do it|why not|remind me)\b", t)
            no = re.match(r"^(no|nope|not now|no thanks|skip|don'?t|dont|never mind)\b", t)
            if not (yes or no):
                return None
            c, self.cur = self.cur, None
            self.state["asked"][c.key]["state"] = "accepted" if yes else "declined"
            if not yes:
                self._save()
                return "Okay, I won't remind you."
            now = self._now()
            times = reminder_times(c.due, now)
            if not times and c.due - now > timedelta(hours=2):
                times = [(now + timedelta(hours=1), "in an hour")]
            for t_, label in times:
                self.state["reminders"].append({"at": t_.timestamp(), "line": c.line, "due": c.due.timestamp(), "key": c.key})
            self._save()
            if self.mem is not None:
                try:
                    self.mem.store.add_event("deadline", f"Deadline noted from the screen: {c.line} (due {c.due:%d %b %Y %H:%M})", importance=0.6, ts=now.isoformat())
                except Exception as ex:
                    log.session("deadline_memory_error", error=type(ex).__name__)
            if not times:
                return "Done, but it is due too soon for a reminder; consider it noted."
            return "Done. I'll remind you " + " and ".join(f"{label} ({t_:%I:%M %p}".replace("(0", "(") + ")" for t_, label in times) + "."

    def _loop(self):
        while not self._stop.wait(5.0):
            try:
                self.tick()
            except Exception as ex:
                log.session("deadline_tick_error", error=type(ex).__name__)

    def start(self):
        if not config.get().deadline_watch_enabled or self._t:
            return
        self.reader.add(self.on_reading)
        self._t = threading.Thread(target=self._loop, daemon=True, name="deadlines")
        self._t.start()

    def stop(self):
        self._stop.set()


def _selfcheck():
    import tempfile
    import events as ev
    config.set_data_dir(tempfile.mkdtemp())
    now = datetime(2026, 10, 12, 14, 0, tzinfo=IST)                                     # a Monday
    D = lambda *a: datetime(*a, tzinfo=IST)
    pos = [
        ("Lab report 3 due Fri 17 Oct 11:59 pm", D(2026, 10, 17, 23, 59)),
        ("Assignment 2 is due on Friday", D(2026, 10, 16, 23, 59)),
        ("Submissions close tomorrow", D(2026, 10, 13, 23, 59)),
        ("Offer ends Sunday", D(2026, 10, 18, 23, 59)),
        ("Deadline: 20 October 2026, 5:30 pm", D(2026, 10, 20, 17, 30)),
        ("Deadline: October 20th", D(2026, 10, 20, 23, 59)),
        ("Submit by 14/10", D(2026, 10, 14, 23, 59)),
        ("Last date to apply: 2026-11-02", D(2026, 11, 2, 23, 59)),
        ("Essay due 03-11-2026 at 09:00", D(2026, 11, 3, 9, 0)),
        ("Chemistry test in 3 days", D(2026, 10, 15, 23, 59)),
        ("Quiz in 2 hours", D(2026, 10, 12, 16, 0)),
        ("Exam on next Monday", D(2026, 10, 19, 23, 59)),
        ("Project due today 6 pm", D(2026, 10, 12, 18, 0)),
        ("Project due tonight", D(2026, 10, 12, 23, 59)),
        ("Registration closes 5 Jan", D(2027, 1, 5, 23, 59)),                           # no year, already past this year -> next year (under 90 days)
        ("Hand in by Tue noon", D(2026, 10, 13, 12, 0)),
        ("Math assignment\nDue: 22 Oct, 11:59 PM", D(2026, 10, 22, 23, 59)),               # cue and date on neighbouring lines
        ("Physics exam on 1st Nov", D(2026, 11, 1, 23, 59)),
        ("Submission closes midnight tomorrow", D(2026, 10, 13, 23, 59)),
        ("Task is due by Wed", D(2026, 10, 14, 23, 59)),
    ]
    for t, due in pos:
        got = find_deadlines(t, now)
        assert got and got[0].due == due, (t, got)
    neg = [
        "Posted 14/10", "Posted on Tuesday by the admin", "Meeting on Friday at 3 pm", "Birthday party tomorrow",
        "Assignment due 10 Oct 2026", "Test results published yesterday", "Chapter 12 test paper, pages 10-12",
        "The latest news on 17 Oct", "Offer ends 3 Jan 2020", "Due soon", "Submit your homework",
        "Weather today: sunny, 24C", "It is due in a long time and the date is Friday ahead of many other words in between us", "Version 2.3.1 released",
        "Ratio 3/4 of the test group", "Call me when you finish", "Terms ended in 2019", "", "Total due: 1,840 on 32/13",
    ]
    for t in neg:
        assert not find_deadlines(t, now), (t, find_deadlines(t, now))
    a, b = find_deadlines("Lab 3 due in 3 days", now)[0], find_deadlines("Lab 3 due in 2 days", now + timedelta(days=1))[0]
    assert a.key == b.key and a.key != find_deadlines("Lab 4 due in 3 days", now)[0].key                    # same deadline seen on another day: one key
    assert [c.line for c in find_deadlines("Lab due Fri\nEssay due Thu", now)] == ["Essay due Thu", "Lab due Fri"]
    assert [t for t, _ in reminder_times(D(2026, 10, 14, 23, 59), now)] == [D(2026, 10, 13, 18, 0), D(2026, 10, 14, 6, 0)]
    assert [t for t, _ in reminder_times(D(2026, 10, 17, 23, 59), now)] == [D(2026, 10, 16, 18, 0), D(2026, 10, 17, 12, 0)]       # Saturday: noon
    assert [t for t, _ in reminder_times(D(2026, 10, 13, 9, 0), D(2026, 10, 12, 19, 0))] == [D(2026, 10, 13, 6, 0)]               # the evening one is already past
    assert reminder_times(D(2026, 10, 13, 9, 0), D(2026, 10, 13, 8, 0)) == []

    # service
    clk = [now.timestamp()]
    bus, said, toasts, mem_ev = ev.EventBus(), [], [], []
    g = gatemod.Gate(bus, clock=lambda: clk[0], now=lambda: datetime.fromtimestamp(clk[0], IST))
    mem = type("M", (), {"store": type("S", (), {"add_event": staticmethod(lambda *a, **k: mem_ev.append((a, k)))})()})()
    path = Path(tempfile.mkdtemp()) / "d.json"
    mk = lambda: DeadlineWatch(bus, type("R", (), {"add": lambda s, fn: None})(), said.append, g, lambda t, x: toasts.append((t, x)), mem, lambda: clk[0], path)
    w = mk()
    w.on_reading("Physics lab due Sat 17 Oct 11:59 pm\nweek 3", {})
    assert len(said) == 1 and "Physics lab due Sat 17 Oct 11:59 pm" in said[0] and "Saturday 17 October" in said[0] and len(toasts) == 1
    w.on_reading("Physics lab due Sat 17 Oct 11:59 pm", {})
    assert len(said) == 1                                                                   # asked once
    assert w.answer("what is the weather") is None and w.answer("yes please") .startswith("Done. I'll remind you")
    assert w.answer("yes") is None                                                          # one answer per question
    assert [(datetime.fromtimestamp(r["at"], IST).strftime("%a %H:%M")) for r in w.state["reminders"]] == ["Fri 18:00", "Sat 12:00"]
    assert len(w.state["reminders"]) == 2 and mem_ev and mem_ev[0][0][0] == "deadline" and mem_ev[0][1]["importance"] == 0.6
    w2 = mk()                                                                               # restart: remembered
    w2.on_reading("Physics lab due Sat 17 Oct 11:59 pm", {})
    assert len(said) == 1 and len(w2.state["reminders"]) == 2
    clk[0] = D(2026, 10, 16, 18, 1).timestamp()
    g.spoke_at = -1e12
    w2.tick()                                                                               # evening before: spoken
    assert len(said) == 2 and said[1].startswith("Reminder: Physics lab") and len(w2.state["reminders"]) == 1
    # a no is final; an unanswered question expires
    clk[0] = now.timestamp() + 5000
    g.offers.clear()
    w3 = mk()
    w3.on_reading("Chemistry test due Thu 15 Oct", {})
    assert len(said) == 3 and w3.answer("No thanks") == "Okay, I won't remind you."
    clk[0] += 100
    w3.on_reading("Chemistry test due Thu 15 Oct", {})
    assert len(said) == 3 and not w3.pending
    # holds: user talking -> nothing asked until free
    w4 = mk()
    bus.emit("audio", "vad", speaking=True)
    clk[0] += 1000
    w4.on_reading("Econ essay due Wed 14 Oct", {})
    assert len(said) == 3 and len(w4.pending) == 1
    bus.emit("audio", "vad", speaking=False)
    w4.tick()
    assert len(said) == 4 and w4.answer("Sure") .startswith("Done")
    # due within hours: only the "in an hour" fallback
    clk[0] = D(2026, 10, 14, 20, 0).timestamp()
    w5 = mk()
    w5.state["reminders"] = []                                                              # (the earlier ones are not under test here)
    g.offers.clear(); g.spoke_at = -1e12
    w5.on_reading("Bio quiz due tomorrow 6 am", {})
    assert len(said) == 5 and w5.cur and "in an hour" in w5.answer("yes")
    # quiet hours: a toast only, but a deadline under 12 h away is spoken anyway
    clk[0] = D(2026, 10, 14, 23, 50).timestamp()
    g.offers.clear(); g.spoke_at = -1e12; g.active_at = -1e12
    w6 = mk()
    w6.state["reminders"] = []
    w6.on_reading("Art project due Mon 2 Nov", {})
    assert len(said) == 5 and len(toasts) >= 5
    clk[0] += 91                                                                            # (one question at a time: the toast-only one expired)
    w6.on_reading("Maths paper due tomorrow 8 am", {})
    assert len(said) == 6 and "Maths paper" in said[5]
    # the answer window closes
    clk[0] += 91
    assert w6.answer("yes") is None
    config.reload()
    print("deadlines ok")


if __name__ == "__main__":
    _selfcheck()
