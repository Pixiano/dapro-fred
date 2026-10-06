"""Mark I memory: the ask queue. One place for everything FRED needs Vatsal to settle (same-person questions, contradictions, merge and
other maintenance proposals), asked as a short either/or question when a conversation winds down.

  ask(mem, topic, turns)   -> at most ONE item {"id", "kind", "text", "options": [{"key", "label"}]} or None. Call it once at the end of a conversation.
                               Longer conversations trigger more often (turns / FULL_TURNS), at most MAX_PER_DAY a day, each item at most MAX_ASKS times,
                               a snoozed item waits; the item closest to what was just talked about (names in `topic`) goes first.
  ask(mem, topic, turns, decider=None) / settle / report / undo: see their docstrings.
  answer(mem, id, reply, recorder=None) -> reply is an option key, its letter (a, b, c...), or a plain word (yes, no, same, different, first, newer, both, later).
                               Returns {"ok", "choice"} or {"error": "unclear", "options"} so the popup/HUD can ask again.
The question names who or what each side is (what was last said about it and its current facts), so the answer is a pick, not a riddle.
Delivery (FRED speaking it, the pill popup with a text box, the HUD) and the "conversation is ending" signal belong to the integration.
Every answer is also written to the archive (stream "askqueue"), so the nightly editor sees it like anything said that day.
ponytail: names are read back out of the question text (the queue stores no ids); a structured payload is the upgrade if the wording changes.
"""
import json
import random
import re
from datetime import datetime, timedelta, timezone

import maintenance
from decisionlog import note
from graph import Graph, norm

MAX_PER_DAY, MAX_ASKS, FULL_TURNS, SNOOZE_DAYS = 5, 2, 10, 3
_SQL = """CREATE TABLE IF NOT EXISTS ask_state (src TEXT NOT NULL, ref INTEGER NOT NULL, asks INTEGER NOT NULL DEFAULT 0, last_asked TEXT, snooze_until TEXT,
  answer TEXT, answered_at TEXT, auto TEXT, meta TEXT, undone INTEGER NOT NULL DEFAULT 0, reported INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(src, ref))"""
PAIR = re.compile(r"[Ii]s \w+ '(.+?)' the same as '(.+?)'\?")
CONTRA = re.compile(r"^(?:\w+/)?(?P<who>.+?) (?P<field>[\w ]+?): current state says (?P<old>.+?), but now (?P<new>.+)$", re.S)
WORDS = {"yes": ("yes", "yeah", "yep", "ok", "sure", "same", "same person", "same one"), "no": ("no", "nope", "different", "two", "different people", "not the same"),
         "old": ("first", "old", "stored", "the first", "before"), "new": ("second", "new", "newer", "now", "latest", "the newer"),
         "both": ("both", "changed", "it changed", "both true"), "later": ("later", "not now", "skip", "ask later", "later please")}


def _day(today):
    return (today or datetime.now(timezone.utc).date()).isoformat()


def _eid(mem, name):
    g, n, s = Graph(mem.store), norm(name), mem.store
    with s.lock:
        r = s.db.execute("SELECT entity_id FROM entity_aliases WHERE alias=?", (n,)).fetchone()
    if r:
        return g.canonical(r[0])
    return next((g.canonical(e["id"]) for e in s.entities() if norm(e["name"]) == n), None)


def _who(mem, eid):
    """(name, short description): what was last said about it, and up to two current facts."""
    s = mem.store
    with s.lock:
        name = s.db.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()[0]
        ev = s.db.execute("SELECT e.content, e.ts FROM events e JOIN event_entities t ON t.event_id=e.id WHERE t.entity_id=? ORDER BY e.ts DESC LIMIT 1", (eid,)).fetchone()
    bits = ([f"last mentioned {ev['ts'][:10]}: \"{ev['content'][:70]}\""] if ev else []) + [f"{k.replace('_', ' ')} {v}" for k, v in list(s.snapshot(eid).items())[:2]]
    return name, "; ".join(bits) or "nothing else known"


def _opts(*pairs):
    return [{"key": k, "label": lab} for k, lab in pairs]


def _pair_item(mem, a, b, src, ref):
    (na, da), (nb, db) = _who(mem, a), _who(mem, b)
    return {"src": src, "ref": ref, "kind": "pair", "ents": (a, b), "names": (na, nb),
            "text": f"Quick one, sir: {na} ({da}) and {nb} ({db}). Same one, or two different ones?",
            "options": _opts(("yes", "Same one"), ("no", "Two different ones"), ("later", "Ask me later"))}


