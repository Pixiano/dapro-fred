"""Mark I memory store, Phase 1: SQLite entities, events, state history, provenance, daily log.

Stdlib only. Thread-safe: one connection shared across threads, every call under `lock`
(other modules that touch `db` take the same lock). Sockets (plug points for later layers):
  - MemoryStore(path): the DB location is a parameter; nothing global.
  - add_source(kind, ref): every producer (conversation, tool, import, consolidation)
    registers a source and passes source_id to add_event/set_state.
  - on_event: list of callables(event_dict) run after each add_event, outside the lock. A failing
    hook is logged, never raised: the event is already saved and must not be lost to a hook bug.
  - Later tables (relationships, inferences, contradictions, consolidation_runs) are added by new
    schema files, not by editing this one.
"""
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")
log = logging.getLogger("memory")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_hooks(hooks, arg):
    for hook in list(hooks):
        try:
            hook(arg)
        except Exception:
            log.exception("memory hook %r failed", hook)


class MemoryStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")  # no-op for :memory:
        self.db.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self.lock = threading.RLock()
        self.on_event = []

    def close(self):
        with self.lock:
            self.db.close()

    def add_source(self, kind, ref=None) -> int:
        return self._ins("INSERT INTO sources(kind,ref,created_at) VALUES(?,?,?)", (kind, ref, _now()))

    def entity(self, kind, name) -> int:
        """Get-or-create."""
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO entities(kind,name,created_at) VALUES(?,?,?)", (kind, name, _now()))
            self.db.commit()
            return self.db.execute("SELECT id FROM entities WHERE kind=? AND name=?", (kind, name)).fetchone()[0]

    def entities(self, kind=None):
        with self.lock:
            if kind:
                return self.db.execute("SELECT * FROM entities WHERE kind=? ORDER BY id", (kind,)).fetchall()
            return self.db.execute("SELECT * FROM entities ORDER BY id").fetchall()

    def set_state(self, entity_id, field, value, source_id=None, confidence=1.0, ts=None):
        """Close the open row for (entity, field) if the value changed, then open a new one. One transaction."""
        ts = ts or _now()
        with self.lock:
            cur = self.state(entity_id, field)
            if cur is not None and cur["value"] == value:
                return cur["id"]
            if cur is not None and ts < cur["valid_from"]:  # older fact (backfilled history): slot it in, leave the current value alone
                return self._set_past(entity_id, field, value, source_id, confidence, ts)
            self.db.execute("UPDATE entity_states SET valid_to=? WHERE entity_id=? AND field=? AND valid_to IS NULL",
                            (ts, entity_id, field))
            return self._ins("INSERT INTO entity_states(entity_id,field,value,valid_from,confidence,source_id) "
                             "VALUES(?,?,?,?,?,?)", (entity_id, field, value, ts, confidence, source_id))

    def _set_past(self, entity_id, field, value, source_id, confidence, ts):
        rows = [dict(r) for r in self.state_history(entity_id, field)]
        pred = max((r for r in rows if r["valid_from"] <= ts), key=lambda r: (r["valid_from"], r["id"]), default=None)
        if pred and pred["value"] == value:
            return pred["id"]
        succ = min((r for r in rows if r["valid_from"] > ts), key=lambda r: (r["valid_from"], r["id"]))
        if pred and (pred["valid_to"] is None or pred["valid_to"] > ts):
            self.db.execute("UPDATE entity_states SET valid_to=? WHERE id=?", (ts, pred["id"]))
        return self._ins("INSERT INTO entity_states(entity_id,field,value,valid_from,valid_to,confidence,source_id) VALUES(?,?,?,?,?,?,?)",
                         (entity_id, field, value, ts, succ["valid_from"], confidence, source_id))

    def state(self, entity_id, field):
        with self.lock:
            return self.db.execute("SELECT * FROM entity_states WHERE entity_id=? AND field=? AND valid_to IS NULL",
                                   (entity_id, field)).fetchone()

    def state_history(self, entity_id, field):
        with self.lock:
            return self.db.execute("SELECT * FROM entity_states WHERE entity_id=? AND field=? ORDER BY valid_from, id",
                                   (entity_id, field)).fetchall()

    def snapshot(self, entity_id) -> dict:
        """Current state of an entity as {field: value}. Projects, goals, tasks, preferences are
        entity kinds whose fields live here (spec section 9), so they need no tables of their own."""
        with self.lock:
            rows = self.db.execute("SELECT field, value FROM entity_states WHERE entity_id=? AND valid_to IS NULL",
                                   (entity_id,)).fetchall()
        return {r["field"]: r["value"] for r in rows}

    def add_event(self, type, content, entity_id=None, importance=0.5, confidence=1.0, source_id=None, ts=None) -> int:
        with self.lock:
            eid = self._ins("INSERT INTO events(ts,type,entity_id,content,importance,confidence,source_id,created_at) "
                            "VALUES(?,?,?,?,?,?,?,?)", (ts or _now(), type, entity_id, content, importance, confidence,
                                                        source_id, _now()))
            ev = dict(self.db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())
        run_hooks(self.on_event, ev)
        return eid

    def events_on(self, day):
        """Events whose ts starts with YYYY-MM-DD (UTC)."""
        with self.lock:
            return self.db.execute("SELECT * FROM events WHERE ts LIKE ? ORDER BY ts, id", (day + "%",)).fetchall()

    def set_daily_log(self, day, summary):
        with self.lock:
            self.db.execute("INSERT INTO daily_logs(day,summary,updated_at) VALUES(?,?,?) "
                            "ON CONFLICT(day) DO UPDATE SET summary=excluded.summary, updated_at=excluded.updated_at",
                            (day, summary, _now()))
            self.db.commit()

    def daily_log(self, day):
        with self.lock:
            r = self.db.execute("SELECT summary FROM daily_logs WHERE day=?", (day,)).fetchone()
        return r[0] if r else None

    def _ins(self, sql, args) -> int:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid


