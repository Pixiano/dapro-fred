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
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from store import ensure_column, to_utc

EXCLUSIVE = {"lives_in", "works_at", "status", "partner_of"}  # extend as needed
AUTO, ASK = 0.85, 0.65  # similarity >= AUTO links as alias, >= ASK queues a question
_HERE = Path(__file__).parent
_PHON = [("oo", "u"), ("ou", "u"), ("ee", "i"), ("ie", "i"), ("ph", "f"), ("w", "v"), ("z", "s"), ("c", "k"), ("y", "i")]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(name):
    return " ".join(name.lower().split())


def _ph(s):
    for x, y in _PHON:
        s = s.replace(x, y)
    return s


def _sim(a, b):
    return max(SequenceMatcher(None, a, b).ratio(), SequenceMatcher(None, _ph(a), _ph(b)).ratio())


def _lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return cur[-1] if a else len(b)


def _variant(a, b):
    """True only for a plain spelling variant of one name (never another person's): both names at least 5 letters (6 for a plain typo), neither a
    prefix/suffix extension of the other (Manish/Manisha, Nikhil/Nikhila, Sara/Sarah are different people), and one typo away, or two
    when the phonetic forms agree (Soohani/Suhani). A plain one-letter difference is NOT enough (Pranay/Pranav, Rahul/Rahil are different people): it is asked about.
    Anything merely similar is asked about, not merged."""
    if min(len(a), len(b)) < 5 or a.startswith(b) or b.startswith(a) or a.endswith(b) or b.endswith(a):
        return False
    squash = lambda s: re.sub(r"(.)+", r"", s.replace("h", ""))  # Nitya/Nithya, Sharma/Sarma: h and doubled letters are transliteration noise
    return squash(a) == squash(b) or (_lev(a, b) <= 2 and _ph(a) == _ph(b))


