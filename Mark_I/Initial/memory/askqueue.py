"""Mark I memory: the ask queue. One place for everything FRED needs Vatsal to settle (same-person questions, contradictions, merge and
other maintenance proposals), asked as a short either/or question when a conversation winds down.

  ask(mem, topic, turns)   -> at most ONE item {"id", "kind", "text", "options": [{"key", "label"}]} or None. Call it once at the end of a conversation.
                               Longer conversations trigger more often (turns / FULL_TURNS), at most MAX_PER_DAY a day, each item at most MAX_ASKS times,
                               a snoozed item waits; the item closest to what was just talked about (names in `topic`) goes first.
  answer(mem, id, reply)    -> reply is an option key, its letter (a, b, c...), or a plain word (yes, no, same, different, first, newer, both, later).
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
  answer TEXT, answered_at TEXT, PRIMARY KEY(src, ref))"""
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


def ask(mem, topic="", turns=0, today=None, rng=random.random):
    s, day = mem.store, _day(today)
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
    return {"id": f"{it['src']}:{it['ref']}", "kind": it["kind"], "text": it["text"], "options": it["options"]}


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


def answer(mem, item_id, reply, today=None):
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
    if it["kind"] in ("pair", "yesno"):
        yes = ch == "yes"
        if src == "proposal":
            maintenance.answer(mem, int(ref), yes)
        else:   # a review question: make it a merge proposal, so confirming merges (undoable) and rejecting is never proposed again
            a, b = it["ents"]
            with s.lock:
                n = lambda e: s.db.execute("SELECT COUNT(*) FROM event_entities WHERE entity_id=?", (e,)).fetchone()[0]
            frm, into = (a, b) if n(a) <= n(b) else (b, a)
            p = maintenance.propose(mem, "merge", {"from": frm, "into": into}, reason="you answered a question", confidence=1.0, agent="askqueue", allow_auto=False)
            if "id" in p:
                maintenance.answer(mem, p["id"], yes)
    elif src == "review" and ch in ("old", "new", "both"):
        pass   # closing below; the answer reaches the facts through the archive record and the nightly editor
    if src == "review":
        with s.lock:
            s.db.execute("UPDATE review_queue SET status='closed' WHERE id=?", (int(ref),))
            s.db.commit()
    label = next(o["label"] for o in it["options"] if o["key"] == ch)
    with s.lock:
        s.db.execute("UPDATE ask_state SET answer=?, answered_at=? WHERE src=? AND ref=?", (ch, day, src, int(ref)))
        s.db.commit()
    mem.archive.write("askqueue", {"question": it["text"], "answer": label, "said": reply})
    note(s, "askqueue", item_id, "action", item=item_id, choice=ch)
    return {"ok": True, "choice": ch}


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
    print("ask queue ok")
