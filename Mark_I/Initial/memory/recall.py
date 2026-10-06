"""Mark I memory: judged recall. The model reads the top candidates, drops the useless ones, and if nothing
useful is left it tries again wider (cold events included, new wording) and finally asks the user for a keyword.

Sockets:
  - judged_search(index, query, llm, k, pool): llm(prompt)->str returning JSON, injected like consolidate's.
    Returns {"events": [...], "ask": str|None}. Only events the model kept are marked recalled, so the
    candidates it threw away do not stay warm. Bad model output falls back to the plain top-k, never an error.
  - say(text): optional callback, the talker model's voice. Called with a short status line ("digging deeper")
    before the wider retry, so the user hears progress during the slow part. A failing callback is ignored.
  - One retry round only. ponytail: loop with a budget if one retry proves too few.
"""
import json
import sqlite3

from decisionlog import traced

PROMPT = """Question: {q}
Candidate memories:
{cands}
Return ONLY JSON: {{"relevant": [ids that help answer the question, best first],
 "retry_queries": [up to 3 better search phrasings or keywords, only if nothing is relevant],
 "ask": "one short question asking the user for a better keyword or detail, only if you cannot do better"}}"""


def _judge(index, q, evs, llm):
    cands = "\n".join(f"{e['id']}: [{e['ts'][:10]}] {e['content']}" for e in evs)
    try:
        out = json.loads(traced(index.store, "recall_judge", q[:60], llm)(PROMPT.format(q=q, cands=cands)))
        ok = {e["id"]: e for e in evs}
        return [ok[i] for i in out.get("relevant", []) if i in ok], out.get("retry_queries") or [], out.get("ask")
    except Exception:  # bad output: trust the retrieval order rather than fail the question
        return None, [], None


def judged_search(index, query, llm, k=5, pool=20, say=None, graph=None) -> dict:
    extra, facts = {}, {}
    if graph:  # names in the question: unsure -> ask first; sure -> pull everything tagged with them
        found, maybe = graph.find_in_text(query)
        if maybe:
            w, eid = maybe[0]
            name = graph.s.db.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()[0]
            return {"events": [], "facts": {}, "maybe": maybe, "ask": f"Do you mean {name} when you say '{w}'?"}
        for eid, name in found.items():
            snap = graph.s.snapshot(eid)
            if snap:
                facts[name] = snap
            extra.update({e["id"]: e for e in graph.events_about(eid)})
    evs = list({e["id"]: e for e in index.search(query, pool, touch=False) + list(extra.values())}.values())
    kept, retries, ask = _judge(index, query, evs, llm) if evs else ([], [], None)
    if kept is None:
        kept = evs[:k]
    elif not kept:  # wider, targeted second try: model's wording, cold events included, bigger pool
        try:
            say and say("Nothing obvious yet, digging deeper...")
        except Exception:
            pass
        wide = {}
        for rq in [query] + [r for r in retries if isinstance(r, str)][:3]:
            for e in index.search(rq, pool * 2, include_cold=True, touch=False):
                wide.setdefault(e["id"], e)
        for eid in (found if graph else {}):
            wide.update({e["id"]: e for e in graph.events_about(eid, 40, include_cold=True)})
        kept = (_judge(index, query, list(wide.values()), llm)[0] or []) if wide else []
        ask = None if kept or facts else (ask or "I could not find that. Can you give me a name, date or keyword to search for?")
    kept = kept[:k]
    index.touch([e["id"] for e in kept])
    out = {"events": kept, "ask": ask, "facts": facts}
    out.update(_by_project(index.store, kept, query))
    return out


def _by_project(store, kept, query):
    """Kept events that belong to 2+ projects: answer about each ("groups"), then offer a "follow_up" question. ponytail: "fred" in the query only
    orders FRED's projects first, it never filters (not every 'fred' means a FRED project)."""
    if not kept:
        return {}
    try:
        with store.lock:
            rows = store.db.execute("SELECT t.event_id, e.id, e.name FROM event_entities t JOIN entities e ON e.id=t.entity_id WHERE e.kind='project' AND t.event_id IN (%s)"
                                    % ",".join("?" * len(kept)), [e["id"] for e in kept]).fetchall()
    except sqlite3.OperationalError:   # no tags table yet: no graph in use
        return {}
    by = {}
    for ev, pid, name in rows:
        by.setdefault((pid, name), set()).add(ev)
    if len(by) < 2:
        return {}
    fred = "fred" in query.lower()
    order = sorted(by, key=lambda k: (not (fred and "fred" in k[1].lower()), -len(by[k])))
    groups = [{"entity": n, "events": [e for e in kept if e["id"] in by[(i, n)]]} for i, n in order]
    names = [g["entity"] for g in groups]
    return {"groups": groups, "follow_up": f"That fits {' and '.join(names)}. Did you mean one of them in particular?"}