def _items(mem):
    """Everything waiting, with its question and options. Review questions it cannot turn into a pick stay for the digest."""
    s, out = mem.store, []
    maintenance.ensure(s)
    Graph(s)   # review_queue
    with s.lock:
        props = [dict(r) for r in s.db.execute("SELECT * FROM proposals WHERE status='proposed' ORDER BY id")]
        rq = [dict(r) for r in s.db.execute("SELECT * FROM review_queue WHERE status IN ('open','explained') ORDER BY id")]
    for p in props:
        d = json.loads(p["payload"])
        if p["kind"] == "merge" and g_ok(mem, d["from"], d["into"]):
            out.append(_pair_item(mem, d["from"], d["into"], "proposal", p["id"]))
        else:
            out.append({"src": "proposal", "ref": p["id"], "kind": "yesno", "ents": (), "names": (), "text": "Quick one, sir: " + maintenance.describe(mem, p),
                        "options": _opts(("yes", "Yes"), ("no", "No"), ("later", "Ask me later"))})
    for r in rq:
        m = PAIR.search(r["text"]) if r["kind"] == "question" else None
        if m and (a := _eid(mem, m.group(1))) and (b := _eid(mem, m.group(2))) and a != b:
            out.append(_pair_item(mem, a, b, "review", r["id"]))
        elif r["kind"] == "contradiction":
            c = CONTRA.match(r["text"])
            with s.lock:
                ruling = s.db.execute("SELECT ruling, note FROM review_rulings WHERE item_id=? ", (r["id"],)).fetchone() if _has(s, "review_rulings") else None
            if c:
                text = f"Quick one, sir: about {c['who']}, {c['field']}. I have {c['old']}, but lately it sounded like {c['new'][:120]}. Which is right?"
                opts = _opts(("old", f"The first: {c['old'][:60]}"), ("new", f"The newer: {c['new'][:60]}"), ("both", "Both, it changed over time"), ("later", "Ask me later"))
            else:
                text = f"Quick one, sir: {r['text'][:240]} Which is right?"
                opts = _opts(("old", "The stored version"), ("new", "The newer one"), ("both", "Both, it changed over time"), ("later", "Ask me later"))
            if ruling and ruling["note"]:
                text += f" (I read it as: {ruling['note'][:120]})"
            out.append({"src": "review", "ref": r["id"], "kind": "contradiction", "ents": (), "names": (c["who"],) if c else (), "text": text, "options": opts,
                        "prio": 1 if ruling and ruling["ruling"] == "real" or r["status"] == "explained" else 0})
    return out


def g_ok(mem, a, b):
    g = Graph(mem.store)
    return g.canonical(a) != g.canonical(b)


def _has(s, table):
    with s.lock:
        return bool(s.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())


def ask(mem, topic="", turns=0, today=None, rng=random.random, decider=None):
    s, day = mem.store, _day(today)
    sug = settle(mem, decider, today)["suggest"] if decider else {}   # settle what the precedents allow first; the rest may come with a suggestion
    with s.lock:
        s.db.execute(_SQL)
        if s.db.execute("SELECT COUNT(*) FROM ask_state WHERE last_asked=?", (day,)).fetchone()[0] >= MAX_PER_DAY:
            return None
        st = {(r["src"], r["ref"]): dict(r) for r in s.db.execute("SELECT * FROM ask_state")}
    if turns < 2 or rng() >= min(1.0, turns / FULL_TURNS):   # short conversations rarely trigger
        return None
    low, ready = topic.lower(), []
    for it in _items(mem):
        x = st.get((it["src"], it["ref"]), {})
        if x.get("asks", 0) >= MAX_ASKS or x.get("last_asked") == day or (x.get("snooze_until") or "") > day:
            continue
        ready.append((2 * sum(n.lower() in low for n in it["names"]) + it.get("prio", 0), -it["ref"], it))
    if not ready:
        return None
    it = max(ready, key=lambda t: t[:2])[2]
    with s.lock:
        s.db.execute("INSERT INTO ask_state(src,ref,asks,last_asked) VALUES(?,?,1,?) ON CONFLICT(src,ref) DO UPDATE SET asks=asks+1, last_asked=?", (it["src"], it["ref"], day, day))
        s.db.commit()
    iid, out = f"{it['src']}:{it['ref']}", {"kind": it["kind"], "text": it["text"], "options": it["options"]}
    if iid in sug:   # level 1: FRED proposes, Vatsal confirms or corrects
        lab = next(o["label"] for o in it["options"] if o["key"] == sug[iid]["choice"])
        out["text"] += f" I'd say: {lab}, like your {len(sug[iid]['precedents'])} earlier answers to this kind of question."
        out["suggestion"] = {"choice": sug[iid]["choice"], "precedents": sug[iid]["precedents"]}
    return {"id": iid, **out}


