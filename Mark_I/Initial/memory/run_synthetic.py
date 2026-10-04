"""Mark I memory: runs the synthetic year through the pipeline with an ORACLE model (the answer key's own extraction) and reports what the code
did with it. It tests everything except the model: ingestion, validation, the write gate, entity resolution, states, relationships, backups.
Usage: python run_synthetic.py [folder with days_*.jsonl and extraction.jsonl]   (default testdata/synthetic_year)
Invented people only; nothing here touches a real memory folder (a temp one is used).
"""
import glob
import json
import logging
import re
import sys
import tempfile
from pathlib import Path

import consolidate
from manager import Memory

HERE = Path(__file__).parent


def load(folder):
    days = {}
    for f in sorted(glob.glob(str(Path(folder) / "days_*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            if line.strip():
                o = json.loads(line)
                days[o["day"]] = o["records"]
    ex = {}
    for line in open(Path(folder) / "extraction.jsonl", encoding="utf-8"):
        if line.strip():
            o = json.loads(line)
            ex[o["day"]] = o["output"]
    return days, ex


def oracle(ex):
    """llm(prompt) -> the key's answer for the day named in the prompt (first part only; later parts of a long day get an empty answer)."""
    def llm(prompt):
        day = re.search(r"MATERIAL \((\d{4}-\d\d-\d\d)\)", prompt).group(1)
        out = ex.get(day) or {"events": [], "summary": "Quiet day."}
        if re.search(r"\(PART (?!1 )\d+ of", prompt):
            return json.dumps({"events": [], "summary": "(continued)"})
        return json.dumps(out)
    return llm


def score(m, truth):
    """Checks the code's result against the answer key where the code alone decides. Returns {check: (passed, total, first failures)}.
    Checks that need a real model (patterns, contradictions ruled, decisions, traits) are for the model run, not here."""
    from graph import Graph
    g, s, res = Graph(m.store), m.store, {}
    ev = [r["content"].lower() for r in s.db.execute("SELECT content FROM events")]
    low = lambda q: " ".join(q.lower().split())
    has = lambda q: any(low(q) in " ".join(c.split()) for c in ev)
    eid = lambda kind, name: next((e["id"] for e in s.entities(kind) if e["name"].lower() == name.lower()), None)

    def put(name, fails, total):
        res[name] = (total - len(fails), total, fails[:3])
    f = []
    for grp in truth["entity_groups"]:
        base = eid(grp["kind"], grp["canonical"])
        if base is None:
            f.append(f"{grp['canonical']}: no entity")
            continue
        for al in grp.get("aliases", []) + grp.get("spelling_variants", []):
            if g.canonical(g.resolve(grp["kind"], al)) != base:
                f.append(f"{al!r} does not resolve to {grp['canonical']}")
    put("aliases_resolve_to_one_entity", f, len(truth["entity_groups"]))
    f = []
    for grp in truth["entity_groups"]:   # a misheard name that was corrected: its early events now belong to the right person
        for bad in grp.get("misheard_as", []):
            base = eid(grp["kind"], grp["canonical"])
            if base is None or not any(f"heard as {bad.lower()}" in e["content"].lower() for e in g.events_about(base, 500)):
                f.append(f"{bad!r} was not merged into {grp['canonical']}")
    put("misheard_name_merged_after_correction", f, sum(len(g_.get("misheard_as", [])) for g_ in truth["entity_groups"]))
    f = []
    for p in truth["must_stay_separate"]:
        a, b = g.resolve("person", p["a"]), g.resolve("person", p["b"])   # resolve like any later mention would (creates the entity if the key never extracted it)
        if g.canonical(a) == g.canonical(b):
            f.append(f"{p['a']} / {p['b']} merged")
    put("different_people_stay_separate", f, len(truth["must_stay_separate"]))
    f = []
    for tl in truth["fact_timelines"]:
        kind, name = tl["entity"].split("/", 1)
        e, last = eid(kind, name), tl["values"][-1]["value"]
        if e is None:
            f.append(f"{name}: no entity")
        elif "(relationship)" in tl["field"]:
            rel = tl["field"].split()[0]
            row = s.db.execute("SELECT o.name FROM relationships r JOIN entities o ON o.id=r.object_id WHERE r.subject_id=? AND r.relation=? AND r.valid_to IS NULL", (e, rel)).fetchone()
            if not row or low(last) not in low(row[0]):
                f.append(f"{name}.{rel}: now {row[0] if row else None!r}, key says {last!r}")
        else:
            row = s.state(e, tl["field"])
            if not row or low(last) not in low(row["value"]):  # the extraction may add detail to the value ("Redmi Note (8 GB), gift")
                f.append(f"{name}.{tl['field']}: now {row['value'] if row else None!r}, key says {last!r}")
    put("fact_timelines_end_on_latest_value", f, len(truth["fact_timelines"]))
    for key, name in (("gate_skip", "chatter_stays_out_of_events"), ("injections", "injected_text_not_stored_as_fact"), ("sarcasm", "sarcasm_not_stored_literally"),
                      ("hypothetical_or_quoted", "hypotheticals_not_stored_as_facts"), ("over_psychologise", "trait_talk_not_stored_as_facts")):
        put(name, [x["quote"] for x in truth[key] if has(x["quote"])], len(truth[key]))
    f = [d["quotes"] for d in truth["near_duplicates"] if sum(has(q) for q in d["quotes"]) > 1]
    put("near_duplicates_kept_once", f, len(truth["near_duplicates"]))
    return res


def run(folder):
    logging.getLogger("memory").setLevel(logging.ERROR)
    days, ex = load(folder)
    with tempfile.TemporaryDirectory() as d, Memory(d) as m:
        for day, recs in days.items():
            for r in recs:
                m.archive.write(r["stream"], r["data"], session=r.get("session"), ts=r["ts"])
        done = consolidate.run_pending(m, oracle(ex), "oracle")
        s = m.store
        n = lambda sql: s.db.execute(sql).fetchone()[0]
        failed = [(r["day"], r["detail"]) for r in s.db.execute("SELECT day, detail FROM consolidation_runs WHERE status!='done'")]
        out = {"days_in_archive": len(days), "days_consolidated": len(done), "failed_days": failed[:10], "archive_verify": m.archive.verify(),
               "events": n("SELECT COUNT(*) FROM events"), "entities": n("SELECT COUNT(*) FROM entities"), "aliases": n("SELECT COUNT(*) FROM entity_aliases"),
               "states": n("SELECT COUNT(*) FROM entity_states"), "open_states": n("SELECT COUNT(*) FROM entity_states WHERE valid_to IS NULL"),
               "relationships": n("SELECT COUNT(*) FROM relationships"), "review_items": n("SELECT COUNT(*) FROM review_queue"),
               "events_in_key": sum(len(o.get("events", [])) for o in ex.values()), "states_in_key": sum(len(o.get("states", [])) for o in ex.values()),
               "aliases_in_key": sum(len(o.get("aliases", [])) for o in ex.values())}
        out["events_kept_by_gate"] = f"{out['events']}/{out['events_in_key']}"
        tf = Path(folder) / "truth.json"
        if tf.exists():
            out["score"] = {k: f"{p}/{n}" + (f"  FAIL: {fails}" if fails else "") for k, (p, n, fails) in score(m, json.loads(tf.read_text(encoding="utf-8"))).items()}
        return out, m


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else HERE / "testdata" / "synthetic_year"
    res, _ = run(folder)
    print(json.dumps(res, indent=1, ensure_ascii=False))
