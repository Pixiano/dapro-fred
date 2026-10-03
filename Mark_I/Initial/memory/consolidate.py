"""Mark I memory, Phase 3: nightly consolidation of one day (spec 14.2). Standalone; wired at integration.

Sockets:
  - llm(prompt) -> str: injected, must return JSON. Production passes the nightly model, tests a fake.
  - consolidate_day(mem, day, llm, model): reads that UTC day's archive + events, writes extracted events,
    state updates, the daily log and the review queue, all with a 'consolidation' source for provenance.
    Idempotent: a day with status 'done' is skipped, so a nightly rerun never duplicates events.
  - run_pending(mem, llm, model): every day before today that has material and no finished run.
    The nightly scheduler is just `run_pending` on a timer (integration step, it needs the real model).
  - Queue: contradictions and open questions land in review_queue (status 'open') for the weekly agent.
ponytail: whole day goes in one prompt, capped at MAX_CHARS; chunk + merge if a day outgrows the model context.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import hygiene
from graph import Graph

_SCHEMA = Path(__file__).with_name("schema_consolidation.sql")
MAX_CHARS = 60_000
log = logging.getLogger("memory")

PROMPT = """You are FRED's nightly memory consolidation agent. Review {day} and return ONLY JSON:
{{"events": [{{"type": str, "content": str, "importance": 0-1, "about": [{{"kind": str, "name": str}}]}}],
 "states": [{{"kind": "project|goal|task|preference|person|other", "name": str, "field": str, "value": str}}],
 "relationships": [{{"subject": {{"kind": str, "name": str}}, "relation": str, "object": {{"kind": str, "name": str}}}}],
 "aliases": [{{"kind": str, "name": str, "alias": str}}],
 "contradictions": [str], "open_questions": [str],
 "summary": str}}
Only record what the material supports; do not invent. Tag each event with the entities it is about. Put an alias (nickname, short form) in aliases ONLY if the user explicitly said so; if you merely suspect one, add a question to open_questions instead. Reuse a name from CURRENT STATE when someone clearly means the same entity (speech-to-text spells names differently). Flag a contradiction only when new material clearly
conflicts with a current state shown below. Summary: plain prose covering significant events, work, decisions,
people, learning, health/routine, notable changes, open threads. Length follows the day: <=200 words if
uneventful, 200-500 normal, 500-800 active, <=1000 exceptional. A boring day gets a short summary.

CURRENT STATE:
{state}

