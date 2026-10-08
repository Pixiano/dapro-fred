"""Tools: the agenda (tasks, homework, events, goals) with due dates, kept in <data_dir>/agenda.json (outside the repo).

Sockets
  in : register(registry) adds add_agenda_item, list_agenda_items, complete_agenda_item, delete_agenda_item (destructive)
       Agenda(path=None).add(kind, subject, due, detail) / .items(when=None, include_done=False) / .complete(match) / .delete(match) / .open_due_within(days)   (L4's morning check reads these)
  out: spoken-style strings; a match that is ambiguous raises bad_args (never completes or deletes a guess); an unreadable due date is bad_args, never "no date"
Kinds: task, homework, event, goal. `when` filters for a list: today, tomorrow, this week, overdue, or a date phrase parse_when understands. Due dates reuse tools/reminders.parse_when.
The earlier FRED kept its agenda as markdown in the vault; this is a fresh store (a migration can import those lines later; nothing is read from the vault here).
Self-check: python tools/agenda.py
"""
import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from tools.registry import ToolError, ToolSpec
from tools.reminders import parse_when

KINDS = ("task", "homework", "event", "goal")


def _day(d: datetime) -> str:
    return d.strftime("%a %d %b")


class Agenda:
    def __init__(self, path=None):
        self.path = Path(path) if path else config.data_dir() / "agenda.json"

    def _all(self) -> list:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, kind: str, subject: str, due: datetime | None, detail: str = "") -> dict:
        it = {"id": uuid.uuid4().hex[:8], "kind": kind, "subject": subject.strip(), "detail": detail, "due": due.isoformat(timespec="minutes") if due else None, "done": False}
        self._save(self._all() + [it])
        return it

    def items(self, when: str = "", now: datetime | None = None, include_done: bool = False) -> list:
        now, w = now or datetime.now(), (when or "").strip().lower()
        out = [i for i in self._all() if include_done or not i["done"]]
        if w:
            today = now.date()
            if w == "overdue":
                out = [i for i in out if i["due"] and datetime.fromisoformat(i["due"]).date() < today]
            elif w in ("this week", "week", "next 7 days"):             # "this week" runs through Sunday; "next 7 days" is a rolling window
                end = today + timedelta(days=7 if w == "next 7 days" else 6 - today.weekday())
                out = [i for i in out if i["due"] and today <= datetime.fromisoformat(i["due"]).date() <= end]
            else:
                d = parse_when(w, now)
                if d is None:
                    raise ToolError("bad_args", "I didn't catch which day.", f"cannot read a day from {when!r}; try today, tomorrow, this week, overdue or a date")
                out = [i for i in out if i["due"] and datetime.fromisoformat(i["due"]).date() == d.date()]
        return sorted(out, key=lambda i: (i["due"] is None, i["due"] or ""))

    def _one(self, match: str) -> dict:
        m = match.strip().lower()
        hits = [i for i in self._all() if not i["done"] and (i["id"] == m or (m and m in i["subject"].lower()))]
        if not hits:
            raise ToolError("not_found", "I don't have an agenda item like that.", f"no open item matches {match!r}")
        if len(hits) > 1:
            raise ToolError("bad_args", "More than one item matches. Which one?", "ambiguous: " + "; ".join(f"{i['id']}={i['subject']}" for i in hits))
        return hits[0]

    def complete(self, match: str) -> dict:
        it = self._one(match)
        self._save([{**i, "done": True} if i["id"] == it["id"] else i for i in self._all()])
        return it

    def delete(self, match: str) -> dict:
        it = self._one(match)
        self._save([i for i in self._all() if i["id"] != it["id"]])
        return it

    def open_due_within(self, days: int, now: datetime | None = None) -> list:
        now = now or datetime.now()
        return [i for i in self.items(now=now) if i["due"] and datetime.fromisoformat(i["due"]) <= now + timedelta(days=days)]


def _line(i: dict) -> str:
    return f"{i['subject']} ({i['kind']}{', due ' + _day(datetime.fromisoformat(i['due'])) if i['due'] else ''})"


def add_agenda_item(subject: str, due: str = "", kind: str = "task", detail: str = "") -> str:
    d = None
    if due.strip():
        d = parse_when(due)
        if d is None:
            raise ToolError("bad_args", "I didn't catch the due date.", f"cannot read a date from {due!r}; give e.g. 'friday', 'tomorrow 5pm', '2026-10-12'")
    it = Agenda().add(kind if kind in KINDS else "task", subject, d, detail)
    return f"Added {_line(it)}."


def list_agenda_items(when: str = "") -> str:
    rows = Agenda().items(when)
    return "\n".join(_line(i) for i in rows) or ("Nothing on your agenda" + (f" for {when}." if when else "."))