if __name__ == "__main__":  # self-check: python store.py
    logging.getLogger("memory").setLevel(logging.CRITICAL)  # the broken test hook logs on purpose
    s = MemoryStore(":memory:")
    seen = []
    s.on_event.append(seen.append)
    s.on_event.insert(0, lambda ev: 1 / 0)  # a broken hook must not lose the event or block the next hook
    src = s.add_source("test")
    p = s.entity("project", "demo")
    assert s.entity("project", "demo") == p
    s.set_state(p, "status", "planning", src, ts="2026-01-01T00:00:00+00:00")
    s.set_state(p, "status", "planning", src)  # same value: no new row
    s.set_state(p, "status", "building", src, ts="2026-02-01T00:00:00+00:00")
    h = s.state_history(p, "status")
    assert [r["value"] for r in h] == ["planning", "building"]
    assert h[0]["valid_to"] == "2026-02-01T00:00:00+00:00" and h[1]["valid_to"] is None
    assert s.state(p, "status")["value"] == "building"
    s.add_event("note", "hello", p, source_id=src, ts="2026-03-04T10:00:00+00:00")
    assert s.snapshot(p) == {"status": "building"} and [e["name"] for e in s.entities("project")] == ["demo"]
    assert len(seen) == 1 and seen[0]["content"] == "hello"
    assert len(s.events_on("2026-03-04")) == 1 and not s.events_on("2026-03-05")
    s.set_daily_log("2026-03-04", "a"); s.set_daily_log("2026-03-04", "b")
    assert s.daily_log("2026-03-04") == "b"
    try:  # foreign keys are enforced
        s.add_event("note", "x", entity_id=999)
        raise SystemExit("foreign key not enforced")
    except sqlite3.IntegrityError:
        pass
    done = []  # shared connection works across threads
    s.set_state(p, "status", "idea", src, ts="2025-12-01T00:00:00+00:00")        # older fact: slotted in before, the present is untouched
    s.set_state(p, "status", "design", src, ts="2026-01-15T00:00:00+00:00")      # older fact between two rows: splits the interval
    h = [(r["value"], r["valid_from"][:10], (r["valid_to"] or "now")[:10]) for r in s.state_history(p, "status")]
    assert h == [("idea", "2025-12-01", "2026-01-01"), ("planning", "2026-01-01", "2026-01-15"), ("design", "2026-01-15", "2026-02-01"),
                 ("building", "2026-02-01", "now")] and s.state(p, "status")["value"] == "building"
    t = threading.Thread(target=lambda: done.append(s.add_event("note", "from thread")))
    t.start(); t.join()
    assert done
    print("memory store ok")
