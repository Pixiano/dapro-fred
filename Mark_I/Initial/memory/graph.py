"""Mark I memory, Phase 4: temporal graph + name resolution. Standalone; fed by consolidate.py.

Sockets:
  - Graph(store): adds its tables via schema_graph.sql (+ review_queue from the consolidation schema).
  - resolve(kind, name): the one door for names. alias/exact -> close spelling (auto alias) ->
    maybe-same (new entity + review question) -> new. Everything that names an entity should call this.
  - relate(subject, relation, object): rows are closed, never overwritten. EXCLUSIVE relations hold one
    object at a time (a new one closes the old); others (knows, works_on) stack.
  - merge(from_id, into_id): moves everything, keeps the old row marked merged. Fix for "X is Y".
Names: difflib similarity on raw and phonetic-ish forms (oo->u, ee->i ...), same kind only.
ponytail: tuned for English/Indian names; use a real phonetic algorithm if misses pile up in the queue.
"""
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

EXCLUSIVE = {"lives_in", "works_at", "status", "partner_of"}  # extend as needed
AUTO, ASK = 0.85, 0.65  # similarity >= AUTO links as alias, >= ASK queues a question
_HERE = Path(__file__).parent
_PHON = [("oo", "u"), ("ou", "u"), ("ee", "i"), ("ie", "i"), ("ph", "f"), ("w", "v"), ("z", "s"), ("c", "k"), ("y", "i")]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(name):
    return " ".join(name.lower().split())


def _sim(a, b):
    def ph(s):
        for x, y in _PHON:
            s = s.replace(x, y)
        return s
    return max(SequenceMatcher(None, a, b).ratio(), SequenceMatcher(None, ph(a), ph(b)).ratio())


class Graph:
    def __init__(self, store):
        self.s = store
        with store.lock:
            for f in ("schema_consolidation.sql", "schema_graph.sql"):
                store.db.executescript((_HERE / f).read_text(encoding="utf-8"))

    # ---- names ----
    def canonical(self, eid):
        with self.s.lock:
            while (r := self.s.db.execute("SELECT into_id FROM entity_merges WHERE entity_id=?", (eid,)).fetchone()):
                eid = r[0]
        return eid

    def add_alias(self, kind, alias, entity_id):
        with self.s.lock:
            self.s.db.execute("INSERT OR REPLACE INTO entity_aliases VALUES(?,?,?)", (kind, norm(alias), entity_id))
            self.s.db.commit()

    def _queue(self, day, kind, text, source_id):
        with self.s.lock:
            self.s.db.execute("INSERT INTO review_queue(day,kind,text,source_id) VALUES(?,?,?,?)", (day, kind, text, source_id))
            self.s.db.commit()

    def resolve(self, kind, name, day=None, source_id=None) -> int:
        n = norm(name)
        if not n:
            raise ValueError("empty name")
        s = self.s
        with s.lock:
            r = s.db.execute("SELECT entity_id FROM entity_aliases WHERE kind=? AND alias=?", (kind, n)).fetchone()
            if r:
                return self.canonical(r[0])
            live = [e for e in s.entities(kind)
                    if not s.db.execute("SELECT 1 FROM entity_merges WHERE entity_id=?", (e["id"],)).fetchone()]
            for e in live:
                if norm(e["name"]) == n:
                    return e["id"]
            best = max(live, key=lambda e: _sim(n, norm(e["name"])), default=None)
            score = _sim(n, norm(best["name"])) if best else 0
            if score >= AUTO:
                self.add_alias(kind, name, best["id"])
                return best["id"]
            eid = s.entity(kind, name.strip())
            if score >= ASK:
                self._queue(day or _now()[:10], "question", f"Is {kind} '{name}' the same as '{best['name']}'?", source_id)
            return eid

    def merge(self, from_id, into_id):
        f, t = self.canonical(from_id), self.canonical(into_id)
        if f == t:
            return t
        s = self.s
        with s.lock:
            now = _now()
            for fld in {r[0] for r in s.db.execute("SELECT field FROM entity_states WHERE entity_id=? AND valid_to IS NULL", (f,))}:
                if s.state(t, fld):  # target already has a current value: it wins, the moved one closes now
                    s.db.execute("UPDATE entity_states SET valid_to=? WHERE entity_id=? AND field=? AND valid_to IS NULL", (now, f, fld))
            s.db.execute("UPDATE entity_states SET entity_id=? WHERE entity_id=?", (t, f))
            s.db.execute("UPDATE events SET entity_id=? WHERE entity_id=?", (t, f))
            s.db.execute("UPDATE relationships SET subject_id=? WHERE subject_id=?", (t, f))
            s.db.execute("UPDATE relationships SET object_id=? WHERE object_id=?", (t, f))
            s.db.execute("UPDATE entity_aliases SET entity_id=? WHERE entity_id=?", (t, f))
            row = s.db.execute("SELECT kind, name FROM entities WHERE id=?", (f,)).fetchone()
            s.db.execute("INSERT INTO entity_merges VALUES(?,?,?)", (f, t, now))
            s.db.commit()
            self.add_alias(row["kind"], row["name"], t)
        return t

    # ---- relationships ----
    def relate(self, subject_id, relation, object_id, source_id=None, ts=None, confidence=1.0, day=None):
        """Returns the new row id, or None if it was a no-op or got queued instead."""
        subject_id, object_id = self.canonical(subject_id), self.canonical(object_id)
        relation = norm(relation).replace(" ", "_")
        if subject_id == object_id or not relation:
            raise ValueError("a relationship needs two different entities and a relation")
        ts, s = ts or _now(), self.s
        with s.lock:
            open_rows = s.db.execute("SELECT * FROM relationships WHERE subject_id=? AND relation=? AND valid_to IS NULL",
                                     (subject_id, relation)).fetchall()
            if any(r["object_id"] == object_id for r in open_rows):
                return None
            if relation in EXCLUSIVE and open_rows:
                if ts < open_rows[0]["valid_from"]:  # an older fact would overlap the current one: ask, don't guess
                    self._queue(day or ts[:10], "contradiction",
                                f"Older {relation} fact (entity {subject_id} -> {object_id}, {ts}) overlaps the current one; not applied.",
                                source_id)
                    return None
                s.db.execute("UPDATE relationships SET valid_to=? WHERE subject_id=? AND relation=? AND valid_to IS NULL",
                             (ts, subject_id, relation))
            cur = s.db.execute("INSERT INTO relationships(subject_id,relation,object_id,valid_from,confidence,source_id) "
                               "VALUES(?,?,?,?,?,?)", (subject_id, relation, object_id, ts, confidence, source_id))
            s.db.commit()
            return cur.lastrowid

    def neighbors(self, entity_id, at=None):
        """Links touching an entity that were true at `at` (ISO time; default now)."""
        e, at = self.canonical(entity_id), at or _now()
        with self.s.lock:
            return self.s.db.execute(
                "SELECT * FROM relationships WHERE (subject_id=? OR object_id=?) AND valid_from<=? "
                "AND (valid_to IS NULL OR valid_to>?) ORDER BY valid_from", (e, e, at, at)).fetchall()

    def history(self, a, b):
        a, b = self.canonical(a), self.canonical(b)
        with self.s.lock:
            return self.s.db.execute(
                "SELECT * FROM relationships WHERE (subject_id=? AND object_id=?) OR (subject_id=? AND object_id=?) "
                "ORDER BY valid_from, id", (a, b, b, a)).fetchall()


