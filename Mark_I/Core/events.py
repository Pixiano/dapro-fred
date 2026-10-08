"""Core event spine: every producer emits Event(ts, source, kind, data); subscribers never call a producer's internals.

Sockets
  in : bus.emit(source, kind, **data) from any producer (audio, clock, senses, engine, tools, proactive)
  out: bus.subscribe(fn, kinds=None, sources=None) -> unsubscribe()   subscribers: EventLog (L4 input), memory feed (Archive.write), session log, UI
       EventLog.recent(seconds, kind=None, source=None)  the ~10 min ring for L4; persisted to <data_dir>/events/YYYY-MM-DD.jsonl
Delivery is synchronous in the emitting thread, in subscription order; a subscriber that raises is logged and skipped, never breaks the producer or the others.
Self-check: python events.py
"""
import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime

import config
import log


@dataclass(frozen=True)
class Event:
    source: str
    kind: str
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class EventBus:
    def __init__(self):
        self._subs: list[tuple] = []
        self._lock = threading.Lock()

    def subscribe(self, fn, kinds=None, sources=None):
        """fn(Event). kinds/sources: optional collections to filter on. Returns an unsubscribe function."""
        sub = (fn, frozenset(kinds) if kinds else None, frozenset(sources) if sources else None)
        with self._lock:
            self._subs.append(sub)

        def unsubscribe():
            with self._lock:
                if sub in self._subs:
                    self._subs.remove(sub)
        return unsubscribe

    def emit(self, source: str, kind: str, **data) -> Event:
        ev = Event(source, kind, data)
        with self._lock:
            subs = list(self._subs)
        for fn, kinds, sources in subs:
            if (kinds and kind not in kinds) or (sources and source not in sources):
                continue
            try:
                fn(ev)
            except Exception as ex:
                log.session("subscriber_error", subscriber=getattr(fn, "__qualname__", repr(fn)), event=f"{source}.{kind}", error=repr(ex))
        return ev


class EventLog:
    """Subscriber: keeps the last `ring_s` seconds in memory and appends every event to the day's jsonl."""

    def __init__(self, bus: EventBus, ring_s: float | None = None, persist: bool = True):
        self.ring_s = config.get().event_ring_s if ring_s is None else ring_s
        self.persist = persist
        self._ring: deque[Event] = deque()
        self._lock = threading.Lock()
        self._unsub = bus.subscribe(self._on)

    def _on(self, ev: Event):
        with self._lock:
            self._ring.append(ev)
            while self._ring and ev.ts - self._ring[0].ts > self.ring_s:
                self._ring.popleft()
        if self.persist:
            d = config.data_dir() / "events"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"{datetime.fromtimestamp(ev.ts):%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(ev), ensure_ascii=False, default=str) + "\n")

    def recent(self, seconds: float | None = None, kind=None, source=None) -> list[Event]:
        now = time.time()
        with self._lock:
            evs = list(self._ring)
        return [e for e in evs if (seconds is None or now - e.ts <= seconds) and (kind is None or e.kind == kind) and (source is None or e.source == source)]

    def close(self):
        self._unsub()


bus = EventBus()   # the process-wide bus; tests build their own EventBus()


def _selfcheck():
    import tempfile
    real = config.data_dir()
    with tempfile.TemporaryDirectory() as t:
        config.set_data_dir(t)
        b, got, only = EventBus(), [], []
        elog = EventLog(b, ring_s=0.2)
        b.subscribe(got.append)
        un = b.subscribe(only.append, kinds={"wake"}, sources={"audio"})
        b.subscribe(lambda e: 1 / 0)                    # a broken subscriber must not stop the rest
        b.emit("audio", "wake", score=0.9)
        b.emit("audio", "vad", on=True)
        b.emit("screen", "wake")
        assert [e.kind for e in got] == ["wake", "vad", "wake"] and len(only) == 1 and only[0].data["score"] == 0.9
        assert len(elog.recent(kind="wake")) == 2 and len(elog.recent(source="screen")) == 1
        un()
        b.emit("audio", "wake")
        assert len(only) == 1
        time.sleep(0.3)
        b.emit("audio", "late")
        assert [e.kind for e in elog.recent()] == ["late"]   # ring trimmed to ring_s
        files = list((config.data_dir() / "events").glob("*.jsonl"))
        assert len(files) == 1 and len(files[0].read_text().splitlines()) == 5   # persistence keeps all
        config.set_data_dir(real)
    print("events ok")


if __name__ == "__main__":
    _selfcheck()