def complete_agenda_item(match: str) -> str:
    return f"Marked done: {Agenda().complete(match)['subject']}."


def delete_agenda_item(match: str) -> str:
    return f"Removed: {Agenda().delete(match)['subject']}."


def register(reg):
    cues = (r"agenda", r"homework", r"to-?do", r"my tasks?", r"due (?:on |by )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today)", r"deadline")
    reg.register(ToolSpec("add_agenda_item", add_agenda_item, {"type": "object", "required": ["subject"], "properties": {"subject": {"type": "string"}, "due": {"type": "string", "description": "e.g. 'friday', 'tomorrow 5pm', '2026-10-12'"},
                          "kind": {"type": "string", "enum": list(KINDS), "default": "task"}, "detail": {"type": "string"}}}, "Add a task, homework, event or goal to the agenda.", group="agenda", say=True, cues=cues,
                          examples=("add physics homework due friday to my agenda", "put the dentist on my agenda for tuesday")))
    reg.register(ToolSpec("list_agenda_items", list_agenda_items, {"type": "object", "properties": {"when": {"type": "string", "description": "today, tomorrow, this week, overdue, or a date; empty = everything open"}}},
                          "List open agenda items, optionally for one day or period.", group="agenda", cues=cues, examples=("what's on my agenda this week", "what's due tomorrow")))
    reg.register(ToolSpec("complete_agenda_item", complete_agenda_item, {"type": "object", "required": ["match"], "properties": {"match": {"type": "string", "description": "words from the item"}}},
                          "Mark an agenda item done.", group="agenda", say=True, cues=cues + (r"(?:finished|done with|completed)",), examples=("I finished the physics homework", "mark the dentist as done")))
    reg.register(ToolSpec("delete_agenda_item", delete_agenda_item, {"type": "object", "required": ["match"], "properties": {"match": {"type": "string"}}},
                          "Remove an agenda item entirely (asks the user first).", group="agenda", destructive=True, say=True, cues=cues, examples=("remove the dentist from my agenda",)))


def _selfcheck():
    import tempfile
    N = datetime(2026, 10, 6, 18, 0)                                                   # a Tuesday
    with tempfile.TemporaryDirectory() as t:
        a = Agenda(Path(t) / "agenda.json")
        a.add("homework", "physics homework", datetime(2026, 10, 9, 17, 0))
        a.add("task", "call the plumber", datetime(2026, 10, 6, 9, 0))
        a.add("event", "dentist", datetime(2026, 10, 13, 16, 0))
        a.add("goal", "read more", None)
        assert [i["subject"] for i in a.items(now=N)] == ["call the plumber", "physics homework", "dentist", "read more"]          # dated first, by date; undated last
        a.add("task", "pay the bill", datetime(2026, 10, 5, 12, 0))
        assert [i["subject"] for i in a.items("overdue", N)] == ["pay the bill"]                                  # due today is not overdue until the day has passed
        assert [i["subject"] for i in a.items("this week", N) if i["subject"] != "pay the bill"] == ["call the plumber", "physics homework"]      # through Sunday: the dentist (13 Oct) is next week
        a.complete("pay the bill")
        assert [i["subject"] for i in a.items("friday", N)] == ["physics homework"] and [i["subject"] for i in a.items("2026-10-13", N)] == ["dentist"]
        try:
            a.items("whenever", N)
            raise AssertionError
        except ToolError as ex:
            assert ex.kind == "bad_args"
        assert [i["subject"] for i in a.open_due_within(3, N)] == ["call the plumber", "physics homework"]
        try:
            a.add("task", "call mum", None)
            a.complete("call")
            raise AssertionError
        except ToolError as ex:
            assert ex.kind == "bad_args"                                                  # two matches: asks, never completes a guess
        assert a.complete("plumber")["subject"] == "call the plumber" and "call the plumber" not in [i["subject"] for i in a.items(now=N)]
        assert a.delete("dentist")["subject"] == "dentist"
        real = config.data_dir()
        config.set_data_dir(t)
        from tools.registry import Registry
        r = Registry()
        register(r)
        assert "Added" in r.call("add_agenda_item", {"subject": "chemistry lab report", "due": "friday", "kind": "homework"}).data
        assert r.call("add_agenda_item", {"subject": "x", "due": "someday-ish"}).error_kind == "bad_args" and "chemistry" in r.call("list_agenda_items").data
        assert r.call("complete_agenda_item", {"match": "chemistry"}).ok and r.call("delete_agenda_item", {"match": "zzz"}).error_kind == "not_found" and r.spec("delete_agenda_item").destructive
        config.set_data_dir(real)
    print("agenda ok")


if __name__ == "__main__":
    _selfcheck()
