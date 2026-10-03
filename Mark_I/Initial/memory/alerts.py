"""Mark I memory: yearly alerts (birthdays, anniversaries, festivals, small memorable days). Design: Documentation/2026/10/05/done.md.

Two sources, kept apart on purpose:
  - PERSONAL alerts live in the private memory database (table yearly_alerts), never in the repo. They come from Vatsal's own list
    (load_seed from a file OUTSIDE the repo) or from conversation: anything FRED suggests itself is saved as "proposed" and only becomes
    active once Vatsal confirms it (confirm / reject). A date Vatsal was unsure of is stored with certain=0 and is shown as unsure until fix_date.
  - PUBLIC days (festivals, national days, fun facts) come from calendar_india.json, which gen_calendar.py builds (movable festivals are
    computed per year, fixed ones are listed). Nothing personal is in it.

Sockets: add / confirm / reject / remove / fix_date / pending, due(mem, today, ahead) is what FRED reads (at the first greeting of the day,
or when asked "anything coming up?"). Nothing is ever deleted: removed and rejected alerts keep their row and log.
Feb 29 rule: a personal birthday on Feb 29 is shown on Feb 28 in non-leap years; "skip" (Leap Day itself) is only shown in leap years.
ponytail: dates are month/day only (no time zones, no lunar calculation here); movable festivals come from the generated JSON, so past its last year
(see gen_calendar.py) only the fixed days show until it is regenerated.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("memory")
_HERE = Path(__file__).parent
KINDS = {"birthday", "anniversary", "memorial", "festival", "special", "fun_fact"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS yearly_alerts (
  id INTEGER PRIMARY KEY, month INTEGER NOT NULL, day INTEGER NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
  important INTEGER NOT NULL DEFAULT 0, certain INTEGER NOT NULL DEFAULT 1, note TEXT, year INTEGER,   -- year: birth or start year, if known
  source TEXT NOT NULL, status TEXT NOT NULL, leap_rule TEXT NOT NULL DEFAULT 'feb28',                 -- source user|model; status active|proposed|rejected|removed
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alert_log (id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL REFERENCES yearly_alerts(id), change TEXT NOT NULL, at TEXT NOT NULL);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s):
    return " ".join(s.lower().split())


def _db(mem):
    with mem.store.lock:
        mem.store.db.executescript(_SCHEMA)
    return mem.store


def _log(s, aid, change):
    s.db.execute("INSERT INTO alert_log(alert_id,change,at) VALUES(?,?,?)", (aid, change, _now()))


def _check_date(month, day):
    date(2000, int(month), int(day))  # 2000 is a leap year: Feb 29 is allowed
    return int(month), int(day)


def add(mem, month, day, title, kind="special", important=False, certain=True, note="", year=None, source="user", leap_rule=None):
    """Returns the alert id (the existing one if the same title is already on that day). source "model" is saved as proposed, never active."""
    month, day = _check_date(month, day)
    if kind not in KINDS or source not in ("user", "model") or not title.strip():
        raise ValueError(f"bad alert: kind must be one of {sorted(KINDS)}, source user or model, title not empty")
    s = _db(mem)
    with s.lock:
        for r in s.db.execute("SELECT id, status FROM yearly_alerts WHERE month=? AND day=? AND status IN ('active','proposed')", (month, day)):
            if _norm(s.db.execute("SELECT title FROM yearly_alerts WHERE id=?", (r["id"],)).fetchone()[0]) == _norm(title):
                return r["id"]
        now = _now()
        cur = s.db.execute("INSERT INTO yearly_alerts(month,day,title,kind,important,certain,note,year,source,status,leap_rule,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (month, day, title.strip(), kind, int(bool(important)), int(bool(certain)), note, year, source, "active" if source == "user" else "proposed",
                            leap_rule or ("skip" if (month, day) == (2, 29) and kind == "festival" else "feb28"), now, now))
        _log(s, cur.lastrowid, f"added by {source}")
        s.db.commit()
        return cur.lastrowid


def _set(mem, aid, change, **fields):
    s = _db(mem)
    with s.lock:
        if not s.db.execute("SELECT 1 FROM yearly_alerts WHERE id=?", (aid,)).fetchone():
            raise ValueError(f"no alert {aid}")
        s.db.execute(f"UPDATE yearly_alerts SET {','.join(k + '=?' for k in fields)}, updated_at=? WHERE id=?", [*fields.values(), _now(), aid])
        _log(s, aid, change)
        s.db.commit()


def confirm(mem, aid):
    """Vatsal said yes to a proposed alert."""
    _set(mem, aid, "confirmed by Vatsal", status="active")


def reject(mem, aid):
    _set(mem, aid, "rejected by Vatsal", status="rejected")


def remove(mem, aid):
    _set(mem, aid, "removed by Vatsal", status="removed")


def fix_date(mem, aid, month, day):
    """Vatsal gave the real date of an alert he was unsure about."""
    month, day = _check_date(month, day)
    _set(mem, aid, f"date fixed to {month:02d}-{day:02d}", month=month, day=day, certain=1)


def pending(mem):
    """Alerts FRED proposed that wait for Vatsal's yes/no, and active ones whose date he was unsure about (to ask once)."""
    s = _db(mem)
    with s.lock:
        return [dict(r) for r in s.db.execute("SELECT * FROM yearly_alerts WHERE status='proposed' OR (status='active' AND certain=0) ORDER BY month, day")]


