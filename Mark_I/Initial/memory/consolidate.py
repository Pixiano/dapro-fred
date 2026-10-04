"""Mark I memory, Phase 3: nightly consolidation of one day (spec 14.2). Standalone; wired at integration.

Sockets:
  - llm(prompt) -> str: injected, must return JSON. Production passes the nightly model, tests a fake.
  - consolidate_day(mem, day, llm, model): reads that UTC day's archive + events, writes extracted events,
    state updates, the daily log and the review queue, all with a 'consolidation' source for provenance.
    Idempotent: a day with status 'done' is skipped, so a nightly rerun never duplicates events.
  - run_pending(mem, llm, model): every day before today that has material and no finished run.
    The nightly scheduler is just `run_pending` on a timer (integration step, it needs the real model).
  - Queue: contradictions and open questions land in review_queue (status 'open') for the weekly agent.
A day longer than MAX_CHARS goes to the model in parts (summaries joined, nothing written until every part is valid).
  - Backfill: imported history (kind "historical") stays raw unless Vatsal vouches for its dates with trust_dates(); backfill_pending then
    consolidates those days (oldest first) without today's state and without contradictions.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import hygiene
from decisionlog import note, traced
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


def _material(mem, day, historical=False):
    """Live day: the archive records and the day's events. Historical (backfill): only the imported records. The two never mix."""
    d = mem.archive.root / day[:4] / day[5:7] / day[8:10]
    recs = [r for p in sorted(d.glob("*.jsonl")) for r in mem.archive.read(p.stem, day)] if d.exists() else []
    recs = [r for r in recs if (r.get("kind") == "historical") == historical]
    lines = [f"[{r['ts']}] {r['stream']}: {json.dumps(r['data'], ensure_ascii=False)}" for r in sorted(recs, key=lambda r: r["ts"])]
    if not historical:  # events that backfill wrote for this day are not live material
        with mem.store.lock:
            evs = mem.store.db.execute("SELECT e.* FROM events e LEFT JOIN sources s ON s.id=e.source_id WHERE e.ts LIKE ? AND COALESCE(s.kind,'')!='backfill' ORDER BY e.ts, e.id", (day + "%",)).fetchall()
        lines += [f"[{e['ts']}] event/{e['type']}: {e['content']}" for e in evs]
    return "\n".join(lines)


def _chunks(text):
    """Pieces of at most MAX_CHARS, cut at line ends (a single huge line is cut hard). A normal day is one piece."""
    out, cur = [], ""
    for ln in text.split("\n"):
        while len(ln) > MAX_CHARS:
            if cur:
                out.append(cur)
                cur = ""
            out.append(ln[:MAX_CHARS])
            ln = ln[MAX_CHARS:]
        if cur and len(cur) + 1 + len(ln) > MAX_CHARS:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{ln}" if cur else ln
    return out + [cur] if cur else out


STATE_CHARS = 8000  # the entity list in the prompt must not outgrow the model's window as memory grows


def _pick(mem, material, line):
    """Entities the day's material names come first, then the newest ones, until the budget is used."""
    ents = mem.store.entities()
    low = material.lower()
    hit = [e for e in ents if e["name"].lower() in low]
    rest = [e for e in reversed(ents) if e not in hit]
    out, used = [], 0
    for e in hit + rest:
        ln = line(e)
        if used + len(ln) > STATE_CHARS and out:
            out.append(f"(... {len(ents) - len(out)} more entities not shown)")
            break
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out) or "(none)"


def _state(mem, material=""):
    def line(e):
        snap = mem.store.snapshot(e["id"])
        return f"{e['kind']}/{e['name']}: {snap}" if snap else f"{e['kind']}/{e['name']}"
    return _pick(mem, material, line)


def _names(mem, material=""):
    return "(names only: this day is imported history, so today's state is not shown and nothing is a contradiction)\n" + _pick(
        mem, material, lambda e: f"{e['kind']}/{e['name']}")


def _valid(out):
    """Structure check of one model answer BEFORE any write, so a malformed item cannot leave a day half-applied."""
    if not isinstance(out, dict):
        raise ValueError("model answer is not an object")
    str(out["summary"])
    is_s = lambda *xs: all(isinstance(x, str) and x.strip() for x in xs)
    ent = lambda d: isinstance(d, dict) and is_s(d.get("kind"), d.get("name"))
    for key in ("events", "states", "aliases", "relationships", "contradictions", "open_questions"):
        if not isinstance(out.get(key, []), list):
            raise ValueError(f"{key} must be a list")
    for e in out.get("events", []):
        if not (isinstance(e, dict) and is_s(e.get("type"), e.get("content"))):
            raise ValueError(f"bad event {e!r}")
        float(e.get("importance", 0.5))
        if not (isinstance(e.get("about", []), list) and all(ent(a) for a in e.get("about", []))):
            raise ValueError(f"bad event tags {e!r}")
    for st in out.get("states", []):
        if not (ent(st) and is_s(st.get("field")) and isinstance(st.get("value"), (str, int, float))):
            raise ValueError(f"bad state {st!r}")
    for a in out.get("aliases", []):
        if not (ent(a) and is_s(a.get("alias"))):
            raise ValueError(f"bad alias {a!r}")
    for r in out.get("relationships", []):
        if not (isinstance(r, dict) and ent(r.get("subject")) and ent(r.get("object")) and is_s(r.get("relation"))):
            raise ValueError(f"bad relationship {r!r}")
    for key in ("contradictions", "open_questions"):
        if not all(isinstance(x, str) for x in out.get(key, [])):
            raise ValueError(f"{key} must be text")


