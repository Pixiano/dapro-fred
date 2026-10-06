"""Mark I memory: precedents (decision memory). What Vatsal decided on a doubt is kept as a precedent, so a near-identical doubt later can be settled by FRED,
with the precedent named. FRED only settles its OWN review items (same-person, contradiction, merge/stale proposals): never basics, never external actions.

  record(mem, item, choice, said, llm, embed)   after an answer: situation (no names/dates), choice, reason, domain, stakes, vector. Also scores what FRED
                                                would have decided (a "shadow"): that is how autonomy is earned. Wire it as askqueue.answer(recorder=...)
  decide(mem, item, llm, embed)                 {"choice","mode":"suggest"|"decide","precedents":[ids]} or None (= ask Vatsal)
  reason_tags(mem, item, choice, embed)         one-tap reason options, only for a first-of-kind case or one that conflicts with a precedent; else None
  outcome(mem, domain, ok, ids)                 Vatsal confirmed (ok) or overrode a suggestion; confirmations raise the level, an override drops it
Code holds every permission; the model only abstracts the situation and judges "what is different here?":
  - the reason is NEVER model-written: it is Vatsal's own words, or "none" (a model-offered reason counts only if it is a verbatim part of what he said)
  - SIM 0.75 was measured with the real Qwen3 embedder (2026-10-06): questions of one kind score 0.78-0.91 with each other, a different kind about 0.5, so 0.9 never matched.
    The embedding only separates kinds of question; what is different about THIS case is the model's difference test (it sees the full text), which is why that test must stay.
  - needs >= MIN_MATCH similar (>= SIM) live precedents of one domain, all with the same choice and none opposed; then the model's difference test
  - autonomy per domain: 0 ask, 1 suggest, 2 decide and tell (capped per domain by stakes; a silent level 3 is not used); a new level takes CONFIRM agreeing shadows/confirmations
  - old precedents fade (half-life a year): 3 close ones must weigh 2.0 together, so three year-old ones are not enough
  - distill(mem, llm, pm): a choice made 3+ times in a domain, with reasons given for at least 2, becomes one 'decision' item of the personal model (wording by the model, no names; code gates it)
"""
import json
import math
import re
from datetime import datetime, timezone

from decisionlog import note, traced
from graph import Graph

SIM, MIN_MATCH, CONFIRM, HALF_LIFE_DAYS, MIN_WEIGHT = 0.75, 3, 3, 365, 2.0   # an old precedent counts less: weight 0.5 ** (age / half-life), the matches must weigh MIN_WEIGHT together
MAX_LEVEL = {"pair": 2, "yesno": 2, "contradiction": 1}   # stakes: a contradiction is only ever suggested; unknown domains stay at 0
STAKES = {"pair": "low", "yesno": "medium", "contradiction": "high"}
REVERSIBLE = {"pair": 1, "yesno": 1, "contradiction": 0}
TAGS = ["I changed my mind", "They are different people", "I was wrong earlier", "Other"]
_SQL = """CREATE TABLE IF NOT EXISTS precedents (id INTEGER PRIMARY KEY, at TEXT NOT NULL, domain TEXT NOT NULL, situation TEXT NOT NULL, options TEXT NOT NULL,
  choice TEXT NOT NULL, reason TEXT NOT NULL, stakes TEXT, reversible INTEGER, vec TEXT NOT NULL, qtext TEXT, item TEXT, overridden INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS autonomy (domain TEXT PRIMARY KEY, level INTEGER NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS precedent_use (id INTEGER PRIMARY KEY, at TEXT NOT NULL, item TEXT, domain TEXT, mode TEXT, choice TEXT, precedent_ids TEXT, detail TEXT)"""
VOCAB = set("yes yeah yep ok sure same no nope different two first old stored before second new newer now latest both changed later skip not person one the a is it please sir "
            "and but they them that this".split())
PROMPT = """You file one decision Vatsal made, for a memory system.
QUESTION asked: {q}
OPTIONS: {opts}
VATSAL ANSWERED: {said!r} (meaning: {choice})
Reply with one JSON object: {{"situation": "<the kind of situation in one sentence, with NO names, dates or places: say 'a person', 'a project'>", "reason": "<ONLY words Vatsal actually said as a reason, copied exactly; otherwise the word none>"}}"""
DIFF = """Vatsal settled earlier cases like this one the same way each time:
{cases}
NEW CASE: {new}
Is there anything in the new case that the earlier cases did not have, such that Vatsal might decide differently? Reply with one JSON object: {{"differs": true|false, "difference": "<short>"}}"""