def _choice(reply, options):
    r = (reply or "").strip().lower().rstrip(".!")
    keys = [o["key"] for o in options]
    if r in keys:
        return r
    if len(r) == 1 and "a" <= r < chr(ord("a") + len(keys)):
        return keys[ord(r) - ord("a")]
    for k, words in WORDS.items():
        if k in keys and r in words:
            return k
    return None


def answer(mem, item_id, reply, today=None, recorder=None):
    s, day = mem.store, _day(today)
    src, ref = item_id.split(":")
    it = next((i for i in _items(mem) if i["src"] == src and i["ref"] == int(ref)), None)
    if not it:
        return {"error": "no such waiting question"}
    ch = _choice(reply, it["options"])
    if not ch:
        return {"error": "unclear", "options": it["options"]}
    with s.lock:
        s.db.execute(_SQL)
    if ch == "later":
        with s.lock:
            s.db.execute("UPDATE ask_state SET snooze_until=? WHERE src=? AND ref=?", ((datetime.fromisoformat(day) + timedelta(days=SNOOZE_DAYS)).date().isoformat(), src, int(ref)))
            s.db.commit()
        return {"ok": True, "choice": "later"}
    _close(mem, it, ch, reply, day, item_id, recorder=recorder)
    return {"ok": True, "choice": ch}


def _close(mem, it, ch, said, day, item_id, recorder=None, auto=None):
    """Applies an answer: the merge/reject, the review item closed, the archive record, the precedent. `auto` = FRED settled it from precedents (then no
    precedent is recorded: FRED's own decisions never become evidence for itself)."""
    s, src, ref, meta = mem.store, it["src"], it["ref"], {}
    if it["kind"] in ("pair", "yesno"):
        yes = ch == "yes"
        if src == "proposal":
            meta["proposal"] = ref
            maintenance.answer(mem, ref, yes)
        else:   # a review question: make it a merge proposal, so confirming merges (undoable) and rejecting is never proposed again
            a, b = it["ents"]
            with s.lock:
                n = lambda e: s.db.execute("SELECT COUNT(*) FROM event_entities WHERE entity_id=?", (e,)).fetchone()[0]
            frm, into = (a, b) if n(a) <= n(b) else (b, a)
            p = maintenance.propose(mem, "merge", {"from": frm, "into": into}, reason="you answered a question" if not auto else "settled from your earlier answers",
                                    confidence=1.0, agent="askqueue", allow_auto=False)
            if "id" in p:
                meta["proposal"] = p["id"]
                maintenance.answer(mem, p["id"], yes)
    if src == "review":   # the answer reaches the facts through the archive record and the nightly editor
        with s.lock:
            s.db.execute("UPDATE review_queue SET status='closed' WHERE id=?", (ref,))
            s.db.commit()
    label = next(o["label"] for o in it["options"] if o["key"] == ch)
    if auto:
        auto = {**auto, "domain": it["kind"], "question": it["text"], "label": label}
    with s.lock:
        s.db.execute("INSERT INTO ask_state(src,ref,answer,answered_at,auto,meta) VALUES(?,?,?,?,?,?) ON CONFLICT(src,ref) DO UPDATE SET answer=?, answered_at=?, auto=?, meta=?",
                     (src, ref, ch, day, json.dumps(auto) if auto else None, json.dumps(meta), ch, day, json.dumps(auto) if auto else None, json.dumps(meta)))
        s.db.commit()
    mem.archive.write("askqueue", {"question": it["text"], "answer": label, "said": said if not auto else "(FRED settled this from your earlier answers)"})
    note(s, "askqueue", item_id, "action", item=item_id, choice=ch, auto=bool(auto))
    if recorder and not auto:   # precedents.recorder(mem, llm, embed): keeps what was decided; a failure there never loses the answer
        try:
            recorder({**it, "id": item_id}, ch, said)
        except Exception as ex:
            note(s, "askqueue", item_id, "model_failure", error=repr(ex))


