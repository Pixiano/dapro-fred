"""Mark I memory store, Phase 1: SQLite entities, events, state history, provenance, daily log.

Stdlib only. Sockets (plug points for later phases):
  - MemoryStore(path): the DB location is a parameter; nothing global.
  - add_source(kind, ref): every producer (conversation, tool, import, consolidation)
    registers a source and passes source_id to add_event/set_state.
  - on_event: list of callables(event_dict) run after each add_event. Phase 2 plugs
    FAISS indexing / memory candidates in here without touching this file.
  - Later tables (relationships, inferences, contradictions, consolidation_runs, archive_index)
    are added by new schema files, not by editing this one.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self.on_event = []

    def add_source(self, kind, ref=None) -> int:
        return self._ins("INSERT INTO sources(kind,ref,created_at) VALUES(?,?,?)", (kind, ref, _now()))

    def entity(self, kind, name) -> int:
        """Get-or-create."""
        self.db.execute("INSERT OR IGNORE INTO entities(kind,name,created_at) VALUES(?,?,?)", (kind, name, _now()))
        return self.db.execute("SELECT id FROM entities WHERE kind=? AND name=?", (kind, name)).fetchone()[0]

    def set_state(self, entity_id, field, value, source_id=None, confidence=1.0, ts=None):
        """Close the open row for (entity, field) if the value changed, then open a new one."""
        ts = ts or _now()
        cur = self.state(entity_id, field)
        if cur is not None and cur["value"] == value:
            return cur["id"]
        self.db.execute("UPDATE entity_states SET valid_to=? WHERE entity_id=? AND field=? AND valid_to IS NULL",
                        (ts, entity_id, field))
        return self._ins("INSERT INTO entity_states(entity_id,field,value,valid_from,confidence,source_id) "
                         "VALUES(?,?,?,?,?,?)", (entity_id, field, value, ts, confidence, source_id))

    def state(self, entity_id, field):
        return self.db.execute("SELECT * FROM entity_states WHERE entity_id=? AND field=? AND valid_to IS NULL",
                               (entity_id, field)).fetchone()

    def state_history(self, entity_id, field):
        return self.db.execute("SELECT * FROM entity_states WHERE entity_id=? AND field=? ORDER BY valid_from, id",
                               (entity_id, field)).fetchall()

    def entities(self, kind=None):
        q, a = ("SELECT * FROM entities WHERE kind=? ORDER BY id", (kind,)) if kind else ("SELECT * FROM entities ORDER BY id", ())
        return self.db.execute(q, a).fetchall()

    def snapshot(self, entity_id) -> dict:
        """Current state of an entity as {field: value}. Projects, goals, tasks, preferences are
        entity kinds whose fields live here (spec section 9), so they need no tables of their own."""
        rows = self.db.execute("SELECT field, value FROM entity_states WHERE entity_id=? AND valid_to IS NULL",
                               (entity_id,)).fetchall()
        return {r["field"]: r["value"] for r in rows}

    def add_event(self, type, content, entity_id=None, importance=0.5, confidence=1.0, source_id=None, ts=None) -> int:
        eid = self._ins("INSERT INTO events(ts,type,entity_id,content,importance,confidence,source_id,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?)", (ts or _now(), type, entity_id, content, importance, confidence,
                                                    source_id, _now()))
        ev = dict(self.db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())
        for hook in self.on_event:
            hook(ev)
        return eid

    def events_on(self, day):
        """Events whose ts starts with YYYY-MM-DD (UTC)."""
        return self.db.execute("SELECT * FROM events WHERE ts LIKE ? ORDER BY ts, id", (day + "%",)).fetchall()

    def set_daily_log(self, day, summary):
        self.db.execute("INSERT INTO daily_logs(day,summary,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(day) DO UPDATE SET summary=excluded.summary, updated_at=excluded.updated_at",
                        (day, summary, _now()))
        self.db.commit()

    def daily_log(self, day):
        r = self.db.execute("SELECT summary FROM daily_logs WHERE day=?", (day,)).fetchone()
        return r[0] if r else None

    def _ins(self, sql, args) -> int:
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur.lastrowid


if __name__ == "__main__":  # self-check: python store.py
    s = MemoryStore(":memory:")
    seen = []
    s.on_event.append(seen.append)
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
    print("memory store ok")