if __name__ == "__main__":  # self-check: python recall.py
    from semantic import SemanticIndex
    from store import MemoryStore
    words = ["cat", "dog", "visa", "tax"]
    s = MemoryStore(":memory:")
    ix = SemanticIndex(s, lambda t, q: [float(w in t) + 0.01 for w in words], "fake")
    for c in ("my cat and dog", "the dog barked", "visa appointment booked", "paid tax"):
        s.add_event("note", c)
    ix.sync()
    pick = lambda text: (lambda p: json.dumps({"relevant": [int(l.split(":")[0]) for l in p.splitlines()
                                                              if l[:1].isdigit() and text in l]}))
    r = judged_search(ix, "dog", pick("barked"), k=3)                  # junk dropped, only the kept one is recalled
    assert [e["content"] for e in r["events"]] == ["the dog barked"] and r["ask"] is None
    assert [x[0] for x in s.db.execute("SELECT event_id FROM event_recalls")] == [r["events"][0]["id"]]
    s.db.execute("INSERT INTO cold_events SELECT id, 'x' FROM events WHERE content LIKE 'visa%'"); s.db.commit()
    retry = lambda p: json.dumps({"relevant": [int(l.split(":")[0]) for l in p.splitlines() if "visa" in l and l[:1].isdigit()]
                                  } if "visa" in p else {"relevant": [], "retry_queries": ["visa"]})
    assert judged_search(ix, "paperwork", retry)["events"][0]["content"] == "visa appointment booked"  # retry + cold
    r = judged_search(ix, "zzz", lambda p: json.dumps({"relevant": [], "ask": "Which person?"}))
    assert r["events"] == [] and r["ask"] == "Which person?"          # nothing found: ask the user
    assert judged_search(ix, "dog", lambda p: "garbage", k=2)["events"]  # bad model output: plain top-k
    s.db.execute("INSERT OR IGNORE INTO cold_events SELECT id, 'x' FROM events WHERE content LIKE 'visa%'"); s.db.commit()  # recalled above, so re-cool
    said = []
    judged_search(ix, "paperwork", retry, say=said.append)
    assert said == ["Nothing obvious yet, digging deeper..."]
    judged_search(ix, "dog", pick("barked"), say=said.append)           # found first time: stays quiet
    assert len(said) == 1
    judged_search(ix, "zzz", retry, say=lambda s: 1 / 0)                 # broken talker must not break recall
    from graph import Graph
    g = Graph(s)                                                       # tags: "Mee's fav colour" never says Meera
    me = g.resolve("person", "Meera")
    g.tag(s.add_event("note", "her favourite shade is cyan"), me)
    s.set_state(me, "favourite_colour", "cyan")
    keep_cyan = lambda p: json.dumps({"relevant": [int(l.split(":")[0]) for l in p.splitlines() if "cyan" in l and l[:1].isdigit()]})
    q = "Whats Mee's fav colour?"
    r = judged_search(ix, q, keep_cyan, graph=g)
    assert r["events"] == [] and r["maybe"] and "Meera" in r["ask"]    # unsure: asks first, searches nothing
    g.confirm_alias(*r["maybe"][0])
    r = judged_search(ix, q, keep_cyan, graph=g)
    assert r["events"][0]["content"] == "her favourite shade is cyan" and r["facts"] == {"Meera": {"favourite_colour": "cyan"}}
    from graph import Graph
    s2 = MemoryStore(":memory:")
    ix2 = SemanticIndex(s2, lambda t, q: [float("voice" in t) + 0.01, 0.0, 0.0, 0.0], "fake")
    a, b, c = s2.entity("project", "Voice Assistant"), s2.entity("project", "FRED Voice"), s2.entity("project", "Garden")
    g2 = Graph(s2)
    for pid, txt in ((a, "started the voice assistant in March"), (b, "began work on fred voice in September"), (c, "voice of the garden gnome")):
        g2.tag(s2.add_event("note", txt), pid)
    ix2.sync()
    allk = lambda p: json.dumps({"relevant": [int(l.split(":")[0]) for l in p.splitlines() if l[:1].isdigit() and "garden" not in l]})
    r = judged_search(ix2, "when did I start the voice project", allk)                    # two projects fit: answer about both, then offer a question
    assert sorted(x["entity"] for x in r["groups"]) == ["FRED Voice", "Voice Assistant"] and "Did you mean" in r["follow_up"]
    r = judged_search(ix2, "when did I start fred voice", allk)                           # "fred" only orders, it never filters
    assert r["groups"][0]["entity"] == "FRED Voice" and len(r["groups"]) == 2
    print("recall ok")
