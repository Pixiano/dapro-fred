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
import re
import logging
from datetime import datetime, timezone
from pathlib import Path

import hygiene
from decisionlog import note, traced
from graph import Graph

_SCHEMA = Path(__file__).with_name("schema_consolidation.sql")
MAX_CHARS = 60_000
log = logging.getLogger("memory")
USER = "Vatsal"  # whose memory this is; first-person facts attach to person/USER (tests of other people's data set it)
KINDS = {"person", "place", "project", "pet", "org", "task", "goal"}  # anything else is topic noise ("concept", "education", "time check")
RELATIVE = ("tomorrow", "today", "tonight", "yesterday", "now", "next week", "this week", "later")  # a state is a lasting fact, never a relative time
MAX_QUESTIONS = 1
ADVICE = re.compile(r"(should|how (to|can|do|might)|will|would|could|can|shall) ", re.I)   # advice, plans and predictions are not doubts about what is stored
RELATIONS = {"parent_of", "sibling_of", "cousin_of", "friend_of", "teammate_of", "mentor_of", "tutors", "helps_with", "works_on", "works_at", "lives_in", "studies_at", "owns", "pet_of"}
RELATION_ALIASES = {"has_sister": "sibling_of", "has_brother": "sibling_of", "sister_of": "sibling_of", "brother_of": "sibling_of", "sibling": "sibling_of",
                    "mother_of": "parent_of", "father_of": "parent_of", "mom_of": "parent_of", "has_parent": "parent_of", "best_friend_of": "friend_of",
                    "friends_with": "friend_of", "friend": "friend_of", "teacher_of": "mentor_of", "teaches": "tutors", "works_with": "teammate_of"}

PROMPT = """You are FRED's nightly memory consolidation agent. Review {day} and return ONLY JSON:
{{"events": [{{"type": str, "content": str, "importance": 0-1, "about": [{{"kind": str, "name": str}}]}}],
 "states": [{{"kind": "project|goal|task|preference|person|other", "name": str, "field": str, "value": str}}],
 "relationships": [{{"subject": {{"kind": str, "name": str}}, "relation": str, "object": {{"kind": str, "name": str}}}}],
 "aliases": [{{"kind": str, "name": str, "alias": str}}],
 "corrections": [{{"kind": str, "wrong": str, "right": str}}],
 "contradictions": [str], "open_questions": [str],
 "summary": str}}
Only record what the material supports; do not invent. The user is {user}: what they say about themselves ("I", "my") is about person "{user}", always under that exact name, never "user". Events and states are about the user's life, plans, people, projects, decisions and stated facts. Do NOT record what FRED explained, general knowledge or homework answers as events or states. Entity kinds are only person, place, project, pet, org, task, goal (likes, dislikes and habits are states on the person, never entities): no topic, concept, subject or time-check entities. A state value is a lasting fact, never a relative time ("tomorrow", "today"); a one-off plan is an event with its date. Flag a contradiction only when a new value for the same entity and field differs from CURRENT STATE. At most ONE open_question per day, only for a possible same-person pair, a clash with a stored fact or a clear mishearing, never trivia or next steps. Write each contradiction as "ENTITY FIELD: current state says X, but now Y" quoting CURRENT STATE; anything else is not a contradiction. One standalone fact per event; merge the steps of one decision into one event; usually 1-2 events a day, at most 4; no weather or filler. A relationship reads "subject RELATION object" = the subject IS the RELATION of the object ("Mom parent_of {user}", "Mr. Okhande mentor_of {user}", "{user} tutors Chintu"); keep the direction right. Record each person's relation to {user} the first time they appear, with relations only from: parent_of, sibling_of, cousin_of, friend_of, teammate_of, mentor_of, tutors, helps_with, works_on, works_at, lives_in, studies_at, owns, pet_of. Name a person by their formal form (Mom for mumma/ma, Mr. Okhande for "Okhande sir"), and put the other spoken forms in aliases when the user said them. Tag each event with the entities it is about. Put an alias (nickname, short form) in aliases ONLY if the user explicitly said so; if you merely suspect one, add a question to open_questions instead. corrections: ONLY when {user} explicitly fixes a name used before ("my sister is Nitya, not Navya"): wrong = the old name, right = the correct one, both exactly as written in the material. A different person who merely has a similar name is not a correction. For a state, reuse the exact field name already shown for that entity in CURRENT STATE when the new value is about the same fact (field "coffee", not a new "drink_preference"), so a later different value replaces the old one and a clash is seen. Reuse a name from CURRENT STATE when someone clearly means the same entity (speech-to-text spells names differently). Flag a contradiction only when new material clearly
conflicts with a current state shown below. Summary: plain prose covering significant events, work, decisions,
people, learning, health/routine, notable changes, open threads. Length follows the day: <=200 words if
uneventful, 200-500 normal, 500-800 active, <=1000 exceptional. A boring day gets a short summary.

CURRENT STATE:
{state}

MATERIAL ({day}):
{material}
"""


