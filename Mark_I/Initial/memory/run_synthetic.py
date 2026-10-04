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
        return out, m


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else HERE / "testdata" / "synthetic_year"
    res, _ = run(folder)
    print(json.dumps(res, indent=1, ensure_ascii=False))