def _apply(s, g, out, day, ts, sid, agent, run, historical):
    """Writes one model answer. Returns its summary."""
    for e in out.get("events", []):
        imp = float(e.get("importance", 0.5))
        if imp < hygiene.MIN_IMPORTANCE or hygiene.is_duplicate(s, day, e["content"]):
            note(s, agent, run, "override", why="write gate", event=e)
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
            if historical and kind == "contradiction":
                continue  # old facts cannot contradict the present
            for text in out.get(key, []):
                s.db.execute("INSERT INTO review_queue(day,kind,text,source_id) SELECT ?,?,?,? WHERE NOT EXISTS "
                             "(SELECT 1 FROM review_queue WHERE day=? AND kind=? AND text=? AND status='open')", (day, kind, text, sid, day, kind, text))  # a retry must not duplicate
        s.db.commit()
    return out["summary"]


def consolidate_day(mem, day, llm, model="unset", historical=False) -> bool:
    """Returns False if skipped (already done or nothing to consolidate). historical=True is backfill: it reads only the imported
    records of that day (kind "historical"), see backfill_pending. A day longer than MAX_CHARS goes to the model in parts; every part is
    answered and checked BEFORE anything is written, so a failed part leaves nothing half-applied."""
    s = mem.store
    key, agent = (f"{day}#backfill", "backfill") if historical else (day, "consolidate")
    with s.lock:
        s.db.executescript(_SCHEMA.read_text(encoding="utf-8"))
        done = s.db.execute("SELECT 1 FROM consolidation_runs WHERE day=? AND status='done'", (key,)).fetchone()
    if done:
        return False  # nothing is read for a finished day
    ts = f"{day}T23:59:59+00:00"  # extracted facts belong to the day they describe
    try:
        material = _material(mem, day, historical)
        if not material:
            return False
        g = Graph(s)
        parts, state = _chunks(material), _names(mem, material) if historical else _state(mem, material)
        outs = []
        for i, part in enumerate(parts):
            head = f"(PART {i + 1} of {len(parts)} of this day)\n" if len(parts) > 1 else ""
            out = json.loads(traced(s, agent, key, llm)(PROMPT.format(day=day, state=state, material=head + part)))
            _valid(out)  # a malformed part fails the whole day before any write
            outs.append(out)
        sid = s.add_source("backfill" if historical else "consolidation", day)
        summary = "\n\n".join(_apply(s, g, o, day, ts, sid, agent, key, historical) for o in outs)
        old = s.daily_log(day) if historical else None
        s.set_daily_log(day, f"{old}\n\n(Imported history) {summary}" if old else summary)
        status, detail = "done", None
    except Exception as ex:  # bad model output must not wedge the nightly run; it retries next night
        log.exception("consolidation of %s failed", key)
        status, detail = "failed", repr(ex)
    with s.lock:
        s.db.execute("INSERT OR REPLACE INTO consolidation_runs VALUES(?,?,?,?,?)",
                     (key, model, status, detail, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        s.db.commit()
    return status == "done"


def trust_dates(mem, since, until, note_text=""):
    """Vatsal vouches that imported history dated since..until (YYYY-MM-DD, inclusive) really happened on those days.
    Only then can backfill_pending consolidate it. Never called automatically."""
    d1, d2 = datetime.fromisoformat(since).date(), datetime.fromisoformat(until).date()
    if d2 < d1:
        raise ValueError("until is before since")
    with mem.store.lock:
        mem.store.db.executescript(_SCHEMA.read_text(encoding="utf-8"))
        cur = mem.store.db.execute("INSERT INTO backfill_trust(since,until,note,at) VALUES(?,?,?,?)",
                                   (d1.isoformat(), d2.isoformat(), note_text, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        mem.store.db.commit()
    return cur.lastrowid


def backfill_pending(mem, llm, model="unset") -> list:
    """Consolidates imported history, oldest day first, but ONLY days inside a trusted range (trust_dates). Everything else stays raw.
    Returns the days done. Weekly/monthly runs then pick the old weeks and months up like any others (they run oldest first).
    ponytail: one trust range check per day by archive record ts; a per-record dating flag can replace it if imports get messy."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with mem.store.lock:
        mem.store.db.executescript(_SCHEMA.read_text(encoding="utf-8"))
        ranges = [(r[0], r[1]) for r in mem.store.db.execute("SELECT since, until FROM backfill_trust")]
        days = sorted(r[0] for r in mem.store.db.execute("SELECT DISTINCT day FROM archive_files"))
    return [d for d in days if d < today and any(a <= d <= b for a, b in ranges) and consolidate_day(mem, d, llm, model, historical=True)]


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
        # ---- long days go in parts; nothing is written unless every part is valid ----
        MAX_CHARS = 120
        assert _chunks("a\nb") == ["a\nb"] and _chunks("") == [] and all(len(c) <= 120 for c in _chunks("\n".join(["x" * 50] * 5))) \
            and "\n".join(_chunks("\n".join(["x" * 50] * 5))) == "\n".join(["x" * 50] * 5) and [len(c) for c in _chunks("y" * 300)] == [120, 120, 60]
        for i in range(4):
            m.archive.write("conversation", {"text": f"message number {i} " + "w" * 40}, ts=f"2026-02-01T10:0{i}:00+00:00")
        seen = []
        part = lambda n: json.dumps({"events": [{"type": "note", "content": ["", "bought a bicycle", "fixed the roof gutter", "wrote a long letter", "planted tomatoes"][n % 5] , "importance": 0.8}], "summary": f"summary {n}"})
        assert consolidate_day(m, "2026-02-01", lambda p: (seen.append(p), part(len(seen)))[1])
        assert len(seen) > 1 and "(PART 1 of" in seen[0] and "(PART 2 of" in seen[1]
        assert m.store.daily_log("2026-02-01") == "\n\n".join(f"summary {i + 1}" for i in range(len(seen)))
        assert n("events") == 1 + len(seen)                                                       # the earlier test event + one per part
        for i in range(4):
            m.archive.write("conversation", {"text": f"second day {i} " + "v" * 40}, ts=f"2026-02-02T10:0{i}:00+00:00")
        calls = []
        def flaky(p):
            calls.append(p)
            return part(len(calls)) if len(calls) == 1 else "not json"
        ev0 = n("events")
        assert not consolidate_day(m, "2026-02-02", flaky) and n("events") == ev0 and m.store.daily_log("2026-02-02") is None   # part 2 failed: part 1 not applied
        MAX_CHARS = 60_000
        # ---- backfill: imported history, only inside date ranges Vatsal vouched for ----
        for d, txt in (("2025-05-01", "old chat one"), ("2025-05-02", "old chat two"), ("2025-06-01", "old chat in june")):
            m.archive.write("import", {"text": txt}, ts=f"{d}T10:00:00+00:00", kind="historical")
        hist = json.dumps({"events": [{"type": "note", "content": "started the project long ago", "importance": 0.8, "about": [{"kind": "project", "name": "FRED"}]}],
                           "states": [{"kind": "project", "name": "FRED", "field": "phase", "value": "1"}],
                           "contradictions": ["conflicts with the present"], "open_questions": ["who was involved?"], "summary": "Early days."})
        sent = []
        hl = lambda p: (sent.append(p), hist)[1]
        assert backfill_pending(m, hl) == []                                                       # nothing trusted yet: everything stays raw
        assert trust_dates(m, "2025-05-01", "2025-05-31", "dated exports")
        try:
            trust_dates(m, "2025-05-31", "2025-05-01")
            raise SystemExit("backwards range accepted")
        except ValueError:
            pass
        rq = n("review_queue")
        assert backfill_pending(m, hl) == ["2025-05-01", "2025-05-02"] and len(sent) == 2          # June is not trusted: stays raw
        assert "imported history" in sent[0] and "names only" in sent[0] and "phase" not in sent[0].split("CURRENT STATE:")[1].split("MATERIAL")[0]
        assert m.store.daily_log("2025-05-01") == "Early days." and m.store.daily_log("2025-06-01") is None
        assert n("review_queue") == rq + 2                                                          # open questions kept, contradictions skipped
        assert m.store.db.execute("SELECT kind FROM sources ORDER BY id DESC LIMIT 1").fetchone()[0] == "backfill"
        assert [r["value"] for r in m.store.state_history(fred, "phase")] == ["1", "3"] and m.store.snapshot(fred) == {"phase": "3"}   # the present is untouched
        assert backfill_pending(m, hl) == [] and len(sent) == 2                                    # idempotent
        assert not consolidate_day(m, "2025-05-01", hl)                                            # the live path still ignores imports
        # a day that has both live data and imported history keeps its live summary and gets the imported one appended
        m.archive.write("import", {"text": "old chat same day"}, ts="2026-01-01T09:00:00+00:00", kind="historical")
        trust_dates(m, "2026-01-01", "2026-01-01")
        assert backfill_pending(m, hl) == ["2026-01-01"]
        assert m.store.daily_log("2026-01-01") == "Quiet build day.\n\n(Imported history) Early days."
        assert m.archive.read("import", "2025-05-01")[0]["kind"] == "historical"                  # the raw record itself is never touched
    print("consolidate ok")