def ensure(store):
    Graph(store)   # alias tables
    with store.lock:
        store.db.executescript(_SQL)


def _now(today=None):
    return (today or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1)


def scrub(mem, text, extra=()):
    ensure(mem.store)
    """No names or dates in a stored situation: every entity name/alias (and the item's own names) and every date becomes a placeholder."""
    s = mem.store
    with s.lock:
        names = {e["name"] for e in s.entities()} | {r[0] for r in s.db.execute("SELECT alias FROM entity_aliases")}
    for n in sorted(set(names) | set(extra), key=len, reverse=True):
        if len(n.strip()) >= 2:
            text = re.sub(r"(?<!\w)" + re.escape(n.strip()) + r"(?!\w)", "[name]", text, flags=re.I)
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "[date]", text)
    return re.sub(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}\b|\b\d{1,2} (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", "[date]", text)


def _reason(said, offered):
    """Vatsal's own words or 'none'. A model-offered reason only counts as a verbatim part of what he said."""
    sw = " ".join((said or "").lower().split())
    off = " ".join(str(offered or "").lower().split())
    words = lambda s: [w for w in re.findall(r"[\w']+", s) if w not in VOCAB]   # a reason has real content beyond "yeah same person"
    if off != "none" and off in sw and len(words(off)) >= 2:
        return off
    return " ".join(said.split()) if len(words(sw)) >= 2 else "none"


def _matches(mem, domain, vec, today=None):
    s = mem.store
    ensure(s)
    now = datetime.fromisoformat(_now(today))
    with s.lock:
        rows = [dict(r) for r in s.db.execute("SELECT * FROM precedents WHERE domain=? AND overridden=0", (domain,))]
    out = []
    for r in rows:
        r["weight"] = 0.5 ** (max(0, (now - datetime.fromisoformat(r["at"])).total_seconds()) / 86400 / HALF_LIFE_DAYS)
        out.append((_cos(vec, json.loads(r["vec"])), r))
    return sorted(out, key=lambda t: -t[0])


def _eligible(mem, item, embed, situation=None, today=None):
    """The precedent rule, code only: {"choice","ids","cases"} or None."""
    sit = situation or scrub(mem, item["text"], item.get("names", ()))
    ms = _matches(mem, item["kind"], embed(sit, False), today)
    close = [r for s_, r in ms if s_ >= SIM]
    choices = {r["choice"] for r in close}
    if len(close) < MIN_MATCH or len(choices) != 1 or sum(r["weight"] for r in close) < MIN_WEIGHT:
        return None   # too few, too old, or they disagree: ask
    return {"choice": choices.pop(), "ids": [r["id"] for r in close], "cases": [r["qtext"] or r["situation"] for r in close[:3]], "situation": sit}


def level(mem, domain):
    ensure(mem.store)
    with mem.store.lock:
        r = mem.store.db.execute("SELECT level FROM autonomy WHERE domain=?", (domain,)).fetchone()
    return r[0] if r else 0


def outcome(mem, domain, ok, ids=()):
    """ok: Vatsal agreed with what FRED would decide/suggested. CONFIRM in a row raises the level (up to the domain cap); one override drops it a level
    and marks the precedents it rested on as overridden, so they stop counting."""
    s = mem.store
    ensure(s)
    cap = MAX_LEVEL.get(domain, 0)
    with s.lock:
        s.db.execute("INSERT OR IGNORE INTO autonomy(domain) VALUES(?)", (domain,))
        lv, st = s.db.execute("SELECT level, streak FROM autonomy WHERE domain=?", (domain,)).fetchone()
        if ok:
            st += 1
            if st >= CONFIRM and lv < cap:
                lv, st = lv + 1, 0
        else:
            lv, st = max(0, lv - 1), 0
            for i in ids:
                s.db.execute("UPDATE precedents SET overridden=1 WHERE id=?", (i,))
        s.db.execute("UPDATE autonomy SET level=?, streak=? WHERE domain=?", (lv, st, domain))
        s.db.commit()
    note(s, "precedents", domain, "action", outcome="agreed" if ok else "overridden", level=lv, streak=st)
    return lv


def record(mem, item, choice, said, llm, embed, today=None):
    """Keep what was decided. Returns the new precedent id, or None for 'later' (nothing was decided)."""
    s = mem.store
    ensure(s)
    if choice == "later":
        return None
    d = item["kind"]
    elig = _eligible(mem, item, embed, today=today)   # shadow: what FRED would have said, before this answer is added
    if elig:
        outcome(mem, d, elig["choice"] == choice, elig["ids"])
    offered = None
    sit = scrub(mem, item["text"], item.get("names", ()))
    if llm:
        try:
            opts = ", ".join(f"{o['key']}={o['label']}" for o in item["options"])
            out = json.loads(traced(s, "precedents", d, llm)(PROMPT.format(q=scrub(mem, item["text"], item.get("names", ())), opts=opts, said=said, choice=choice)))
            if isinstance(out.get("situation"), str) and out["situation"].strip():
                sit = scrub(mem, out["situation"], item.get("names", ()))
            offered = out.get("reason")
        except Exception:   # a bad model answer: the scrubbed question stands in as the situation
            pass
    reason = _reason(said, offered)
    if str(offered or "none").strip().lower() != "none" and reason == "none":
        note(s, "precedents", d, "override", why="model offered a reason Vatsal did not give", offered=offered, said=said)
    q = scrub(mem, item["text"], item.get("names", ()))   # the same code-built text a new case is compared on (and shown to the difference test)
    vec = [float(x) for x in embed(q, False)]
    with s.lock:
        pid = s.db.execute("INSERT INTO precedents(at,domain,situation,options,choice,reason,stakes,reversible,vec,qtext,item) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (_now(today), d, sit, json.dumps([o["key"] for o in item["options"]]), choice, reason, STAKES.get(d, "high"), REVERSIBLE.get(d, 0),
                            json.dumps(vec), q, item.get("id"))).lastrowid
        s.db.commit()
    return pid