if __name__ == "__main__":  # self-check: python graph.py
    from store import MemoryStore
    s = MemoryStore(":memory:")
    g = Graph(s)
    n = lambda t: s.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    su = g.resolve("person", "Rina")
    assert g.resolve("person", "  rina ") == su                      # case/space
    assert g.resolve("person", "Rinah") == su                        # close spelling -> alias, no new entity
    assert n("entity_aliases") == 1 and n("entities") == 1
    ro, ri = g.resolve("person", "Rohan"), g.resolve("person", "Rohit")
    assert ro != ri                                                    # different people stay different
    assert g.resolve("project", "Rina") != su                        # kinds never mix
    fred = g.resolve("project", "FRED")
    assert g.relate(su, "works on", fred, ts="2026-01-01T00:00:00+00:00")
    assert g.relate(su, "works_on", fred) is None                      # duplicate = no-op
    for city, t in (("Delhi", "2026-02-01T00:00:00+00:00"), ("Pune", "2026-06-01T00:00:00+00:00")):
        g.relate(su, "lives_in", g.resolve("place", city), ts=t)
    live = lambda at: {s.db.execute("SELECT name FROM entities WHERE id=?", (r["object_id"],)).fetchone()[0]
                       for r in g.neighbors(su, at) if r["relation"] == "lives_in"}
    assert live("2026-03-01T00:00:00+00:00") == {"Delhi"} and live("2026-07-01T00:00:00+00:00") == {"Pune"}
    q = n("review_queue")
    assert g.relate(su, "lives_in", g.resolve("place", "Goa"), ts="2026-04-01T00:00:00+00:00") is None  # overlap queued
    assert n("review_queue") == q + 1
    try:
        g.relate(su, "knows", su)
        raise SystemExit("self-link accepted")
    except ValueError:
        pass
    s.set_state(ro, "mood", "ok")
    s.add_event("note", "hi", entity_id=ro)
    n_ent = n("entities")
    g.merge(ro, ri)                                                    # reversible merge: row kept, marked
    assert g.canonical(ro) == ri and g.resolve("person", "Rohan") == ri and n("entities") == n_ent
    assert s.snapshot(ri) == {"mood": "ok"} and len(g.history(su, fred)) == 1
    print("graph ok")
