"""Mark I memory: the curation and quality tools for the memory agents (not FRED's own recall tools).

MemTools is a mixin of the tool-loop engine (weekly.Week): every agent gets the ten read-only quality tools; the four proposal tools
work only where `can_propose` is True (the critic and the maintenance agent). Proposals never change memory directly:
maintenance.propose decides by tier whether code may apply it (undoable, high confidence) or Vatsal must confirm.
Reads: self.q (SQL), self.s (store), self.g (graph), self.mem (archive), self.index; all come from weekly.Week.
"""
import json
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from decisionlog import show

READ_LINES = """CURATION AND QUALITY TOOLS (read-only):
source_check {"inference_id": int} or {"event_id": int}   the source and the raw archive lines behind a claim; flags missing sources
find_contradictions {"entity": str}   flip-flopping facts and several open links of one kind, minus what is already queued for review
confidence_audit {"from": date, "to": date}   claims whose confidence does not match their evidence
coverage_gaps {"from": date, "to": date}   days with no events, or events but no daily log
timeline_gaps {"entity": str}   holes and overlaps in an entity's history, long silences
dedupe_events {"day": "YYYY-MM-DD"}   near-duplicate events inside one day
orphan_report {}   entities, chapters and inferences hanging on nothing
backup_status {}   when the last backup ran
queue_summary {}   size and age of the review queue, the proposals and the open inferences
decision_replay {"run": str, "agent": str}   replay one earlier agent run from the decision log (run = its week/month/day label)"""

PROPOSE_LINES = """PROPOSAL TOOLS (they only queue a proposal; Vatsal confirms, except an undoable merge you are very sure of):
propose_merge {"a": str, "b": str, "reason": str, "confidence": 0-1}   a is merged INTO b; confidence 0.9+ only if the evidence leaves no doubt
propose_split {"entity": str, "new_name": str, "event_ids": [int], "reason": str}   one entity is really two: move those events to a new one
retire_alias {"alias": str, "entity": str, "reason": str}   a nickname that points at the wrong entity
flag_stale {"entity": str, "field": str, "why": str}   a current fact that looks out of date"""

GAP_DAYS = 30


def tool_text(agent):
    return "\n" + READ_LINES + ("\n" + PROPOSE_LINES if getattr(agent, "can_propose", False) else "")


def _days(a, b, cap=92):
    d, out = datetime.fromisoformat(a), []
    while d.strftime("%Y-%m-%d") <= b and len(out) < cap:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def _words(s):
    return {w for w in "".join(c if c.isalnum() else " " for c in s.lower()).split() if len(w) >= 4}