def recorder(mem, llm, embed):
    """For askqueue.answer(recorder=...): item, choice, said."""
    return lambda item, choice, said: record(mem, item, choice, said, llm, embed)


def decide(mem, item, llm, embed, today=None):
    """May FRED settle this itself? Level 0: never. Then the precedent rule, then the model's difference test. Every outcome is logged in precedent_use."""
    s = mem.store
    ensure(s)
    d, lv = item["kind"], level(mem, item["kind"])
    el = _eligible(mem, item, embed, today=today) if lv >= 1 else None
    if not el:
        return None
    try:
        out = json.loads(traced(s, "precedents", d, llm)(DIFF.format(cases="\n".join("- " + c for c in el["cases"]), new=el["situation"])))
        differs = out.get("differs") is not False   # anything but a clear "no difference" means ask
    except Exception:
        differs, out = True, {}
    mode = "suggest" if lv == 1 else "decide"
    with s.lock:
        s.db.execute("INSERT INTO precedent_use(at,item,domain,mode,choice,precedent_ids,detail) VALUES(?,?,?,?,?,?,?)",
                     (_now(today), item.get("id"), d, "asked: " + str(out.get("difference", "unsure")) if differs else mode, el["choice"], json.dumps(el["ids"]), json.dumps(out)))
        s.db.commit()
    return None if differs else {"choice": el["choice"], "mode": mode, "precedents": el["ids"]}


DISTILL = """Vatsal has settled {n} similar doubts the same way. The doubts (names removed):
{cases}
His choice each time: {label}. Reasons he gave: {reasons}.
Write ONE short sentence describing this as a habit of Vatsal's ("When ..., he usually ..."). No names, no dates, no guesses beyond what the cases show. Reply with one JSON object: {{"pattern": "<sentence>"}}"""


def distill(mem, llm, pm):
    """Weekly: a choice made at least MIN_MATCH times (live precedents, same domain and choice, none of the opposite choice among them) becomes one
    personal-model item (PersonalModel.add_decision_pattern). Returns the ids added."""
    s, added = mem.store, []
    ensure(s)
    with s.lock:
        rows = [dict(r) for r in s.db.execute("SELECT * FROM precedents WHERE overridden=0 AND choice!='later' ORDER BY id")]
    for d in sorted({r["domain"] for r in rows}):
        by = {}
        for r in (r for r in rows if r["domain"] == d):
            by.setdefault(r["choice"], []).append(r)
        if len(by) != 1:
            continue   # mixed answers in a domain are not a habit
        choice, rs = next(iter(by.items()))
        if len(rs) < MIN_MATCH or pm.has_pattern(f"{d}/{choice}") or sum(r["reason"] != "none" for r in rs) < MIN_MATCH - 1:
            continue   # without reasons a pattern only repeats the precedents, which decide() already uses
        try:
            reasons = "; ".join(r["reason"] for r in rs if r["reason"] != "none") or "none given"
            out = json.loads(traced(s, "precedents", d, llm)(DISTILL.format(n=len(rs), cases=chr(10).join("- " + (r["qtext"] or r["situation"]) for r in rs[-5:]),
                                                                          label=choice, reasons=reasons)))
            text = scrub(mem, str(out.get("pattern", "")))
        except Exception:
            continue
        if len(text.split()) >= 4 and "[name]" not in text and "[date]" not in text:
            iid = pm.add_decision_pattern(f"{d}/{choice}", text)
            if iid:
                added.append(iid)
    return added