CHECK = """You check what {user} said on {day} against facts already stored in memory. Treat each stored fact as true and ask: could everything in the material still be true as well? Quote the exact words of the material that bear on the fact and compare numbers, dates, ages, places and "never/always" by calculation (a stored age of 12 against "I started class 12 two years ago", "lives in Jaipur" against "walked to school here in Pune every day"). The material may imply something without naming the fact. For EACH numbered stored fact write in "why" what the material says about it, then give one verdict:
confirmed = the material says the same; not_mentioned = the material really says nothing about its subject;
changed = {user} genuinely changed his mind or the situation moved on after the fact was stored (a new opinion, a new school, a new value), give the new value in "new";
conflict = the material and the stored fact cannot both be true and it is no change over time (a past-tense claim about a time before the fact was stored is never a change).
A change of mind or taste is "changed", never "conflict". Use only the material. Return ONLY JSON: {{"checks": [{{"fact": n, "why": str, "verdict": "confirmed|changed|conflict|not_mentioned", "new": str or null}}]}}

STORED FACTS:
{facts}

MATERIAL ({day}):
{material}
"""
CHECK_FACTS = 40  # facts shown to the check call


def _facts(mem, day, material):
    """Stored facts about the entities the material names (and the user): current states and the most important earlier events, for the fact check."""
    s, g, low, out = mem.store, Graph(mem.store), material.lower(), []
    for e in s.entities():
        if e["name"] == USER or e["name"].lower() in low:
            tag = f"{e['kind']}/{e['name']}"
            with s.lock:   # the state as it stood before this day, so the check also works on a day that is already stored
                before = {r["field"]: r["value"] for r in s.db.execute("SELECT field, value FROM entity_states WHERE entity_id=? AND valid_from < ? ORDER BY valid_from", (e["id"], day))}
            out += [{"e": e["id"], "field": f, "value": v, "text": f"{tag}: {f} = {v}", "tag": tag} for f, v in before.items()]
            out += [{"e": e["id"], "field": None, "value": ev["content"], "text": f"{tag}: on {ev['ts'][:10]}: {ev['content']}", "tag": tag}
                    for ev in g.events_about(e["id"], 5) if ev["ts"][:10] < day]
    return out[:CHECK_FACTS]


def _check(mem, day, material, llm):
    """Second nightly call: each stored fact about today's entities against today's material. Returns the verdicts that need action (changed, conflict).
    A broken answer gives no verdicts: the day is never failed by the check."""
    facts = _facts(mem, day, material)
    if not facts:
        return []
    try:
        reply = json.loads(llm(CHECK.format(day=day, user=USER, material=material, facts="\n".join(f"{i + 1}. {f['text']}" for i, f in enumerate(facts)))))
        return [{**facts[int(c["fact"]) - 1], "verdict": c["verdict"], "new": str(c.get("new") or "").strip(), "why": str(c.get("why") or "").strip()}
                for c in reply["checks"] if c.get("verdict") in ("changed", "conflict") and 0 < int(c["fact"]) <= len(facts)]
    except (ValueError, KeyError, TypeError, AttributeError) as ex:
        log.warning("fact check of %s skipped: %r", day, ex)
        return []


def _apply_checks(s, checks, day, ts, sid):
    """conflict -> a contradiction in the review queue (the existing mechanism); changed -> a normal state update, history kept."""
    for c in checks:
        if c["verdict"] == "conflict" and c["why"]:
            text = f"{c['tag']} {c['field'] or 'fact'}: current state says {c['value']}, but now {c['why']}"
            with s.lock:
                s.db.execute("INSERT INTO review_queue(day,kind,text,source_id) SELECT ?,?,?,? WHERE NOT EXISTS "
                             "(SELECT 1 FROM review_queue WHERE day=? AND kind=? AND text=? AND status='open')", (day, "contradiction", text, sid, day, "contradiction", text))
                s.db.commit()
        elif c["verdict"] == "changed" and c["field"] and c["new"]:
            cur = s.state(c["e"], c["field"])
            if cur is not None and cur["value"] == c["value"]:   # the first pass did not already write the new value
                s.set_state(c["e"], c["field"], c["new"], source_id=sid, ts=ts)