class MemTools:
    can_propose = False
    backup_dir = None

    def _one(self, name):
        ids, err = self._need_entity(name)
        if err:
            return None, err
        if len(ids) > 1:
            return None, {"error": f"{name!r} matches {len(ids)} entities of different kinds; use get_entity to pick one"}
        return ids[0], None

    # ---- read-only quality tools ----
    def t_source_check(self, inference_id=None, event_id=None):
        if inference_id is not None:
            ids = [r["event_id"] for r in self.q("SELECT event_id FROM inference_evidence WHERE inference_id=? AND role='support'", (int(inference_id),))]
            if not ids:
                return {"inference": inference_id, "problem": "no supporting evidence at all"}
        elif event_id is not None:
            ids = [int(event_id)]
        else:
            return {"error": "give inference_id or event_id"}
        out = []
        for eid in ids[:12]:
            e = self.q("SELECT e.id, e.ts, e.content, e.source_id, s.kind sk, s.ref FROM events e LEFT JOIN sources s ON s.id=e.source_id WHERE e.id=?", (eid,))
            if not e:
                out.append({"event": eid, "problem": "event does not exist"})
                continue
            e = e[0]
            item = {"event": eid, "date": e["ts"][:10], "content": e["content"][:200], "source": e["sk"], "ref": e["ref"], "problems": [], "archive_lines": []}
            if e["source_id"] is None:
                item["problems"].append("event has no source")
            elif e["sk"] is None:
                item["problems"].append("source row is missing")
            lines = self._archive_lines(e["ts"][:10], e["content"], e["ref"] if e["sk"] == "archive" else None)
            item["archive_lines"] = lines
            if not lines:
                item["problems"].append("no raw archive line found for that day" if e["sk"] != "archive" else "archive session not found on that day")
            out.append(item)
        return out

    def _archive_lines(self, day, content, session=None):
        folder = Path(self.mem.archive.root) / day[:4] / day[5:7] / day[8:10]
        want, found = _words(content), []
        for f in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
            for line in f.read_text(encoding="utf-8", errors="ignore").split("\n"):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if session and rec.get("session") != session:
                    continue
                score = len(want & _words(line)) if want else 0
                if session or score >= 2:
                    found.append((score, f.stem, line[:220]))
        return [{"stream": s, "line": ln} for _, s, ln in sorted(found, key=lambda x: -x[0])[:3]]

    def t_find_contradictions(self, entity):
        i, err = self._one(entity)
        if err:
            return err
        name = self.q("SELECT name FROM entities WHERE id=?", (i,))[0]["name"]
        lo = (datetime.fromisoformat(self.end) - timedelta(days=90)).strftime("%Y-%m-%d")
        queued = " ".join(r["text"].lower() for r in self.q("SELECT text FROM review_queue WHERE status='open'"))
        out = []
        for r in self.q("SELECT field, COUNT(*) n FROM entity_states WHERE entity_id=? AND substr(valid_from,1,10)>=? GROUP BY field HAVING n>=3", (i, lo)):
            if name.lower() not in queued or r["field"].lower() not in queued:
                out.append({"kind": "flip_flop", "field": r["field"], "changes_in_90_days": r["n"]})
        for r in self.q("SELECT relation, COUNT(DISTINCT object_id) n FROM relationships WHERE subject_id=? AND valid_to IS NULL GROUP BY relation HAVING n>1", (i,)):
            if name.lower() not in queued or r["relation"].lower() not in queued:
                out.append({"kind": "several_open_links", "relation": r["relation"], "open": r["n"],
                            "note": "may be legitimate (several friends); judge from the relation"})
        return out or {"found": 0}

    def t_confidence_audit(self, **a):
        lo, hi = a.get("from", self.start), a.get("to", self.end)
        out = []
        for r in self.q("SELECT i.id, i.statement, i.status, i.confidence, "
                        "(SELECT COUNT(*) FROM inference_evidence WHERE inference_id=i.id AND role='support') sup, "
                        "(SELECT COUNT(*) FROM inference_evidence WHERE inference_id=i.id AND role='counter') con "
                        "FROM inferences i WHERE i.status IN ('proposed','supported','weakened') AND substr(i.created_at,1,10) BETWEEN ? AND ?", (lo, hi)):
            why = []
            if r["status"] == "supported" and r["confidence"] < 0.4:
                why.append("supported but confidence is low")
            if r["confidence"] > 0.7 and r["sup"] < 2:
                why.append("high confidence on fewer than two supporting events")
            if r["con"] >= max(1, r["sup"]) and r["status"] != "weakened":
                why.append("as much counter-evidence as support, not marked weakened")
            if why:
                out.append({"id": r["id"], "statement": r["statement"][:140], "confidence": r["confidence"], "support": r["sup"], "counter": r["con"], "why": why})
        for r in self.q("SELECT e.name, s.field, s.confidence, s.valid_from FROM entity_states s JOIN entities e ON e.id=s.entity_id "
                        "WHERE s.valid_to IS NULL AND s.confidence < 0.5 AND substr(s.valid_from,1,10) < ? LIMIT 15",
                        ((datetime.fromisoformat(self.end) - timedelta(days=30)).strftime("%Y-%m-%d"),)):
            out.append({"fact": f"{r['name']}.{r['field']}", "confidence": r["confidence"], "since": r["valid_from"][:10], "why": ["weak fact still current after 30 days"]})
        return out or {"found": 0}

    def t_coverage_gaps(self, **a):
        lo, hi = a.get("from", self.start), a.get("to", self.end)
        ev = {r["d"] for r in self.q("SELECT DISTINCT substr(ts,1,10) d FROM events WHERE substr(ts,1,10) BETWEEN ? AND ?", (lo, hi))}
        lg = {r["day"] for r in self.q("SELECT day FROM daily_logs WHERE day BETWEEN ? AND ?", (lo, hi))}
        days = _days(lo, hi)
        return {"days_checked": len(days), "no_events": [d for d in days if d not in ev][:40], "events_without_daily_log": sorted(ev - lg)[:40]}

    def t_timeline_gaps(self, entity):
        i, err = self._one(entity)
        if err:
            return err
        out = []
        fields = [r["field"] for r in self.q("SELECT DISTINCT field FROM entity_states WHERE entity_id=?", (i,))]
        for f in fields:
            rows = self.q("SELECT value, valid_from, valid_to FROM entity_states WHERE entity_id=? AND field=? ORDER BY valid_from, id", (i, f))
            for p, n in zip(rows, rows[1:]):
                if p["valid_to"] is None:
                    out.append({"field": f, "problem": "overlap: an earlier value was never closed", "values": [p["value"], n["value"]]})
                elif n["valid_from"][:10] > (datetime.fromisoformat(p["valid_to"][:10]) + timedelta(days=1)).strftime("%Y-%m-%d"):
                    out.append({"field": f, "problem": "gap with no known value", "from": p["valid_to"][:10], "to": n["valid_from"][:10]})
        days = [r["d"] for r in self.q("SELECT DISTINCT substr(e.ts,1,10) d FROM events e WHERE e.entity_id=? OR e.id IN (SELECT event_id FROM event_entities WHERE entity_id=?) ORDER BY d", (i, i))]
        for p, n in zip(days, days[1:]):
            if (datetime.fromisoformat(n) - datetime.fromisoformat(p)).days > GAP_DAYS:
                out.append({"problem": f"silence of more than {GAP_DAYS} days between events", "from": p, "to": n})
        return out[:30] or {"found": 0}

    def t_dedupe_events(self, day):
        evs = self.events(day, day)[:200]
        out = []
        for k, a in enumerate(evs):
            for b in evs[k + 1:]:
                r = SequenceMatcher(None, " ".join(a["content"].lower().split()), " ".join(b["content"].lower().split())).ratio()
                if r >= 0.75:
                    out.append({"ids": [a["id"], b["id"]], "similarity": round(r, 2), "a": a["content"][:100], "b": b["content"][:100]})
        return out[:25] or {"found": 0}

    def t_orphan_report(self):
        from hygiene import ensure
        ensure(self.s)
        with self.s.lock:
            self.s.db.executescript((Path(__file__).with_name("schema_period.sql")).read_text(encoding="utf-8"))  # chapters
        ents = self.q("SELECT e.id, e.kind, e.name FROM entities e WHERE e.id NOT IN (SELECT entity_id FROM entity_merges) "
                      "AND e.id NOT IN (SELECT entity_id FROM event_entities) AND e.id NOT IN (SELECT entity_id FROM events WHERE entity_id IS NOT NULL) "
                      "AND e.id NOT IN (SELECT subject_id FROM relationships) AND e.id NOT IN (SELECT object_id FROM relationships) "
                      "AND e.id NOT IN (SELECT entity_id FROM entity_states) LIMIT 30")
        ch = self.q("SELECT id, title FROM chapters WHERE id NOT IN (SELECT chapter_id FROM chapter_log) LIMIT 20")
        inf = self.q("SELECT i.id, i.statement FROM inferences i WHERE i.status IN ('proposed','supported','weakened') AND NOT EXISTS ("
                     "SELECT 1 FROM inference_evidence x JOIN events e ON e.id=x.event_id WHERE x.inference_id=i.id AND x.role='support' "
                     "AND e.id NOT IN (SELECT event_id FROM cold_events)) LIMIT 20")
        return {"entities_with_nothing": [f"{r['kind']}/{r['name']}" for r in ents], "chapters_without_history": [f"#{r['id']} {r['title']}" for r in ch],
                "inferences_without_live_support": [f"#{r['id']} {r['statement'][:100]}" for r in inf]}

    def t_backup_status(self):
        from backup import DEFAULT_BACKUP_DIR, _STAMP, last_backup
        d = Path(self.backup_dir or DEFAULT_BACKUP_DIR)
        last = last_backup(d)
        n = sum(1 for p in (d.iterdir() if d.is_dir() else []) if _STAMP.fullmatch(p.name))
        if not last:
            return {"backups": n, "last": None, "warning": "no full backup found"}
        age = (datetime.fromisoformat(self.end + "T23:59:59+05:30") - last).days
        return {"backups": n, "last": last.isoformat(timespec="minutes"), "age_days": age, **({"warning": "older than 14 days"} if age > 14 else {})}

    def t_queue_summary(self):
        from maintenance import ensure
        ensure(self.s)
        one = lambda sql: self.q(sql)[0]
        rq = one("SELECT COUNT(*) n, MIN(day) oldest FROM review_queue WHERE status='open'")
        pr = one("SELECT COUNT(*) n, MIN(substr(created_at,1,10)) oldest FROM proposals WHERE status='proposed'")
        ap = one("SELECT COUNT(*) n FROM proposals WHERE status='applied'")
        inf = one("SELECT COUNT(*) n FROM inferences WHERE status='proposed'")
        return {"review_items_open": rq["n"], "oldest_review_item": rq["oldest"], "proposals_waiting_for_Vatsal": pr["n"], "oldest_proposal": pr["oldest"],
                "auto_applied_not_yet_reported": ap["n"], "inferences_still_proposed": inf["n"]}

    def t_decision_replay(self, run, agent=None, limit=40):
        rows = show(self.s, agent, run, None, min(int(limit), 80))
        return [{"id": r["id"], "agent": r["agent"], "kind": r["kind"], "data": json.dumps(r["data"], ensure_ascii=False, default=str)[:350]} for r in rows] or {"found": 0}

    # ---- proposal tools: queue only, tier decides the rest ----
    def _prop(self, kind, payload, reason, confidence):
        if not self.can_propose:
            return {"error": "this agent may not propose changes"}
        from maintenance import AUTO_MAX, propose
        try:
            res = propose(self.mem, kind, payload, reason, confidence, agent=type(self).__name__, run=self.end,
                          allow_auto=getattr(self, "_auto", 0) < AUTO_MAX)
        except ValueError as ex:
            return {"error": str(ex)}
        if res.get("status") == "applied":
            self._auto = getattr(self, "_auto", 0) + 1
        return res

    def t_propose_merge(self, a, b, reason="", confidence=0.5):
        (x, ex), (y, ey) = self._one(a), self._one(b)
        if ex or ey:
            return ex or ey
        return self._prop("merge", {"from": x, "into": y}, reason, confidence)

    def t_propose_split(self, entity, new_name, event_ids, reason=""):
        i, err = self._one(entity)
        return err or self._prop("split", {"entity": i, "new_name": str(new_name).strip(), "event_ids": [int(e) for e in event_ids or []]}, reason, 0.5)

    def t_retire_alias(self, alias, entity, reason=""):
        i, err = self._one(entity)
        return err or self._prop("retire_alias", {"entity": i, "alias": " ".join(str(alias).lower().split())}, reason, 0.5)

    def t_flag_stale(self, entity, field, why=""):
        i, err = self._one(entity)
        return err or self._prop("stale_fact", {"entity": i, "field": str(field)}, why, 0.5)