def reason_tags(mem, item, choice, embed, today=None):
    """Ask for a reason only when it is the first case of its kind, or the answer conflicts with a similar precedent. One tap, never free text."""
    ms = _matches(mem, item["kind"], embed(scrub(mem, item["text"], item.get("names", ())), False), today)
    close = [r for s_, r in ms if s_ >= SIM]
    return TAGS if not close or any(r["choice"] != choice for r in close) else None


if __name__ == "__main__":  # self-check: python precedents.py
    import logging
    import tempfile
    from datetime import timedelta
    from manager import Memory
    logging.getLogger("memory").setLevel(logging.ERROR)

    def embed(t, q):   # bag of words, 64 hashed buckets
        v = [0.0] * 64
        for w in re.findall(r"[a-z]+", t.lower()):
            v[sum(map(ord, w)) % 64] += 1
        return v

    OPTS = [{"key": "yes", "label": "Same one"}, {"key": "no", "label": "Two different ones"}, {"key": "later", "label": "Ask me later"}]
    mk = lambda a, b: {"id": f"review:{a}", "kind": "pair", "names": (a, b), "options": OPTS,
                       "text": f"Quick one, sir: {a} (lives in Pune) and {b} (came to tuition). Same one, or two different ones?"}
    sit = lambda p: json.dumps({"situation": "Two similar names, one lives in another city. Same person?", "reason": "none"})
    with tempfile.TemporaryDirectory() as d, Memory(d) as m:
        s = m.store
        for n in ("Nitu", "Nitya", "Ravi", "Ravina", "Sam", "Samir", "Tia", "Tiana"):
            s.entity("person", n)
        # 1. the model invents a reason for a bare "b": code refuses it and logs the override
        liar = lambda p: json.dumps({"situation": "Two people, one in another city. Same person?", "reason": "because they seem similar"})
        pid = record(m, mk("Nitu", "Nitya"), "no", "b", liar, embed)
        assert s.db.execute("SELECT reason FROM precedents WHERE id=?", (pid,)).fetchone()[0] == "none"
        assert s.db.execute("SELECT COUNT(*) FROM decision_log WHERE agent='precedents' AND kind='override'").fetchone()[0] == 1
        # 2. Vatsal's own words are kept; a verbatim model extract is accepted; a model paraphrase is not
        said = "same, she's my cousin from Delhi"
        ext = lambda p: json.dumps({"situation": "x", "reason": "she's my cousin"})
        assert s.db.execute("SELECT reason FROM precedents WHERE id=?", (record(m, mk("Ravi", "Ravina"), "yes", said, ext, embed),)).fetchone()[0] == "she's my cousin"
        assert _reason("same, she's my cousin", "they share a family") == "same, she's my cousin"
        assert _reason("yeah same person", "yeah same person") == "none"      # seen live: the model echoed a bare confirmation as its "reason"
        assert _reason("yes", None) == "none" and _reason("later please sir", None) == "none"
        # 3. names and dates never reach a stored situation, even if the model echoes them
        echo = lambda p: json.dumps({"situation": "Nitu and Nitya on 2026-03-05, March 5", "reason": "none"})
        row = s.db.execute("SELECT situation FROM precedents WHERE id=?", (record(m, mk("Nitu", "Nitya"), "no", "no", echo, embed),)).fetchone()[0]
        assert "Nitu" not in row and "Nitya" not in row and "2026" not in row and "March 5" not in row, row
        assert record(m, mk("Nitu", "Nitya"), "later", "later", sit, embed) is None
        # 4. first-of-kind or conflicting cases get one-tap reasons; settled kinds do not
        m2 = Memory(tempfile.mkdtemp())
        assert reason_tags(m2, mk("A", "B"), "yes", embed) == TAGS                      # nothing similar yet: ask why
        m2.close()
    with tempfile.TemporaryDirectory() as d, Memory(d) as m:
        s = m.store
        item = mk("Sam", "Samir")
        no_diff = lambda p: json.dumps({"differs": False, "difference": ""}) if "earlier cases" in p else sit(p)
        assert decide(m, item, no_diff, embed) is None                                    # no precedents: ask
        for i in range(MIN_MATCH):                                                        # three agreeing answers (shadows start counting at the 3rd)
            record(m, mk(f"A{i}", f"B{i}"), "no", "no", sit, embed)
        assert level(m, "pair") == 0 and decide(m, item, no_diff, embed) is None          # level 0 never decides, whatever the precedents say
        for i in range(CONFIRM):                                                          # each further agreeing answer is a correct shadow decision
            record(m, mk(f"C{i}", f"D{i}"), "no", "no", sit, embed)
        assert level(m, "pair") == 1
        assert reason_tags(m, item, "no", embed) is None and reason_tags(m, item, "yes", embed) == TAGS   # settled kind: no question, unless the answer goes against it
        r = decide(m, item, no_diff, embed)
        assert r["choice"] == "no" and r["mode"] == "suggest" and len(r["precedents"]) >= MIN_MATCH
        assert decide(m, item, lambda p: json.dumps({"differs": True, "difference": "new city"}) if "earlier cases" in p else sit(p), embed) is None   # a difference: ask
        assert decide(m, item, lambda p: "garbage", embed) is None                        # unusable difference test: ask
        assert "asked: new city" in [r[0] for r in s.db.execute("SELECT mode FROM precedent_use")][-2]
        assert s.db.execute("SELECT COUNT(*) FROM precedent_use WHERE mode='suggest'").fetchone()[0] == 1
        outcome(m, "pair", False, r["precedents"])                                       # Vatsal overrides: level drops, those precedents stop counting
        assert level(m, "pair") == 0 and decide(m, item, no_diff, embed) is None
        for _ in range(CONFIRM * 5):
            outcome(m, "contradiction", True)
        assert level(m, "contradiction") == 1 and level(m, "alerts") == 0                 # high stakes capped at suggesting; unknown domains never rise
        # opposed precedents block, and old ones expire
        m3 = Memory(tempfile.mkdtemp())
        for i, c in enumerate(["yes", "yes", "yes", "no"]):
            record(m3, mk(f"E{i}", f"F{i}"), c, c, sit, embed)
        assert _eligible(m3, mk("Gita", "Hari"), embed) is None
        for i in range(3):
            record(m3, mk(f"I{i}", f"J{i}"), "yes", "yes", sit, embed)
        assert _eligible(m3, mk("Gita", "Hari"), embed) is None   # 4 yes + 1 no earlier: still opposed
        m4 = Memory(tempfile.mkdtemp())
        for i in range(3):
            record(m4, mk(f"K{i}", f"L{i}"), "no", "no, they live in different cities" if i < 2 else "no", sit, embed)
        assert _eligible(m4, mk("Gita", "Hari"), embed)["choice"] == "no"
        old = datetime.now(timezone.utc) + timedelta(days=365)
        assert _eligible(m4, mk("Gita", "Hari"), embed, today=old) is None                        # a year later three precedents weigh 1.5: ask again
        assert _eligible(m4, mk("Gita", "Hari"), embed, today=datetime.now(timezone.utc) + timedelta(days=180)) is not None
        # distillation: only an all-agreeing domain with 3+ cases, once, wording checked for names
        from personal_model import PersonalModel
        pm = PersonalModel(m4, embed)
        pat = lambda p: json.dumps({"pattern": "When two people have near-identical names but different details, he usually treats them as different people."})
        assert len(distill(m4, pat, pm)) == 1 and distill(m4, pat, pm) == []
        assert distill(m3, pat, PersonalModel(m3, embed)) == []                               # mixed answers: no habit
        m6 = Memory(tempfile.mkdtemp())
        for i in range(3):
            record(m6, mk(f"Q{i}", f"R{i}"), "no", "no", sit, embed)
        assert distill(m6, pat, PersonalModel(m6, embed)) == []                               # no reasons given: nothing worth distilling
        m6.close()
        bad = lambda p: json.dumps({"pattern": "He always says Nitu is different."})
        m5 = Memory(tempfile.mkdtemp())
        m5.store.entity("person", "Nitu")
        for i in range(3):
            record(m5, mk(f"M{i}", f"N{i}"), "no", "no, they live in different cities", sit, embed)
        assert distill(m5, bad, PersonalModel(m5, embed)) == []                               # a name in the wording: refused
        for mm in (m3, m4, m5):
            mm.close()
    print("precedents ok")