def check_day(mem, day, llm):
    """The second pass alone on a day that is already consolidated (so a different model can do it after a model swap). Returns the verdicts applied."""
    raw = "\n".join(l for l in _material(mem, day).split("\n") if "event/" not in l)   # the archive records only, not the extracted events
    checks = _check(mem, day, raw, llm)
    _apply_checks(mem.store, checks, day, f"{day}T23:59:59+00:00", mem.store.add_source("consolidation", day))
    return checks


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


def _clean(out):
    """After the structure check, before any write: drops what is only noise. Events are kept (the gate handles those); tags, states and relationships on
    non-entity kinds, slash-joined or empty names and relative-time values go; 'user' becomes USER; open questions are capped."""
    def ent(d):
        name = d["name"].strip()
        if name.lower() in ("user", "me", "i", "the user"):
            name = USER
        return {**d, "kind": d["kind"].strip().lower(), "name": name}
    ok = lambda d: d["kind"] in KINDS and "/" not in d["name"]
    out["events"] = [{**e, "about": [a for a in map(ent, e.get("about", [])) if ok(a)]} for e in out.get("events", [])]
    out["states"] = [s for s in map(lambda x: {**ent(x)}, out.get("states", []))
                     if ok(s) and str(s["value"]).strip().lower() not in RELATIVE and s["field"].strip().lower() not in ("current day", "today")]
    out["aliases"] = [a for a in map(ent, out.get("aliases", [])) if ok(a)]
    out["relationships"] = [{**r, "subject": ent(r["subject"]), "object": ent(r["object"])} for r in out.get("relationships", [])]
    for r in out["relationships"]:
        rel = r["relation"].strip().lower().replace(" ", "_")
        r["relation"] = RELATION_ALIASES.get(rel, rel)
    out["relationships"] = [r for r in out["relationships"] if ok(r["subject"]) and ok(r["object"]) and r["relation"] in RELATIONS]
    out["contradictions"] = [c for c in out.get("contradictions", []) if "current state says" in c]   # a contradiction must quote the stored fact it clashes with
    out["open_questions"] = [q for q in out.get("open_questions", []) if not ADVICE.match(q.strip())][:MAX_QUESTIONS]
    out["corrections"] = [c for c in out.get("corrections", []) if c["kind"].strip().lower() in KINDS]
    return out


def _valid(out):
    """Structure check of one model answer BEFORE any write, so a malformed item cannot leave a day half-applied."""
    if not isinstance(out, dict):
        raise ValueError("model answer is not an object")
    str(out["summary"])
    is_s = lambda *xs: all(isinstance(x, str) and x.strip() for x in xs)
    ent = lambda d: isinstance(d, dict) and is_s(d.get("kind"), d.get("name"))
    for key in ("events", "states", "aliases", "relationships", "contradictions", "open_questions", "corrections"):
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
    for c in out.get("corrections", []):
        if not (isinstance(c, dict) and is_s(c.get("kind"), c.get("wrong"), c.get("right"))):
            raise ValueError(f"bad correction {c!r}")


FIELD_SIM = 0.85  # cosine of two field names of one entity above which they are one fact (phone 0.89 mobile_phone). Lower is unsafe: favorite_day 0.76 favorite_food, coffee 0.75 chai; drifted names like coffee 0.72 drink_preference are left to the prompt


def _field(s, eid, field, embed):
    """A new field name close to an existing field of the SAME entity is the same fact under a drifted name: reuse the old name, so later
    values collide and contradictions show. embed(text, is_query) is injected; None (the default) maps nothing."""
    old = list(s.snapshot(eid))
    if embed is None or not old or field in old:
        return field
    try:
        v = embed(field, False)
        sims = [(sum(x * y for x, y in zip(v, w)) / ((sum(x * x for x in v) * sum(y * y for y in w)) ** 0.5 or 1), o) for o, w in ((o, embed(o, False)) for o in old)]
    except Exception as ex:  # no embedder, no mapping: the field stays as the model wrote it
        log.warning("field mapping skipped: %r", ex)
        return field
    best = max(sims)
    return best[1] if best[0] >= FIELD_SIM else field


