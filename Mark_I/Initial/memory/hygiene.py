"""Mark I memory: hygiene, so everything going into events and the graph does not pile up. Nothing is deleted:
the archive keeps it all, hygiene only decides what normal search and the review list show.

Sockets (all numbers are knobs, to be tuned after a week or more of real use):
  - MIN_IMPORTANCE / is_duplicate(): the write gate, called by consolidate.py before saving an event.
  - run_hygiene(mem): run nightly after consolidation. Goes cold: old, unimportant, not recently recalled
    events. Expires: open review items past REVIEW_DAYS. Closes: weak (low-confidence) links past WEAK_DAYS.
  - Cold events are skipped by SemanticIndex.search() and come back warm when recalled (include_cold=True).
ponytail: entities mentioned once and never again are not cooled yet, add when the entity list gets noisy.
"""
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import graph  # noqa: F401  (its schema creates review_queue/relationships)

MIN_IMPORTANCE = 0.3   # below this a nightly event stays in the archive only
DUP_SIM = 0.9          # same-day events this alike are duplicates
COLD_DAYS, KEEP_IMPORTANCE = 60, 0.7   # older than this, below this importance, not recalled lately -> cold
REVIEW_DAYS = 30       # open review items older than this expire
WEAK_CONF, WEAK_DAYS = 0.4, 30         # links below this confidence, older than this, are closed


def ensure(store):
    graph.Graph(store)  # review_queue + relationships
    with store.lock:
        store.db.executescript((Path(__file__).with_name("schema_hygiene.sql")).read_text(encoding="utf-8"))


def is_duplicate(store, day, content) -> bool:
    c = " ".join(content.lower().split())
    return any(SequenceMatcher(None, c, " ".join(e["content"].lower().split())).ratio() >= DUP_SIM
               for e in store.events_on(day))


def run_hygiene(mem, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    iso = lambda d: (now - timedelta(days=d)).isoformat(timespec="seconds")
    s, out = mem.store, {}
    ensure(s)
    with s.lock:
        cur = s.db.execute(
            "INSERT OR IGNORE INTO cold_events SELECT id, ? FROM events WHERE ts < ? AND importance < ? "
            "AND id NOT IN (SELECT event_id FROM event_recalls WHERE last_recalled >= ?)",
            (now.isoformat(timespec="seconds"), iso(COLD_DAYS), KEEP_IMPORTANCE, iso(COLD_DAYS)))
        out["cooled"] = cur.rowcount
        out["expired"] = s.db.execute("UPDATE review_queue SET status='expired' WHERE status='open' AND day < ?",
                                      (iso(REVIEW_DAYS)[:10],)).rowcount
        out["weak_links_closed"] = s.db.execute(
            "UPDATE relationships SET valid_to=? WHERE valid_to IS NULL AND confidence < ? AND valid_from < ?",
            (now.isoformat(timespec="seconds"), WEAK_CONF, iso(WEAK_DAYS))).rowcount
        s.db.commit()
    return out


if __name__ == "__main__":  # self-check: python hygiene.py
    import tempfile
    from manager import Memory
    now = datetime(2026, 12, 1, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as d, Memory(d) as m:
        s = m.store
        ensure(s)
        old, keep, recalled = (s.add_event("n", t, importance=i, ts="2026-08-01T00:00:00+00:00")
                               for t, i in (("old minor", 0.4), ("old important", 0.9), ("old but recalled", 0.4)))
        s.add_event("n", "fresh minor", importance=0.4, ts="2026-11-30T00:00:00+00:00")
        ensure(s)
        g = graph.Graph(s)
        a, b = s.entity("person", "A"), s.entity("person", "B")
        g.relate(a, "knows", b, confidence=0.2, ts="2026-10-01T00:00:00+00:00")
        s.db.execute("INSERT INTO review_queue(day,kind,text) VALUES('2026-09-01','question','old?')")
        s.db.execute("INSERT INTO review_queue(day,kind,text) VALUES('2026-11-28','question','new?')")
        s.db.execute("INSERT INTO event_recalls VALUES(?,?)", (recalled, "2026-11-20T00:00:00+00:00"))
        s.db.commit()
        assert run_hygiene(m, now) == {"cooled": 1, "expired": 1, "weak_links_closed": 1}
        assert [r[0] for r in s.db.execute("SELECT event_id FROM cold_events")] == [old]
        assert run_hygiene(m, now) == {"cooled": 0, "expired": 0, "weak_links_closed": 0}   # idempotent
        assert is_duplicate(s, "2026-08-01", "Old  minor") and not is_duplicate(s, "2026-08-01", "something else")
    print("hygiene ok")