class Graph:
    def __init__(self, store):
        self.s = store
        with store.lock:
            for f in ("schema_consolidation.sql", "schema_hygiene.sql", "schema_graph.sql"):
                store.db.executescript((_HERE / f).read_text(encoding="utf-8"))
            ensure_column(store.db, "relationships", "last_seen TEXT")

    # ---- names ----
    def canonical(self, eid):
        with self.s.lock:
            while (r := self.s.db.execute("SELECT into_id FROM entity_merges WHERE entity_id=?", (eid,)).fetchone()):
                eid = r[0]
        return eid

    def add_alias(self, kind, alias, entity_id, force=False):
        """Another name for an entity. A name that already belongs to a DIFFERENT entity is never re-pointed unless force=True (a
        decision by Vatsal or a merge): the request becomes a review question instead. Returns True if the alias is now set."""
        with self.s.lock:
            old = self.s.db.execute("SELECT entity_id FROM entity_aliases WHERE kind=? AND alias=?", (kind, norm(alias))).fetchone()
            if old and self.canonical(old[0]) != self.canonical(entity_id) and not force:
                self._queue(_now()[:10], "question", f"The name '{alias}' already belongs to entity {self.canonical(old[0])}; a request to point it at "
                            f"entity {self.canonical(entity_id)} was NOT applied.", None)
                return False
            self.s.db.execute("INSERT OR REPLACE INTO entity_aliases VALUES(?,?,?)", (kind, norm(alias), entity_id))
            self.s.db.commit()
            return True

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
            if best and _variant(n, norm(best["name"])):
                self.add_alias(kind, name, best["id"])
                return best["id"]
            eid = s.entity(kind, name.strip())
            if score >= ASK:
                self._queue(day or _now()[:10], "question", f"Is {kind} '{name}' the same as '{best['name']}'?", source_id)
            return eid

    def confirm_alias(self, word, entity_id):
        """The user said yes to "do you mean X?": remember the word as another name for X."""
        e = self.canonical(entity_id)
        with self.s.lock:
            kind = self.s.db.execute("SELECT kind FROM entities WHERE id=?", (e,)).fetchone()[0]
        self.add_alias(kind, word, e, force=True)

    def tag(self, event_id, entity_id):
        with self.s.lock:
            self.s.db.execute("INSERT OR IGNORE INTO event_entities VALUES(?,?)", (event_id, self.canonical(entity_id)))
            self.s.db.commit()

    def events_about(self, entity_id, limit=20, include_cold=False):
        """Events tagged with this entity, most important then newest first."""
        with self.s.lock:
            return [dict(r) for r in self.s.db.execute(
                "SELECT e.* FROM events e JOIN event_entities t ON t.event_id=e.id WHERE t.entity_id=? "
                + ("" if include_cold else "AND e.id NOT IN (SELECT event_id FROM cold_events) ")
                + "ORDER BY e.importance DESC, e.ts DESC LIMIT ?", (self.canonical(entity_id), limit))]

    def find_in_text(self, text):
        """Entities a question names. Returns (found {id: name}, maybe [(word, id)]). A word is *found* when it is
        an alias or exact name; *maybe* when a Capitalised word is the start of exactly one name ("Pri" -> Priya):
        never used silently, the caller asks the user first.
        ponytail: capitalisation is the name signal; drop it if speech-to-text stops capitalising."""
        s, found, maybe = self.s, {}, []
        with s.lock:
            live = [(e["id"], e["name"]) for e in s.entities()
                    if not s.db.execute("SELECT 1 FROM entity_merges WHERE entity_id=?", (e["id"],)).fetchone()]
            for w in re.findall(r"[\w']+", text):
                word = re.sub(r"['’]s$", "", w)
                n = norm(word)
                r = s.db.execute("SELECT entity_id FROM entity_aliases WHERE alias=?", (n,)).fetchone()
                hit = self.canonical(r[0]) if r else next((i for i, nm in live if norm(nm) == n), None)
                if hit:
                    found[hit] = next(nm for i, nm in live if i == hit)
                elif len(n) >= 2 and word[0].isupper():
                    pre = {i for i, nm in live if any(t.startswith(n) for t in norm(nm).split())}
                    if len(pre) == 1:
                        maybe.append((word, pre.pop()))
        return found, maybe

    _MOVES = {"state": ("entity_states", "entity_id"), "event": ("events", "entity_id"),
              "rel_s": ("relationships", "subject_id"), "rel_o": ("relationships", "object_id")}

    def merge(self, from_id, into_id):
        """Moves everything of from_id to into_id and journals each moved row (merge_moves) so unmerge() can undo it."""
        f, t = self.canonical(from_id), self.canonical(into_id)
        if f == t:
            return t
        s = self.s
        with s.lock:
            now = _now()
            jr = lambda tbl, key, extra=None: s.db.execute("INSERT INTO merge_moves(entity_id,tbl,key,extra) VALUES(?,?,?,?)", (f, tbl, str(key), extra))
            for fld in {r[0] for r in s.db.execute("SELECT field FROM entity_states WHERE entity_id=? AND valid_to IS NULL", (f,))}:
                if s.state(t, fld):  # target already has a current value: it wins, the moved one closes now
                    for (i,) in s.db.execute("SELECT id FROM entity_states WHERE entity_id=? AND field=? AND valid_to IS NULL", (f, fld)).fetchall():
                        jr("state_closed", i, now)
                    s.db.execute("UPDATE entity_states SET valid_to=? WHERE entity_id=? AND field=? AND valid_to IS NULL", (now, f, fld))
            for rel in EXCLUSIVE:  # the target already has this link open: the merged entity's own copy closes now (undo reopens it)
                if s.db.execute("SELECT 1 FROM relationships WHERE subject_id=? AND relation=? AND valid_to IS NULL", (t, rel)).fetchone():
                    for (i,) in s.db.execute("SELECT id FROM relationships WHERE subject_id=? AND relation=? AND valid_to IS NULL", (f, rel)).fetchall():
                        jr("rel_closed", i, now)
                        s.db.execute("UPDATE relationships SET valid_to=? WHERE id=?", (now, i))
            skip = {"rel_s": " AND object_id!=?", "rel_o": " AND subject_id!=?"}  # a link between the two merged entities would become a self-loop
            for kind, (tbl, col) in self._MOVES.items():
                ids = [r[0] for r in s.db.execute(f"SELECT id FROM {tbl} WHERE {col}=?" + skip.get(kind, ""), (f, t) if kind in skip else (f,)).fetchall()]
                for i in ids:
                    jr(kind, i)
                    s.db.execute(f"UPDATE {tbl} SET {col}=? WHERE id=?", (t, i))
            for k, al in s.db.execute("SELECT kind, alias FROM entity_aliases WHERE entity_id=?", (f,)).fetchall():
                jr("alias", json.dumps([k, al]))
            s.db.execute("UPDATE entity_aliases SET entity_id=? WHERE entity_id=?", (t, f))
            for (e,) in s.db.execute("SELECT event_id FROM event_entities WHERE entity_id=?", (f,)).fetchall():
                jr("tag", e, "dup" if s.db.execute("SELECT 1 FROM event_entities WHERE event_id=? AND entity_id=?", (e, t)).fetchone() else "moved")
            s.db.execute("UPDATE OR IGNORE event_entities SET entity_id=? WHERE entity_id=?", (t, f))
            s.db.execute("DELETE FROM event_entities WHERE entity_id=?", (f,))
            row = s.db.execute("SELECT kind, name FROM entities WHERE id=?", (f,)).fetchone()
            key = json.dumps([row["kind"], norm(row["name"])])
            prev = s.db.execute("SELECT entity_id FROM entity_aliases WHERE kind=? AND alias=?", (row["kind"], norm(row["name"]))).fetchone()
            jr("alias_prev", key, prev[0]) if prev else jr("alias_added", key)
            s.db.execute("INSERT INTO entity_merges VALUES(?,?,?)", (f, t, now))
            s.db.commit()
            self.add_alias(row["kind"], row["name"], t, force=True)
        return t

    def unmerge(self, entity_id):
        """Undo a merge made by merge(): every moved row goes back to the entity, the entity is live again. Rows created after the
        merge stay where they are. Returns how many rows went back. ValueError if it was not merged or has no journal."""
        s = self.s
        with s.lock:
            m = s.db.execute("SELECT into_id FROM entity_merges WHERE entity_id=?", (entity_id,)).fetchone()
            if not m:
                raise ValueError(f"entity {entity_id} is not merged")
            rows = s.db.execute("SELECT * FROM merge_moves WHERE entity_id=? ORDER BY id DESC", (entity_id,)).fetchall()
            if not rows:
                raise ValueError(f"entity {entity_id} was merged without a journal; it cannot be undone")
            f, t = entity_id, self.canonical(m[0])
            for r in rows:  # newest first, so each step undoes exactly what the later one did
                k, key, extra = r["tbl"], r["key"], r["extra"]
                if k in self._MOVES:
                    tbl, col = self._MOVES[k]
                    s.db.execute(f"UPDATE {tbl} SET {col}=? WHERE id=?", (f, int(key)))
                elif k == "state_closed":  # reopen only if no other value became current meanwhile (state() and snapshot() must agree)
                    s.db.execute("UPDATE entity_states SET valid_to=NULL WHERE id=? AND valid_to=? AND NOT EXISTS (SELECT 1 FROM entity_states x "
                                 "WHERE x.entity_id=entity_states.entity_id AND x.field=entity_states.field AND x.valid_to IS NULL)", (int(key), extra))
                elif k == "rel_closed":
                    s.db.execute("UPDATE relationships SET valid_to=NULL WHERE id=? AND valid_to=? AND NOT EXISTS (SELECT 1 FROM relationships x "
                                 "WHERE x.subject_id=relationships.subject_id AND x.relation=relationships.relation AND x.valid_to IS NULL)", (int(key), extra))
                elif k == "alias":
                    s.db.execute("UPDATE entity_aliases SET entity_id=? WHERE kind=? AND alias=?", (f, *json.loads(key)))
                elif k == "alias_prev":
                    s.db.execute("UPDATE entity_aliases SET entity_id=? WHERE kind=? AND alias=?", (int(extra), *json.loads(key)))
                elif k == "alias_added":
                    s.db.execute("DELETE FROM entity_aliases WHERE kind=? AND alias=?", tuple(json.loads(key)))
                elif k == "tag":
                    if extra == "moved":
                        s.db.execute("UPDATE event_entities SET entity_id=? WHERE event_id=? AND entity_id=?", (f, int(key), t))
                    else:
                        s.db.execute("INSERT OR IGNORE INTO event_entities VALUES(?,?)", (int(key), f))
            s.db.execute("DELETE FROM merge_moves WHERE entity_id=?", (f,))
            s.db.execute("DELETE FROM entity_merges WHERE entity_id=?", (f,))
            s.db.commit()
        return len(rows)

    # ---- relationships ----
    def relate(self, subject_id, relation, object_id, source_id=None, ts=None, confidence=1.0, day=None):
        """Returns the new row id, or None if it was a no-op or got queued instead."""
        subject_id, object_id = self.canonical(subject_id), self.canonical(object_id)
        relation = norm(relation).replace(" ", "_")
        if subject_id == object_id or not relation:
            raise ValueError("a relationship needs two different entities and a relation")
        ts, s = to_utc(ts) if ts else _now(), self.s
        with s.lock:
            open_rows = s.db.execute("SELECT * FROM relationships WHERE subject_id=? AND relation=? AND valid_to IS NULL",
                                     (subject_id, relation)).fetchall()
            same = [r for r in open_rows if r["object_id"] == object_id]
            if same:
                if ts > (same[0]["last_seen"] or same[0]["valid_from"]):  # stated again: remember when
                    s.db.execute("UPDATE relationships SET last_seen=? WHERE id=?", (ts, same[0]["id"]))
                    s.db.commit()
                return None
            if relation in EXCLUSIVE and open_rows:
                if ts < max(open_rows[0]["valid_from"], open_rows[0]["last_seen"] or ""):  # an older fact would overlap the current one: ask, don't guess
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

    def path(self, a, b, at=None, max_hops=4):
        """Shortest chain of links between two entities that were true at `at` (default now): a list of
        {"from", "relation", "to", "forward"} steps, [] if they are the same entity, None if no chain within max_hops."""
        a, b = self.canonical(a), self.canonical(b)
        if a == b:
            return []
        seen, frontier = {a: None}, [a]
        for _ in range(max_hops):
            nxt = []
            for e in frontier:
                for r in self.neighbors(e, at):
                    o = self.canonical(r["object_id"] if self.canonical(r["subject_id"]) == e else r["subject_id"])
                    if o in seen:
                        continue
                    seen[o] = (e, r)
                    if o == b:
                        steps, cur = [], b
                        name = lambda i: self.s.db.execute("SELECT name FROM entities WHERE id=?", (i,)).fetchone()[0]
                        while seen[cur]:
                            p, rel = seen[cur]
                            steps.append({"from": name(p), "relation": rel["relation"], "to": name(cur), "forward": self.canonical(rel["subject_id"]) == p})
                            cur = p
                        return steps[::-1]
                    nxt.append(o)
            frontier = nxt
        return None

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
    su = g.resolve("person", "Priya")
    assert g.resolve("person", "  priya ") == su                      # case/space
    assert g.resolve("person", "Preeya") == su                        # close spelling -> alias, no new entity
    assert n("entity_aliases") == 1 and n("entities") == 1
    ro, ri = g.resolve("person", "Rohan"), g.resolve("person", "Rohit")
    assert ro != ri                                                    # different people stay different
    assert g.resolve("project", "Priya") != su                        # kinds never mix
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
    # tags + finding names in a question
    me = g.resolve("person", "Meera")
    e1 = s.add_event("note", "likes cyan")
    g.tag(e1, me)
    assert [e["content"] for e in g.events_about(me)] == ["likes cyan"]
    found, maybe = g.find_in_text("Whats Mee's fav colour?")
    assert found == {} and maybe == [("Mee", me)]                      # unknown nickname: ask, never assume
    assert g.find_in_text("Pri called")[1] == []                        # ambiguous (person + project Priya): no guess
    g.confirm_alias("Mee", me)                                         # user said yes
    assert g.find_in_text("Whats Mee's fav colour?") == ({me: "Meera"}, [])
    assert g.find_in_text("a meeting at noon")[1] == []                # lower-case words are not names
    s.set_state(ro, "mood", "ok")
    s.add_event("note", "hi", entity_id=ro)
    n_ent = n("entities")
    g.tag(e2 := s.add_event('note', 'x'), ro)
    g.merge(ro, ri)                                                    # reversible merge: row kept, marked
    assert g.canonical(ro) == ri and g.resolve("person", "Rohan") == ri and n("entities") == n_ent
    assert [e["id"] for e in g.events_about(ri)] == [e2] and s.snapshot(ri) == {"mood": "ok"} and len(g.history(su, fred)) == 1
    # undo the merge: everything goes back, things created after the merge stay with the merged-into entity
    e3 = s.add_event("note", "after the merge"); g.tag(e3, ri)
    back = g.unmerge(ro)
    assert back >= 3 and g.canonical(ro) == ro and not s.db.execute("SELECT 1 FROM entity_merges").fetchone()
    assert [e["id"] for e in g.events_about(ro)] == [e2] and [e["id"] for e in g.events_about(ri)] == [e3]
    assert s.snapshot(ro) == {"mood": "ok"} and s.snapshot(ri) == {} and g.resolve("person", "Rohan") == ro
    try:
        g.unmerge(ro)
        raise SystemExit("unmerge of a live entity accepted")
    except ValueError:
        pass
    s.set_state(ri, "mood", "great")
    g.merge(ro, ri)                                                    # merge again with a clashing state: the target's value wins, undo restores both
    assert s.snapshot(ri) == {"mood": "great"}
    g.unmerge(ro)
    assert s.snapshot(ri) == {"mood": "great"} and s.snapshot(ro) == {"mood": "ok"} and g.canonical(ro) == ro
    # multi-hop: Priya -works on-> FRED <-works on- Meera; Rohit knows nobody
    g.relate(me, "works on", fred, ts="2026-01-01T00:00:00+00:00")
    p = g.path(su, me)
    assert [(x["from"], x["relation"], x["to"], x["forward"]) for x in p] == [("Priya", "works_on", "FRED", True), ("FRED", "works_on", "Meera", False)]
    assert g.path(su, su) == [] and g.path(su, ri) is None and g.path(su, me, max_hops=1) is None
    assert g.path(su, me, at="2025-01-01T00:00:00+00:00") is None       # the chain did not exist yet
    # review fixes: different people are asked about, never silently merged; a confirmed alias cannot be re-pointed by a model
    for known, new in (("Manish", "Manisha"), ("Nikhil", "Nikhila"), ("Aditya", "Aditi"), ("Mohan", "Mohana"), ("Sara", "Sarah")):
        a1 = g.resolve("person", known)
        assert g.resolve("person", new) != a1, (known, new)
    pa = g.resolve("person", "Pranay")
    assert g.resolve("person", "Pranav") != pa and g.resolve("person", "Rahil") != g.resolve("person", "Rahul")   # one letter apart is a different person
    ni = g.resolve("person", "Nitya")
    assert g.resolve("person", "Nithya") == ni                                                                    # h/doubled letters are only spelling
    zk, zr = g.resolve("person", "Zara Khan"), g.resolve("person", "Zoya Rao")
    g.confirm_alias("Mom", zk)
    assert g.add_alias("person", "Mom", zr) is False and g.resolve("person", "Mom") == zk          # refused, queued as a question
    assert s.db.execute("SELECT COUNT(*) FROM review_queue WHERE text LIKE 'The name ''Mom'' already belongs%'").fetchone()[0] == 1
    assert g.add_alias("person", "Mom", zr, force=True) and g.resolve("person", "Mom") == zr        # Vatsal's own decision may
    print("graph ok")