MATERIAL ({day}):
{material}
"""


def _material(mem, day):
    d = mem.archive.root / day[:4] / day[5:7] / day[8:10]
    recs = [r for p in sorted(d.glob("*.jsonl")) for r in mem.archive.read(p.stem, day)] if d.exists() else []
    recs = [r for r in recs if r.get("kind") != "historical"]  # imported history is undated: stays raw, never mixed into live days
    lines = [f"[{r['ts']}] {r['stream']}: {json.dumps(r['data'], ensure_ascii=False)}" for r in sorted(recs, key=lambda r: r["ts"])]
    lines += [f"[{e['ts']}] event/{e['type']}: {e['content']}" for e in mem.store.events_on(day)]
    return "\n".join(lines)[:MAX_CHARS]


def _state(mem):
    out = []
    for e in mem.store.entities():
        snap = mem.store.snapshot(e["id"])
        out.append(f"{e['kind']}/{e['name']}: {snap}" if snap else f"{e['kind']}/{e['name']}")
    return "\n".join(out) or "(none)"


def consolidate_day(mem, day, llm, model="unset") -> bool:
    """Returns False if skipped (already done or nothing to consolidate)."""
    s = mem.store
    with s.lock:
        s.db.executescript(_SCHEMA.read_text(encoding="utf-8"))
        done = s.db.execute("SELECT 1 FROM consolidation_runs WHERE day=? AND status='done'", (day,)).fetchone()
    material = _material(mem, day)
    if done or not material:
        return False
    ts = f"{day}T23:59:59+00:00"  # extracted facts belong to the day they describe
    try:
        g = Graph(s)
        out = json.loads(llm(PROMPT.format(day=day, state=_state(mem), material=material)))
        sid = s.add_source("consolidation", day)
        for e in out.get("events", []):
            imp = float(e.get("importance", 0.5))
            if imp < hygiene.MIN_IMPORTANCE or hygiene.is_duplicate(s, day, e["content"]):
                continue  # write gate: small talk and repeats stay in the archive only
            ev = s.add_event(e["type"], e["content"], importance=imp, source_id=sid, ts=ts)
            for a in e.get("about", []):
                try:
                    g.tag(ev, g.resolve(a["kind"], a["name"], day, sid))
                except (ValueError, KeyError, TypeError) as ex:
                    log.warning("skipped tag %r: %r", a, ex)
        for st in out.get("states", []):
            s.set_state(g.resolve(st["kind"], st["name"], day, sid), st["field"], st["value"], source_id=sid, ts=ts)
        for a in out.get("aliases", []):
            try:
                g.add_alias(a["kind"], a["alias"], g.resolve(a["kind"], a["name"], day, sid))
            except (ValueError, KeyError, TypeError) as ex:
                log.warning("skipped alias %r: %r", a, ex)
        for r in out.get("relationships", []):
            try:
                g.relate(g.resolve(r["subject"]["kind"], r["subject"]["name"], day, sid), r["relation"],
                         g.resolve(r["object"]["kind"], r["object"]["name"], day, sid), source_id=sid, ts=ts, day=day)
            except (ValueError, KeyError, TypeError) as ex:  # one bad row must not lose the rest of the day
                log.warning("skipped relationship %r: %r", r, ex)
        with s.lock:
            for kind, key in (("contradiction", "contradictions"), ("question", "open_questions")):
                for text in out.get(key, []):
                    s.db.execute("INSERT INTO review_queue(day,kind,text,source_id) VALUES(?,?,?,?)", (day, kind, text, sid))
            s.db.commit()
        s.set_daily_log(day, out["summary"])
        status, detail = "done", None
    except Exception as ex:  # bad model output must not wedge the nightly run; it retries next night
        log.exception("consolidation of %s failed", day)
        status, detail = "failed", repr(ex)
    with s.lock:
        s.db.execute("INSERT OR REPLACE INTO consolidation_runs VALUES(?,?,?,?,?)",
                     (day, model, status, detail, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        s.db.commit()
    return status == "done"


def run_pending(mem, llm, model="unset") -> list:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with mem.store.lock:
        days = {r[0] for r in mem.store.db.execute("SELECT day FROM archive_files")}
        days |= {r[0] for r in mem.store.db.execute("SELECT DISTINCT substr(ts,1,10) FROM events")}
    return [d for d in sorted(days) if d < today and consolidate_day(mem, d, llm, model)]


if __name__ == "__main__":  # self-check: python consolidate.py
    logging.getLogger("memory").setLevel(logging.CRITICAL)
    import tempfile
    from manager import Memory
    good = json.dumps({"events": [{"type": "decision", "content": "chose SQLite", "importance": 0.8, "about": [{"kind": "person", "name": "Preeya"}]},
                          {"type": "decision", "content": "Chose  sqlite", "importance": 0.8},   # duplicate
                          {"type": "note", "content": "said hi", "importance": 0.1}],            # below the gate
                       "states": [{"kind": "project", "name": "FRED", "field": "phase", "value": "3"}],
                       "aliases": [{"kind": "person", "name": "Priya", "alias": "Pri"}],
                       "relationships": [{"subject": {"kind": "person", "name": "Priya"}, "relation": "works on", "object": {"kind": "project", "name": "FRED"}},
                                         {"subject": {"kind": "person", "name": "Preeya"}, "relation": "knows", "object": {"kind": "person", "name": "Preeya"}}],
                       "contradictions": ["phase was 2"], "open_questions": ["which model?"], "summary": "Quiet build day."})
    with tempfile.TemporaryDirectory() as d, Memory(d) as m:
        m.archive.write("conversation", {"text": "let's use sqlite"}, ts="2026-01-01T10:00:00+00:00")
        m.archive.write("import", {"text": "old chat"}, ts="2026-01-02T10:00:00+00:00", kind="historical")
        assert not consolidate_day(m, "2026-01-02", lambda p: good)          # nothing to do (historical records are skipped)
        assert not consolidate_day(m, "2026-01-01", lambda p: "not json")    # failure recorded, no writes
        assert m.store.daily_log("2026-01-01") is None
        assert run_pending(m, lambda p: good) == ["2026-01-01"]              # retries the failed day
        assert m.store.daily_log("2026-01-01") == "Quiet build day."
        fred = m.store.entity("project", "FRED")
        assert m.store.snapshot(fred) == {"phase": "3"}
        assert run_pending(m, lambda p: good) == []                          # idempotent
        n = lambda t: m.store.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        assert n("review_queue") == 2 and n("events") == 1
        assert n("event_entities") == 1 and m.store.db.execute("SELECT alias FROM entity_aliases WHERE alias='pri'").fetchone()
        assert n("relationships") == 1 and n("entities") == 2            # self-link skipped, Preeya = Priya
    print("consolidate ok")