@lru_cache(maxsize=1)
def _public():
    p = _HERE / "calendar_india.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"fixed": [], "movable": {}}


def _leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _hits(month, day, rule, d):
    if (month, day) == (d.month, d.day):
        return True
    return (month, day) == (2, 29) and rule == "feb28" and (d.month, d.day) == (2, 28) and not _leap(d.year)


def due(mem, today=None, ahead=2):
    """Everything on `today` and the next `ahead` days: personal alerts (active only) and public days. Important ones first within a day.
    Items: {"date", "days_away", "title", "kind", "important", "certain", "note", "age"}. Unsure dates carry certain=False so FRED can say so."""
    today = today or datetime.now(timezone.utc).date()
    s = _db(mem)
    with s.lock:
        mine = [dict(r) for r in s.db.execute("SELECT * FROM yearly_alerts WHERE status='active'")]
    pub, out = _public(), []
    for n in range(ahead + 1):
        d = today + timedelta(days=n)
        for r in mine:
            if _hits(r["month"], r["day"], r["leap_rule"], d):
                age = d.year - r["year"] if r["year"] and r["kind"] in ("birthday", "anniversary") else None
                out.append({"date": d.isoformat(), "days_away": n, "title": r["title"], "kind": r["kind"], "important": bool(r["important"]),
                            "certain": bool(r["certain"]), "note": r["note"] or "", "age": age, "id": r["id"]})
        for m, dd, title, kind, imp, note in pub.get("fixed", []):
            if _hits(m, dd, "skip" if (m, dd) == (2, 29) else "feb28", d) and not ((m, dd) == (2, 29) and (d.month, d.day) != (2, 29)):
                out.append({"date": d.isoformat(), "days_away": n, "title": title, "kind": kind, "important": bool(imp), "certain": True, "note": note, "age": None})
        for iso, title, imp, note in pub.get("movable", {}).get(str(d.year), []):
            if iso == d.isoformat():
                out.append({"date": iso, "days_away": n, "title": title, "kind": "festival", "important": bool(imp), "certain": True, "note": note, "age": None})
    return sorted(out, key=lambda x: (x["days_away"], not x["important"], x["title"]))


def load_seed(mem, path):
    """Vatsal's own list from a JSON file kept OUTSIDE the repo: [{"month","day","title","kind","important","certain","note","year"}...].
    These are his words, so they are saved active. Idempotent. Returns how many were new."""
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    n = 0
    s = _db(mem)
    for it in items:
        with s.lock:
            had = s.db.execute("SELECT COUNT(*) FROM yearly_alerts").fetchone()[0]
        add(mem, it["month"], it["day"], it["title"], it.get("kind", "special"), it.get("important", False), it.get("certain", True), it.get("note", ""), it.get("year"), "user")
        with s.lock:
            n += s.db.execute("SELECT COUNT(*) FROM yearly_alerts").fetchone()[0] - had
    return n


def oldest_date(folder, suffixes=None):
    """(YYYY-MM-DD, relative path) of the file with the oldest modified time under folder: the date a project folder really starts
    (modified times survive copies; creation times do not). Used to settle FRED's own birthday from its oldest files."""
    root, best = Path(folder), None
    for dirpath, _, names in os.walk(root):
        for n in names:
            if suffixes and not n.lower().endswith(tuple(suffixes)):
                continue
            p = Path(dirpath) / n
            try:
                t = p.stat().st_mtime
            except OSError:
                continue
            if best is None or t < best[0]:
                best = (t, p)
    return (datetime.fromtimestamp(best[0]).date().isoformat(), best[1].relative_to(root).as_posix()) if best else None