def settle(mem, decider, today=None):
    """FRED settles what its precedents allow (mode "decide": level 2, same choice every time, no difference found). decider(item) -> {"choice","mode","precedents"}
    or None, e.g. lambda it: precedents.decide(mem, it, llm, embed). Returns {"done": [...settled], "suggest": {item id: decision}} (level 1: only suggested).
    undo(id) reverses a settled one, report() tells Vatsal."""
    day, out = _day(today), {"done": [], "suggest": {}}
    with mem.store.lock:
        mem.store.db.execute(_SQL)
    for it in _items(mem):
        iid = f"{it['src']}:{it['ref']}"
        d = decider({**it, "id": iid})
        if d and d["mode"] == "decide":
            _close(mem, it, d["choice"], None, day, iid, auto=d)
            out["done"].append({"id": iid, "choice": d["choice"], "precedents": d["precedents"]})
        elif d:
            out["suggest"][iid] = d
    return out


def report(mem):
    """What FRED settled on its own since the last report, with ids to undo; None if nothing. Marks them reported."""
    s = mem.store
    with s.lock:
        s.db.execute(_SQL)
        rows = [dict(r) for r in s.db.execute("SELECT src, ref, auto FROM ask_state WHERE auto IS NOT NULL AND undone=0 AND reported=0")]
        s.db.execute("UPDATE ask_state SET reported=1 WHERE auto IS NOT NULL AND reported=0")
        s.db.commit()
    return [{"id": f"{r['src']}:{r['ref']}", "text": f"I settled this from your earlier answers: {json.loads(r['auto'])['question'][:160]} -> {json.loads(r['auto'])['label']} (say 'undo' to reopen it)"}
            for r in rows] or None


def undo(mem, item_id, today=None):
    """Reverses something FRED settled itself: the merge is undone / the question reopens, the level of that domain drops and the precedents it rested on
    stop counting. A question Vatsal answered himself is not undone here (merges have maintenance.undo)."""
    s, (src, ref) = mem.store, (item_id.split(":")[0], int(item_id.split(":")[1]))
    with s.lock:
        s.db.execute(_SQL)
        r = s.db.execute("SELECT * FROM ask_state WHERE src=? AND ref=?", (src, ref)).fetchone()
    if not r or not r["auto"] or r["undone"]:
        return {"error": "FRED did not settle that one, or it is already undone"}
    auto, meta = json.loads(r["auto"]), json.loads(r["meta"] or "{}")
    if "proposal" in meta:
        pid = meta["proposal"]
        if r["answer"] == "yes":
            maintenance.undo(mem, pid)
        with s.lock:
            s.db.execute("UPDATE proposals SET status=? WHERE id=?", ("proposed" if src == "proposal" else "undone", pid))
            s.db.commit()
    if src == "review":
        with s.lock:
            s.db.execute("UPDATE review_queue SET status='open' WHERE id=?", (ref,))
            s.db.commit()
    with s.lock:
        s.db.execute("UPDATE ask_state SET undone=1, answer=NULL, asks=0, last_asked=NULL WHERE src=? AND ref=?", (src, ref))
        s.db.commit()
    import precedents
    precedents.outcome(mem, auto["domain"], False, auto.get("precedents", []))
    note(s, "askqueue", item_id, "action", item=item_id, undone=True)
    return {"ok": True}


