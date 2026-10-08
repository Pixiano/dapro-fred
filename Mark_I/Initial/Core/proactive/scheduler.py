"""Scheduler: persistent reminders and timers that fire as a bus event when due. (P6; the old ReminderScheduler minus APScheduler, recurrence and file watches.)

Sockets
  in : Scheduler(bus, path=None, now=time.time).add(at, text, kind="reminder"|"timer") -> item   at = epoch seconds, must be in the future
       .cancel(match) -> int   match = words from the text, or "all"; .items() -> list sorted by time; .start() / .stop() run the 1 s tick; .tick() fires what is due (tests call it directly)
  out: bus.emit("proactive", "due", text, what, late)   what = reminder|timer   late=True when it was missed (FRED was off) and fires at start; the item is removed before the event, so nothing fires twice
       <data_dir>/schedule.json  [{id, at, text, kind}], written atomically on every change; a damaged file is ignored (empty schedule), never a crash
Delivery (speaking it, with "sir") is engine/live.py's job; this module only knows time.
Self-check: python proactive/scheduler.py
"""
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log


class Scheduler:
    def __init__(self, bus, path=None, now=time.time):
        self.bus, self.now, self._lock, self._stop = bus, now, threading.RLock(), threading.Event()
        self.path = Path(path) if path else config.data_dir() / "schedule.json"
        try:
            self._items = [i for i in json.loads(self.path.read_text(encoding="utf-8")) if {"id", "at", "text", "kind"} <= set(i)]
        except Exception:
            self._items = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._items), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, at: float, text: str, kind: str = "reminder") -> dict:
        if at <= self.now():
            raise ValueError("that time has already passed")
        item = {"id": uuid.uuid4().hex[:6], "at": at, "text": text.strip(), "kind": kind}
        with self._lock:
            self._items.append(item)
            self._save()
        log.session("schedule_add", what=kind, at=at)
        return item

    def items(self) -> list:
        with self._lock:
            return sorted(self._items, key=lambda i: i["at"])

    def cancel(self, match: str) -> int:
        m = match.lower().split()
        with self._lock:
            gone = [i for i in self._items if match.strip().lower() == "all" or (m and all(w in (i["text"] + " " + i["kind"]).lower() for w in m))]
            self._items = [i for i in self._items if i not in gone]
            gone and self._save()
        return len(gone)

    def tick(self):
        with self._lock:
            now = self.now()
            due = sorted((i for i in self._items if i["at"] <= now), key=lambda i: i["at"])
            if due:
                self._items = [i for i in self._items if i not in due]
                self._save()
        for i in due:
            self.bus.emit("proactive", "due", text=i["text"], what=i["kind"], late=now - i["at"] > 60)

    def start(self):
        self.tick()                                                                 # missed while off: fires at once, marked late
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.wait(1.0):
            try:
                self.tick()
            except Exception as ex:
                log.session("schedule_tick_failed", err=type(ex).__name__)

    def stop(self):
        self._stop.set()


def _selfcheck():
    import tempfile
    import events as ev
    bus, got, clock = ev.EventBus(), [], [1000.0]
    bus.subscribe(lambda e: got.append(e.data), kinds=("due",), sources=("proactive",))
    p = Path(tempfile.mkdtemp()) / "s.json"
    s = Scheduler(bus, p, now=lambda: clock[0])
    s.add(1060, "call mum"); s.add(1030, "tea", "timer"); s.add(2000, "dentist")
    try:
        s.add(900, "past")
        raise AssertionError("past time accepted")
    except ValueError:
        pass
    assert [i["text"] for i in s.items()] == ["tea", "call mum", "dentist"]
    clock[0] = 1040; s.tick()
    assert got == [{"text": "tea", "what": "timer", "late": False}] and len(s.items()) == 2
    s.tick(); assert len(got) == 1                                                  # never twice
    assert len(Scheduler(bus, p, now=lambda: clock[0]).items()) == 2                # persisted
    assert s.cancel("MUM call") == 1 and s.cancel("nothing") == 0 and [i["text"] for i in s.items()] == ["dentist"]
    clock[0] = 5000; s2 = Scheduler(bus, p, now=lambda: clock[0]); s2.tick()        # was off for hours
    assert got[-1] == {"text": "dentist", "what": "reminder", "late": True} and not s2.items()
    s2.add(6000, "a"); s2.add(7000, "b"); assert s2.cancel("all") == 2
    p.write_text("{not json", encoding="utf-8"); assert Scheduler(bus, p).items() == []
    print("scheduler ok")


if __name__ == "__main__":
    _selfcheck()