if __name__ == "__main__":  # self-check: python alerts.py
    import tempfile
    from manager import Memory
    logging.getLogger("memory").setLevel(logging.CRITICAL)
    with tempfile.TemporaryDirectory() as d, Memory(Path(d) / "mem") as m:
        a = add(m, 3, 31, "Rina's birthday", "birthday", important=True, year=2010)
        assert add(m, 3, 31, "  rina's BIRTHDAY ", "birthday") == a                                    # same day, same title: no duplicate
        b = add(m, 9, 10, "Kabir's birthday", "birthday", certain=False, note="not sure of the date")
        c = add(m, 2, 29, "Tara's birthday", "birthday", year=2012)
        p = add(m, 5, 5, "Maybe a trip anniversary", "anniversary", source="model")                  # FRED's own idea: waits for a yes
        for bad in (lambda: add(m, 2, 30, "x"), lambda: add(m, 1, 1, "x", kind="nonsense"), lambda: add(m, 1, 1, "  "), lambda: add(m, 1, 1, "x", source="robot")):
            try:
                bad()
                raise SystemExit("bad alert accepted")
            except ValueError:
                pass
        day = lambda y, mo, dd, ahead=0: [(x["title"], x["days_away"]) for x in due(m, date(y, mo, dd), ahead) if x["kind"] in ("birthday", "anniversary")]
        assert day(2026, 3, 31) == [("Rina's birthday", 0)] and day(2026, 3, 29, 2) == [("Rina's birthday", 2)]           # a heads-up
        assert [x["age"] for x in due(m, date(2026, 3, 31)) if x["kind"] == "birthday"] == [16] and day(2026, 5, 5) == []                               # proposed alerts are silent
        assert [x["certain"] for x in due(m, date(2026, 9, 10)) if x["kind"] == "birthday"] == [False]                       # unsure dates say so
        assert day(2027, 2, 28) == [("Tara's birthday", 0)] and day(2028, 2, 28) == [] and day(2028, 2, 29) == [("Tara's birthday", 0)]   # Feb 29: Feb 28 in common years
        assert [x["id"] for x in pending(m)] == [p, b]                                                                       # unsure + proposed are what FRED must ask about
        confirm(m, p)
        fix_date(m, b, 9, 12)
        assert pending(m) == [] and day(2026, 5, 5) == [("Maybe a trip anniversary", 0)] and day(2026, 9, 12) == [("Kabir's birthday", 0)] and day(2026, 9, 10) == []
        reject(m, add(m, 6, 6, "Suggested thing", source="model"))
        remove(m, a)
        assert day(2026, 3, 31) == [] and m.store.db.execute("SELECT COUNT(*) FROM yearly_alerts").fetchone()[0] == 5                # nothing is deleted
        assert [r[0] for r in m.store.db.execute("SELECT change FROM alert_log WHERE alert_id=?", (a,))] == ["added by user", "removed by Vatsal"]
        try:
            confirm(m, 999)
            raise SystemExit("unknown alert accepted")
        except ValueError:
            pass
        # public days: fixed, movable per year, leap day only in leap years, important first
        pub = {"fixed": [[1, 1, "New Year's Day", "festival", 1, ""], [2, 28, "Science day", "fun_fact", 0, "a fact"], [2, 29, "Leap Day", "fun_fact", 0, ""]],
               "movable": {"2026": [["2026-03-04", "Holi", 1, ""]], "2027": [["2027-03-22", "Holi", 1, "estimated"]]}}
        _public = lambda: pub                                                                   # the test calendar replaces the JSON file
        t = lambda y, mo, dd, ahead=0: [x["title"] for x in due(m, date(y, mo, dd), ahead)]
        assert t(2026, 1, 1) == ["New Year's Day"] and t(2026, 3, 4) == ["Holi"] and t(2027, 3, 4) == [] and t(2027, 3, 22) == ["Holi"]
        assert t(2027, 2, 28) == ["Tara's birthday", "Science day"] or t(2027, 2, 28) == ["Science day", "Tara's birthday"]
        assert "Leap Day" not in t(2027, 2, 27, 2) and "Leap Day" in t(2028, 2, 28, 1)
        # seed file: his own list, idempotent
        seed = Path(d) / "seed.json"
        seed.write_text(json.dumps([{"month": 4, "day": 4, "title": "Mira's birthday", "kind": "birthday", "certain": False, "note": "about the 4th"},
                                    {"month": 8, "day": 8, "title": "Our anniversary", "kind": "anniversary", "important": True}]), encoding="utf-8")
        assert load_seed(m, seed) == 2 and load_seed(m, seed) == 0
        assert [x["title"] for x in pending(m)] == ["Mira's birthday"]
        # oldest file of a folder
        f = Path(d) / "folder"
        (f / "sub").mkdir(parents=True)
        for name, ts in (("new.txt", 1_800_000_000), ("sub/old.txt", 1_699_963_200), ("sub/old.log", 1_599_998_400)):
            (f / name).write_text("x")
            os.utime(f / name, (ts, ts))
        assert oldest_date(f) == ("2020-09-13", "sub/old.log") and oldest_date(f, [".txt"]) == ("2023-11-14", "sub/old.txt") and oldest_date(Path(d) / "nothing") is None
    print("alerts ok")