def _apply(s, g, out, day, ts, sid, agent, run, historical, material="", embed=None):
    """Writes one model answer. Returns its summary."""
    low = " ".join(material.lower().split())
    for c in out.get("corrections", []):  # first, so the rest of the answer already sees the corrected names
        wrong, right = c["wrong"].strip(), c["right"].strip()
        if " ".join(wrong.lower().split()) not in low or " ".join(right.lower().split()) not in low:
            note(s, agent, run, "override", why="correction names not in the material", correction=c)
            continue  # the model may not invent a correction: both names must be in what was actually said
        res = g.correct(c["kind"].strip().lower(), wrong, right, day, sid)
        note(s, agent, run, "action", correction=c, result=res)
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
        e = g.resolve(st["kind"], st["name"], day, sid)
        f = _field(s, e, st["field"], embed)
        if f != st["field"]:
            note(s, agent, run, "override", why="field name mapped to the entity's existing field", was=st["field"], now=f)
        s.set_state(e, f, st["value"], source_id=sid, ts=ts)
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


def consolidate_day(mem, day, llm, model="unset", historical=False, embed=None, check_llm=None) -> bool:
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
        outs, checks = [], []
        for i, part in enumerate(parts):
            head = f"(PART {i + 1} of {len(parts)} of this day)\n" if len(parts) > 1 else ""
            out = json.loads(traced(s, agent, key, llm)(PROMPT.format(day=day, state=state, material=head + part, user=USER)))
            for k in ("events", "states", "aliases", "relationships", "corrections"):   # stray strings in a list (gpt-oss ran its keys into an array) carry no data
                if isinstance(out.get(k), list):
                    out[k] = [x for x in out[k] if isinstance(x, dict)]
            for e in out.get("events", []) if isinstance(out.get("events"), list) else []:   # 'about': ["Bruno"] (bare names, no kind) cannot be resolved: the event stays, the link goes
                if isinstance(e, dict) and isinstance(e.get("about"), list):
                    e["about"] = [x for x in e["about"] if isinstance(x, dict)]
            if isinstance(out.get("states"), list):   # an entry with an empty field or value is noise, not a reason to fail the day
                out["states"] = [x for x in out["states"] if not (isinstance(x, dict) and "field" in x and "value" in x and not (str(x["field"]).strip() and str(x["value"]).strip()))]
            _valid(out)  # a malformed part fails the whole day before any write
            outs.append(_clean(out))
            if check_llm and not historical:   # the second pass decides contradictions; the first answer's own flags are dropped
                outs[-1]["contradictions"] = []
                checks += _check(mem, day, part, check_llm)
        sid = s.add_source("backfill" if historical else "consolidation", day)
        summary = "\n\n".join(_apply(s, g, o, day, ts, sid, agent, key, historical, material, embed) for o in outs)
        _apply_checks(s, checks, day, ts, sid)
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
                       "contradictions": ["FRED phase: current state says 3, but now 2"], "open_questions": ["which model?"], "summary": "Quiet build day."})
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
    with tempfile.TemporaryDirectory() as d2, Memory(d2) as m2:   # spoken name correction: merged, the word freed, the model cannot invent one
        m2.archive.write("conversation", {"text": "my sister Navya took my charger"}, ts="2026-02-01T10:00:00+00:00")
        m2.archive.write("conversation", {"text": "my sister is called Nitya, not Navya. Navya from tuition is nice"}, ts="2026-02-02T10:00:00+00:00")
        mk = lambda txt, who, corr=(): json.dumps({"events": [{"type": "note", "content": txt, "importance": 0.8, "about": [{"kind": "person", "name": who}]}],
                                                  "corrections": list(corr), "summary": "s"})
        assert consolidate_day(m2, "2026-02-01", lambda p: mk("Navya took a charger", "Navya"))
        corr = [{"kind": "person", "wrong": "Navya", "right": "Nitya"}, {"kind": "person", "wrong": "Zed", "right": "Nitya"}]   # Zed never appears: ignored
        assert consolidate_day(m2, "2026-02-02", lambda p: mk("tuition Navya is nice", "Navya", corr))
        g2 = Graph(m2.store)
        ids = {r["name"]: r["id"] for r in m2.store.entities("person")}
        assert {"Nitya", "Navya", "Navya [heard as Nitya]"} <= set(ids) and "Zed" not in ids
        about = lambda n: [e["content"] for e in g2.events_about(ids[n])]
        assert about("Nitya") == ["Navya took a charger"] and about("Navya") == ["tuition Navya is nice"]
    q = _clean({"events": [], "states": [], "aliases": [], "relationships": [], "contradictions": [], "summary": "s",
                "open_questions": ["Should I remind Arin to label the charger?", "Will the tutoring go ahead?", "Is person 'Chintu' the same as 'Chhotu'?"]})
    assert q["open_questions"] == ["Is person 'Chintu' the same as 'Chhotu'?"]                              # advice and predictions are not memory doubts
    with tempfile.TemporaryDirectory() as d4, Memory(d4) as m4:   # one empty state entry must not fail the day (gpt-oss did this)
        m4.archive.write("conversation", {"text": "I live in Shanti Nagar"}, ts="2026-02-03T10:00:00+00:00")
        bad = json.dumps({"events": [], "states": [{"kind": "place", "name": "Shanti Nagar", "field": "", "value": ""}], "aliases": [], "relationships": [],
                          "contradictions": [], "open_questions": [], "summary": "s"})
        bad = json.dumps({"events": [], "states": [], "aliases": [], "relationships": [{"subject": {"kind": "person", "name": "A"}, "relation": "sibling_of", "object": {"kind": "person", "name": "B"}}, "contradictions", [], "open_questions"],
                          "contradictions": [], "open_questions": [], "summary": "s"})
        assert consolidate_day(m4, "2026-02-03", lambda p: bad)
        m4.store.db.execute("DELETE FROM consolidation_runs"); m4.store.db.commit()
        bad = json.dumps({"events": [{"type": "note", "content": "Bruno is 9", "importance": 0.5, "about": ["Bruno", "Mira"]}], "states": [], "aliases": [], "relationships": [],
                          "contradictions": [], "open_questions": [], "summary": "s"})
        assert consolidate_day(m4, "2026-02-03", lambda p: bad) and m4.store.db.execute("SELECT COUNT(*) FROM events WHERE content='Bruno is 9'").fetchone()[0] == 1   # bare-name about list: day kept
        m4.store.db.execute("DELETE FROM consolidation_runs"); m4.store.db.commit()
        bad = json.dumps({"events": [], "states": [{"kind": "place", "name": "Shanti Nagar", "field": "", "value": ""}], "aliases": [], "relationships": [],
                          "contradictions": [], "open_questions": [], "summary": "s"})
        assert consolidate_day(m4, "2026-02-03", lambda p: bad) and not m4.store.db.execute("SELECT 1 FROM consolidation_runs WHERE status!='done'").fetchone()
    with tempfile.TemporaryDirectory() as d5, Memory(d5) as m5:   # a drifted field name maps onto the entity's own field, never another entity's
        fake = lambda t, q: [float("coffee" in t or "drink" in t), float("age" in t), float("class" in t)]   # coffee ~ drink_preference; age and class apart
        st = lambda who, f, v: json.dumps({"events": [], "states": [{"kind": "person", "name": who, "field": f, "value": v}], "summary": "s"})
        for i, (who, f, v) in enumerate([("Mira", "coffee", "hates it"), ("Mira", "drink", "likes it now"), ("Mira", "age", "15"), ("Mira", "class", "9"), ("Ravi", "drink", "tea")]):
            m5.archive.write("conversation", {"text": f"day {i}"}, ts=f"2026-03-0{i + 1}T10:00:00+00:00")
            assert consolidate_day(m5, f"2026-03-0{i + 1}", lambda p, a=st(who, f, v): a, embed=fake)
        assert m5.store.snapshot(m5.store.entity("person", "Mira")) == {"coffee": "likes it now", "age": "15", "class": "9"}
        assert m5.store.snapshot(m5.store.entity("person", "Ravi")) == {"drink": "tea"}
        assert [r["value"] for r in m5.store.state_history(m5.store.entity("person", "Mira"), "coffee")] == ["hates it", "likes it now"]
    with tempfile.TemporaryDirectory() as d6, Memory(d6) as m6:   # second pass: conflict is queued, a change of mind updates the state, junk answers change nothing
        for i, t in enumerate(["Mira hates mushrooms", "Mira: mum made mushroom soup, I like mushrooms now", "Mira never flew", "Mira flew to Pune"]):
            m6.archive.write("conversation", {"text": t}, ts=f"2026-04-0{i + 1}T10:00:00+00:00")
        first = json.dumps({"events": [{"type": "note", "content": "Mira hates mushrooms", "importance": 0.8, "about": [{"kind": "person", "name": "Mira"}]}],
                            "states": [{"kind": "person", "name": "Mira", "field": "mushrooms", "value": "hates them"}],
                            "contradictions": ["Mira mushrooms: current state says hates them, but now likes them"], "summary": "s"})
        assert consolidate_day(m6, "2026-04-01", lambda p: first.replace("current state says", "state says"))   # (cleaned away: no queue item yet)
        mira = m6.store.entity("person", "Mira")
        verdict = lambda v, new=None, why="": json.dumps({"checks": [{"fact": 1, "verdict": v, "new": new, "why": why}, {"fact": 99, "verdict": "conflict", "why": "x"}]})
        quiet = json.dumps({"events": [], "summary": "s"})
        assert consolidate_day(m6, "2026-04-02", lambda p: quiet, check_llm=lambda p: verdict("changed", "likes them now"))
        assert m6.store.snapshot(mira) == {"mushrooms": "likes them now"} and not m6.store.db.execute("SELECT 1 FROM review_queue").fetchone()   # change of mind: state history, no contradiction
        assert consolidate_day(m6, "2026-04-03", lambda p: first.replace("hates them", "likes them now"), check_llm=lambda p: verdict("conflict", None, "he says he never flew"))
        q = [r[0] for r in m6.store.db.execute("SELECT text FROM review_queue")]
        assert len(q) == 1 and "current state says likes them now, but now he says he never flew" in q[0]   # the first answer's own contradiction was dropped, the check's queued
        assert consolidate_day(m6, "2026-04-04", lambda p: quiet, check_llm=lambda p: "not json") and m6.store.db.execute("SELECT COUNT(*) FROM review_queue").fetchone()[0] == 1   # a broken check answer does not fail the day
    with tempfile.TemporaryDirectory() as d9, Memory(d9) as m9:   # the check on an already stored day (model swap between extraction and check)
        m9.archive.write("conversation", {"text": "Mira hates mushrooms"}, ts="2026-05-01T10:00:00+00:00")
        m9.archive.write("conversation", {"text": "Mira likes mushrooms now"}, ts="2026-05-02T10:00:00+00:00")
        ex = lambda v: json.dumps({"events": [], "states": [{"kind": "person", "name": "Mira", "field": "mushrooms", "value": v}], "summary": "s"})
        assert consolidate_day(m9, "2026-05-01", lambda p: ex("hates them")) and consolidate_day(m9, "2026-05-02", lambda p: ex("likes them"))
        seen9 = []
        got = check_day(m9, "2026-05-02", lambda p: (seen9.append(p), json.dumps({"checks": [{"fact": 1, "verdict": "changed", "new": "likes them", "why": "w"}]}))[1])
        assert "mushrooms = hates them" in seen9[0] and "likes mushrooms now" in seen9[0] and "event/" not in seen9[0].split("MATERIAL")[1] and [c["verdict"] for c in got] == ["changed"]
    c = _clean({"events": [{"type": "n", "content": "x", "about": [{"kind": "Person", "name": "user"}, {"kind": "concept", "name": "friction"}, {"kind": "person", "name": "Chhotu/PF"}]}],
                "states": [{"kind": "person", "name": "user", "field": "exam", "value": "tomorrow"}, {"kind": "person", "name": "user", "field": "phone", "value": "Redmi"}],
                "aliases": [], "relationships": [{"subject": {"kind": "person", "name": "I"}, "relation": "knows", "object": {"kind": "education", "name": "physics"}}],
                "contradictions": ["x", "Maths date: current state says 9th, but now 12th"], "open_questions": ["a", "b", "c", "d"], "summary": "s"})
    assert c["events"][0]["about"] == [{"kind": "person", "name": USER}] and [x["field"] for x in c["states"]] == ["phone"] and c["states"][0]["name"] == USER
    assert c["relationships"] == [] and len(c["open_questions"]) == MAX_QUESTIONS and len(c["contradictions"]) == 1
    print("consolidate ok")