if __name__ == "__main__":  # self-check: python askqueue.py
    import logging
    import tempfile
    from manager import Memory
    logging.getLogger("memory").setLevel(logging.ERROR)
    with tempfile.TemporaryDirectory() as d, Memory(d) as m:
        s, g = m.store, None
        nitu, nitya = s.entity("person", "Nitu"), s.entity("person", "Nitya")
        g = Graph(s)
        e1 = s.add_event("note", "Nitu came with me to the tuition class yesterday", ts="2026-03-05T10:00:00+00:00")
        e2 = s.add_event("note", "Nitya called from Pune", ts="2026-03-04T10:00:00+00:00")
        g.tag(e1, nitu), g.tag(e2, nitya), s.set_state(nitya, "lives_in", "Pune")
        s.db.execute("INSERT INTO review_queue(day,kind,text) VALUES('2026-03-05','question',\"Is person 'Nitu' the same as 'Nitya'?\")"); s.db.commit()
        s.db.execute("INSERT INTO review_queue(day,kind,text) VALUES('2026-03-05','contradiction','person/Toffee age: current state says 3, but now Arin got Toffee in class 3, six years ago')"); s.db.commit()
        always, never = (lambda: 0.0), (lambda: 0.99)
        assert ask(m, "Nitu", turns=1, rng=always) is None and ask(m, "Nitu", turns=3, rng=never) is None   # too short / unlucky
        q = ask(m, "we talked about Nitu and the tuition", turns=12, today=datetime(2026, 3, 6).date(), rng=always)
        assert q["kind"] == "pair" and "Pune" in q["text"] and "tuition" in q["text"] and [o["key"] for o in q["options"]] == ["yes", "no", "later"]   # relevant item first, each side described
        assert ask(m, "x", turns=12, today=datetime(2026, 3, 6).date(), rng=always)["kind"] == "contradiction"   # the same item is not asked twice a day
        assert ask(m, "", turns=12, today=datetime(2026, 3, 6).date(), rng=always) is None                       # nothing else waiting today
        assert answer(m, q["id"], "banana")["error"] == "unclear"
        assert answer(m, q["id"], "later", today=datetime(2026, 3, 6).date())["choice"] == "later"
        assert ask(m, "Nitu", turns=12, today=datetime(2026, 3, 7).date(), rng=always)["kind"] == "contradiction"   # snoozed: not before 3 days
        assert answer(m, q["id"], "b")["choice"] == "no"                                                          # a letter works; two different ones
        assert s.db.execute("SELECT status FROM review_queue WHERE id=1").fetchone()[0] == "closed"
        assert maintenance.propose(m, "merge", {"from": nitu, "into": nitya}, allow_auto=False).get("error")      # rejected: never proposed again
        assert answer(m, "review:2", "newer")["choice"] == "new" and s.db.execute("SELECT status FROM review_queue WHERE id=2").fetchone()[0] == "closed"
        assert any("Toffee" in json.dumps(r) for r in m.archive.read("askqueue", datetime.now(timezone.utc).strftime("%Y-%m-%d")))
        # precedents: record answers, earn levels, FRED suggests, then settles, tells, and undo reopens
        import precedents
        def embed(tx, q):
            v = [0.0] * 64
            for w in re.findall(r"[a-z]+", tx.lower()):
                v[sum(map(ord, w)) % 64] += 1
            return v
        llm = lambda p: json.dumps({"differs": False, "difference": ""}) if "earlier cases" in p else json.dumps({"situation": "two similar names", "reason": "none"})
        rec, dec = precedents.recorder(m, llm, embed), (lambda it: precedents.decide(m, it, llm, embed))
        def mkpair(i):
            a, b = s.entity("person", f"Pa{i}x"), s.entity("person", f"Pb{i}y")
            e1, e2 = s.add_event("note", f"Pa{i}x came to class", ts="2026-03-05T10:00:00+00:00"), s.add_event("note", f"Pb{i}y called from Pune", ts="2026-03-04T10:00:00+00:00")
            g.tag(e1, a), g.tag(e2, b)
            s.db.execute("INSERT INTO review_queue(day,kind,text) VALUES('2026-03-05','question',?)", (f"Is person 'Pa{i}x' the same as 'Pb{i}y'?",)); s.db.commit()
            return s.db.execute("SELECT MAX(id) FROM review_queue").fetchone()[0]
        for i in range(precedents.MIN_MATCH + precedents.CONFIRM):                    # six answers "different": 3 build precedents, 3 agree with FRED's shadow -> level 1
            assert answer(m, f"review:{mkpair(i)}", "no", recorder=rec)["choice"] == "no"
        assert precedents.level(m, "pair") == 1
        for k in range(precedents.CONFIRM):                                            # level 1: FRED suggests, Vatsal confirms; three confirmations -> level 2
            n = mkpair(10 + k)
            q = ask(m, "", turns=12, rng=always, decider=dec, today=datetime(2026, 4, 1 + k).date())
            assert q["id"] == f"review:{n}" and q["suggestion"]["choice"] == "no" and "I'd say: Two different ones" in q["text"]   # suggested, not settled
            answer(m, q["id"], "no", recorder=rec)
        assert precedents.level(m, "pair") == 2
        n = mkpair(30)
        assert ask(m, "", turns=12, rng=always, decider=dec, today=datetime(2026, 4, 20).date()) is None      # level 2: settled by FRED, nothing to ask
        assert s.db.execute("SELECT status FROM review_queue WHERE id=?", (n,)).fetchone()[0] == "closed"
        rp = report(m)
        assert len(rp) == 1 and "settled this from your earlier answers" in rp[0]["text"] and report(m) is None
        assert undo(m, f"review:{n}")["ok"] and s.db.execute("SELECT status FROM review_queue WHERE id=?", (n,)).fetchone()[0] == "open"
        assert precedents.level(m, "pair") == 1 and undo(m, f"review:{n}").get("error")                      # an override drops the level; no double undo
    print("ask queue ok")
