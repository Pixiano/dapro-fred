"""Mark I memory: backups. Whole memory or any part, each part written as three files:
  native (lossless, restorable: .json / .jsonl tree / .sqlite)  +  .md (readable, complete)  +  .pdf (most compatible).

  backup(mem, out_dir, parts=None, since=None, until=None, pdf=True, blobs=True) -> {"dir": path, "files": {...}}
  verify_backup(dir) -> list of problems (empty = every file matches the manifest's hashes)
  CLI: python backup.py ROOT OUT_DIR [part ...] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--no-pdf] [--no-blobs]

Parts: archive events facts graph logs inferences model chapters digests review runs database (parts=None = all).
since/until narrow the time-based parts (archive, events, logs, digests, review, runs); facts, graph, model, inferences
and chapters are "state of the world" and always whole.
Safety: the output folder must be OUTSIDE any git repo (backups hold personal data); each backup goes into its own
timestamped folder and is built as <name>.partial, renamed only when complete, so a crash never leaves a half backup
that looks finished. Nothing existing is ever overwritten or deleted. Tables that do not exist yet are skipped.
PDF needs fpdf2 (the only reason it is a dependency). Fonts: Arial/Segoe UI from the system; characters those fonts lack
(for example Devanagari) can show as boxes in the PDF only, the .md and native files always keep the exact text.
ponytail: PDF is capped at PDF_MAX_CHARS per part with a note pointing at the .md; raise it if you really want a 5,000-page PDF.
"""
import hashlib
import json
import logging
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.getLogger("fontTools").setLevel(logging.ERROR)  # font subsetting is chatty and harmless
PARTS = ("archive", "events", "facts", "graph", "logs", "inferences", "model", "chapters", "digests", "review", "runs", "database")
PDF_MAX_CHARS = 400_000
MD_RECORD_CHARS = 20_000  # one archive record longer than this is cut in the .md only (the native copy is whole)
_FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu"), Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
_FONTS = {"body": ["arial.ttf", "segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"], "bold": ["arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
          "fallback": ["seguisym.ttf", "segoeui.ttf"]}


def _now():
    return datetime.now(timezone.utc)


def _rows(db, sql, args=()):
    try:
        return [dict(r) for r in db.execute(sql, args)]
    except sqlite3.OperationalError as ex:
        if "no such table" in str(ex):
            return []
        raise


def _in(col, since, until):
    """SQL fragment + args for an optional date range on a column holding a YYYY-MM-DD... prefix."""
    sql, args = "", []
    if since:
        sql += f" AND substr({col},1,10) >= ?"
        args.append(since)
    if until:
        sql += f" AND substr({col},1,10) <= ?"
        args.append(until)
    return sql, args


def _table(rows, cols):
    if not rows:
        return "_(none)_\n"
    esc = lambda v: str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] + ["| " + " | ".join(esc(r.get(c)) for c in cols) + " |" for r in rows]) + "\n"


# ---- one function per part: returns (native tables, markdown) ----
def _events(db, since, until):
    q, a = _in("e.ts", since, until)
    ev = _rows(db, "SELECT e.*, (SELECT group_concat(en.name, ', ') FROM event_entities t JOIN entities en ON en.id=t.entity_id WHERE t.event_id=e.id) AS about "
                   "FROM events e WHERE 1=1" + q + " ORDER BY e.ts, e.id", a)
    md, day = ["# Events\n"], None
    for e in ev:
        if e["ts"][:10] != day:
            day = e["ts"][:10]
            md.append(f"\n## {day}\n")
        md.append(f"- **#{e['id']}** {e['ts'][11:19]} [{e['type']}, importance {e['importance']}]{' about ' + e['about'] if e['about'] else ''}: {e['content']}")
    return {"events": ev}, "\n".join(md) + "\n"


def _facts(db, since, until):
    ents = _rows(db, "SELECT * FROM entities ORDER BY kind, name")
    st = _rows(db, "SELECT s.*, e.kind, e.name FROM entity_states s JOIN entities e ON e.id=s.entity_id ORDER BY e.kind, e.name, s.field, s.valid_from")
    src = _rows(db, "SELECT * FROM sources")
    md = ["# Facts: things and how they changed\n"]
    for e in ents:
        mine = [s for s in st if s["entity_id"] == e["id"]]
        md.append(f"\n## {e['kind']}: {e['name']}\n")
        for f in sorted({s["field"] for s in mine}):
            h = [s for s in mine if s["field"] == f]
            md.append(f"- **{f}**: now **{next((s['value'] for s in reversed(h) if s['valid_to'] is None), '(none)')}**; history: " +
                      "; ".join(f"{s['value']} ({s['valid_from'][:10]} to {(s['valid_to'] or 'now')[:10]})" for s in h))
    return {"entities": ents, "entity_states": st, "sources": src}, "\n".join(md) + "\n"


def _graph(db, since, until):
    rel = _rows(db, "SELECT r.*, a.name AS subject, b.name AS object FROM relationships r JOIN entities a ON a.id=r.subject_id JOIN entities b ON b.id=r.object_id "
                    "ORDER BY r.valid_from, r.id")
    al = _rows(db, "SELECT a.*, e.name AS entity FROM entity_aliases a JOIN entities e ON e.id=a.entity_id ORDER BY e.name, a.alias")
    mg = _rows(db, "SELECT m.*, a.name AS merged, b.name AS into_name FROM entity_merges m JOIN entities a ON a.id=m.entity_id JOIN entities b ON b.id=m.into_id")
    tags = _rows(db, "SELECT t.*, e.name FROM event_entities t JOIN entities e ON e.id=t.entity_id")
    md = ["# Relationship graph\n", "## Relationships\n",
          _table([dict(r, until=(r["valid_to"] or "now")[:10], since=r["valid_from"][:10]) for r in rel], ["subject", "relation", "object", "since", "until", "confidence"]),
          "\n## Other names (aliases)\n", _table(al, ["entity", "alias", "kind"]), "\n## Merges\n", _table(mg, ["merged", "into_name", "merged_at"])]
    return {"relationships": rel, "entity_aliases": al, "entity_merges": mg, "event_entities": tags}, "\n".join(md)


def _logs(db, since, until):
    q, a = _in("day", since, until)
    lg = _rows(db, "SELECT * FROM daily_logs WHERE 1=1" + q + " ORDER BY day", a)
    return {"daily_logs": lg}, "# Daily logs\n\n" + "\n".join(f"## {r['day']}\n\n{r['summary']}\n" for r in lg)


def _inferences(db, since, until):
    inf = _rows(db, "SELECT * FROM inferences ORDER BY id")
    ev = _rows(db, "SELECT v.*, e.content FROM inference_evidence v JOIN events e ON e.id=v.event_id")
    alt = _rows(db, "SELECT * FROM inference_alternatives")
    lg = _rows(db, "SELECT * FROM inference_log ORDER BY id")
    md = ["# Inferences (conclusions that are not direct facts)\n"]
    for i in inf:
        md.append(f"\n## #{i['id']} [{i['category']}, {i['status']}, confidence {i['confidence']}, tier {i.get('tier')}, {i['support_weeks']} weeks] {i['statement']}\n")
        for role in ("support", "counter"):
            for e in (x for x in ev if x["inference_id"] == i["id"] and x["role"] == role):
                md.append(f"- {role}: event #{e['event_id']}: {e['content']}")
        md += [f"- alternative: {x['text']}" for x in alt if x["inference_id"] == i["id"]]
        md += [f"- log {x['week']}: {x['change']}" for x in lg if x["inference_id"] == i["id"]]
    return {"inferences": inf, "inference_evidence": ev, "inference_alternatives": alt, "inference_log": lg}, "\n".join(md) + "\n"


def _model(db, since, until):
    it = _rows(db, "SELECT * FROM model_items ORDER BY category, id")
    lg = _rows(db, "SELECT * FROM model_item_log ORDER BY id")
    vs = _rows(db, "SELECT * FROM model_versions ORDER BY id")
    md = ["# Personal model\n", "_Personal. Items are hypotheses with evidence, not facts._\n"]
    for status in ("active", "demoted", "removed"):
        part = [x for x in it if x["status"] == status]
        if part:
            md.append(f"\n## {status.capitalize()} items\n")
            md += [f"- **#{x['id']}** [{x['category']}, tier {x['tier']}, confidence {x['confidence']:.2f}{', yours' if x['source'] == 'user' else ''}] {x['text']}" for x in part]
    md += ["\n## Versions\n", _table(vs, ["week", "mode", "summary", "built_at"])]
    return {"model_items": it, "model_item_log": lg, "model_versions": vs}, "\n".join(md) + "\n"


def _chapters(db, since, until):
    ch = _rows(db, "SELECT * FROM chapters ORDER BY start_day")
    tp = _rows(db, "SELECT * FROM turning_points ORDER BY day")
    ev = _rows(db, "SELECT * FROM turning_point_evidence")
    lg = _rows(db, "SELECT * FROM chapter_log ORDER BY id")
    md = ["# Life chapters and turning points\n", "## Chapters\n",
          "\n".join(f"- **{c['title']}** ({c['start_day']} to {c['end_day'] or 'now'}{', yours' if c['source'] == 'user' else ''}): {c['description'] or ''}" for c in ch) or "_(none)_",
          "\n## Turning points\n", "\n".join(f"- **{t['day']} {t['title']}** [{t['kind'] or ''}]: {t['description'] or ''}" for t in tp) or "_(none)_"]
    return {"chapters": ch, "turning_points": tp, "turning_point_evidence": ev, "chapter_log": lg}, "\n".join(md) + "\n"


def _digests(db, since, until):
    wq, wa = _in("week", since, until)
    pq, pa = _in("period", since, until)
    wk = _rows(db, "SELECT * FROM weekly_digests WHERE 1=1" + wq + " ORDER BY week", wa)
    pd = _rows(db, "SELECT * FROM period_digests WHERE 1=1" + pq + " ORDER BY kind, period", pa)
    yn = _rows(db, "SELECT * FROM year_notes ORDER BY window, month")
    md = ["# Weekly, monthly and yearly reviews\n", "## Weekly\n"] + [f"### Week ending {r['week']}\n\n{r['summary']}\n" for r in wk]
    for kind in ("monthly", "yearly", "yearly_draft"):
        part = [r for r in pd if r["kind"] == kind]
        if part:
            md += [f"\n## {kind.replace('_', ' ').capitalize()}\n"] + [f"### {r['period']}\n\n{r['summary']}\n" for r in part]
    if yn:
        md += ["\n## Year notes\n"] + [f"- {r['window']} / {r['month']}: {r['note']}" for r in yn]
    return {"weekly_digests": wk, "period_digests": pd, "year_notes": yn}, "\n".join(md) + "\n"


def _review(db, since, until):
    q, a = _in("day", since, until)
    rv = _rows(db, "SELECT * FROM review_queue WHERE 1=1" + q + " ORDER BY id", a)
    rl = _rows(db, "SELECT * FROM review_rulings")
    md = ["# Review queue (contradictions and open questions)\n", _table(rv, ["id", "day", "kind", "status", "text"]), "\n## Rulings\n", _table(rl, ["item_id", "ruling", "week", "note"])]
    return {"review_queue": rv, "review_rulings": rl}, "\n".join(md)


def _runs(db, since, until):
    c = _rows(db, "SELECT * FROM consolidation_runs ORDER BY day")
    w = _rows(db, "SELECT * FROM weekly_runs ORDER BY week")
    p = _rows(db, "SELECT * FROM period_runs ORDER BY kind, period")
    md = ["# Run history\n", "## Nightly\n", _table(c, ["day", "model", "status", "detail", "finished_at"]), "\n## Weekly\n",
          _table(w, ["week", "model", "status", "detail", "finished_at"]), "\n## Monthly and yearly\n", _table(p, ["kind", "period", "model", "status", "detail", "finished_at"])]
    return {"consolidation_runs": c, "weekly_runs": w, "period_runs": p}, "\n".join(md)


_PART_FN = {"events": _events, "facts": _facts, "graph": _graph, "logs": _logs, "inferences": _inferences, "model": _model,
            "chapters": _chapters, "digests": _digests, "review": _review, "runs": _runs}


def _archive(mem, dest, since, until, blobs):
    """Copy the JSONL day files in range (native, byte-identical) and write a readable transcript. Returns (md, record count, blob count)."""
    root, md, n = mem.archive.root, ["# Raw archive\n"], 0
    files = sorted(p for p in root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/*.jsonl"))
    days = {}
    for p in files:
        day = f"{p.parts[-4]}-{p.parts[-3]}-{p.parts[-2]}"
        if (since and day < since) or (until and day > until):
            continue
        out = dest / "archive" / p.relative_to(root)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, out)
        days.setdefault(day, []).append(p)
    for day in sorted(days):
        md.append(f"\n## {day}\n")
        recs = [json.loads(l) for p in days[day] for l in p.read_text(encoding="utf-8").splitlines() if l]
        for r in sorted(recs, key=lambda r: r["ts"]):
            body = json.dumps(r["data"], ensure_ascii=False, default=str)
            if len(body) > MD_RECORD_CHARS:
                body = body[:MD_RECORD_CHARS] + f" ... [cut in this .md only, {len(body)} characters in the .jsonl]"
            md.append(f"- `{r['ts'][11:19]}` **{r['stream']}**{' (historical)' if r.get('kind') == 'historical' else ''}: {body}")
            n += 1
    nb = 0
    if blobs:
        q, a = _in("created_at", since, until)
        for b in _rows(mem.store.db, "SELECT * FROM archive_blobs WHERE 1=1" + q, a):
            src = root / b["path"]
            if src.exists():
                out = dest / "archive" / b["path"]
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, out)
                nb += 1
    return "\n".join(md) + "\n", n, nb


# ---- PDF (fpdf2) ----
def _font(kind):
    for d in _FONT_DIRS:
        for name in _FONTS[kind]:
            if (d / name).exists():
                return str(d / name)
    return None


def _pdf(md, path, title):
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.set_title(title)
    body, bold, fb = _font("body"), _font("bold") or _font("body"), _font("fallback")
    unicode_ok = bool(body)
    if unicode_ok:
        pdf.add_font("body", "", body)
        pdf.add_font("body", "B", bold)
        if fb:
            pdf.add_font("fb", "", fb)
            pdf.set_fallback_fonts(["fb"])
        family = "body"
    else:
        family = "helvetica"
    clean = (lambda s: s) if unicode_ok else (lambda s: s.encode("latin-1", "replace").decode("latin-1"))
    pdf.add_page()
    cut = len(md) > PDF_MAX_CHARS
    for line in md[:PDF_MAX_CHARS].splitlines() + ([f"[PDF cut at {PDF_MAX_CHARS} characters. The complete content is in the .md and the native files.]"] if cut else []):
        size, style = 9, ""
        if line.startswith("# "):
            line, size, style = line[2:], 16, "B"
        elif line.startswith("## "):
            line, size, style = line[3:], 13, "B"
        elif line.startswith("### "):
            line, size, style = line[4:], 11, "B"
        line = clean(line.replace("**", "").replace("`", ""))
        pdf.set_font(family, style, size)
        pdf.multi_cell(0, size * 0.55 + 1.5, line or " ", wrapmode="CHAR", new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))
    return not unicode_ok


# ---- the public API ----
def _guard(out_dir):
    out = Path(out_dir).resolve()
    for p in [out, *out.parents]:
        if (p / ".git").exists():
            raise ValueError(f"{out} is inside a git repo ({p}); backups hold personal data, choose a folder outside it")
    return out


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def backup(mem, out_dir, parts=None, since=None, until=None, pdf=True, blobs=True):
    parts = list(PARTS) if parts in (None, "all") else list(parts)
    bad = [p for p in parts if p not in PARTS]
    if bad:
        raise ValueError(f"unknown part(s) {bad}; choose from {list(PARTS)}")
    out = _guard(out_dir)
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    final = out / f"memory-backup-{stamp}" if parts == list(PARTS) else out / f"memory-backup-{stamp}-{'+'.join(parts)}"
    if final.exists():
        raise FileExistsError(final)
    work = final.with_name(final.name + ".partial")
    work.mkdir(parents=True)
    db, info, notes = mem.store.db, {}, []
    try:
        with mem.store.lock:  # one consistent snapshot of the tables while writing
            for part in parts:
                d = work / part
                d.mkdir()
                if part == "archive":
                    md, n, nb = _archive(mem, work, since, until, blobs)
                    native = {"records": n, "blobs": nb}
                    d.joinpath("README.txt").write_text("The day files under this folder are byte-identical copies of the raw archive (YYYY/MM/DD/<stream>.jsonl), blobs under blobs/.\n", encoding="utf-8")
                elif part == "database":
                    md = None
                    dst = sqlite3.connect(d / "memory.sqlite")
                    db.backup(dst)  # sqlite online backup: consistent copy
                    dst.close()
                    native = {"sqlite": True}
                else:
                    tables, md = _PART_FN[part](db, since, until)
                    d.joinpath(f"{part}.json").write_text(json.dumps(tables, ensure_ascii=False, indent=1, default=str), encoding="utf-8", newline="\n")
                    native = {k: len(v) for k, v in tables.items()}
                if md is not None:
                    d.joinpath(f"{part}.md").write_text(md, encoding="utf-8", newline="\n")
                    if pdf:
                        try:
                            if _pdf(md, d / f"{part}.pdf", f"FRED memory: {part}"):
                                notes.append(f"{part}.pdf: no Unicode font found, non-Latin characters replaced with ? (the .md is exact)")
                        except ImportError:
                            pdf = False
                            notes.append("PDF skipped: install fpdf2 (pip install fpdf2)")
                info[part] = native
        files = {str(p.relative_to(work)).replace("\\", "/"): {"sha256": _sha(p), "bytes": p.stat().st_size} for p in sorted(work.rglob("*")) if p.is_file()}
        (work / "manifest.json").write_text(json.dumps({
            "created_at": _now().isoformat(timespec="seconds"), "parts": parts, "since": since, "until": until, "counts": info, "notes": notes,
            "warning": "Contains personal data. Keep outside the git repo and protect like the memory itself.", "files": files}, indent=1), encoding="utf-8")
        work.rename(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)  # only our own half-built folder
        raise
    return {"dir": str(final), "files": files, "counts": info, "notes": notes}


def verify_backup(path):
    root = Path(path)
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for rel, meta in man["files"].items():
        p = root / rel
        if not p.exists():
            bad.append(f"missing: {rel}")
        elif _sha(p) != meta["sha256"]:
            bad.append(f"changed: {rel}")
    return bad


if __name__ == "__main__" and len(sys.argv) > 2:  # CLI
    import argparse
    from manager import Memory
    ap = argparse.ArgumentParser(description="Back up FRED's memory (whole or by part).")
    ap.add_argument("root", help="the Memory(root) folder")
    ap.add_argument("out", help="folder for backups (outside the git repo)")
    ap.add_argument("parts", nargs="*", help=f"any of: {' '.join(PARTS)} (default: all)")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-blobs", action="store_true")
    a = ap.parse_args()
    with Memory(a.root) as m:
        r = backup(m, a.out, a.parts or None, a.since, a.until, not a.no_pdf, not a.no_blobs)
    print(r["dir"], *r["notes"], sep="\n")
    sys.exit(0)

if __name__ == "__main__":  # self-check: python backup.py
    import logging
    import tempfile
    logging.getLogger("memory").setLevel(logging.CRITICAL)
    from graph import Graph
    from manager import Memory
    with tempfile.TemporaryDirectory() as d, Memory(Path(d) / "mem") as m:
        s = m.store
        g = Graph(s)
        m.archive.write("conversation", {"text": "hello \u00e9\u00e8 pipe | test"}, ts="2026-03-01T10:00:00+00:00")
        m.archive.write("conversation", {"text": "later"}, ts="2026-03-09T10:00:00+00:00")
        m.archive.write("import", {"text": "old chat"}, ts="2026-03-01T11:00:00+00:00", kind="historical")
        blob = Path(d) / "note.txt"
        blob.write_text("blob content", encoding="utf-8")
        m.archive.put_file(blob)
        su, fred = g.resolve("person", "Rina"), g.resolve("project", "FRED")
        s.set_state(fred, "phase", "1", ts="2026-02-01T00:00:00+00:00")
        s.set_state(fred, "phase", "2", ts="2026-03-01T00:00:00+00:00")
        ev = s.add_event("note", "works on fred | at night", ts="2026-03-01T22:00:00+00:00")
        s.add_event("note", "march 9 event", ts="2026-03-09T22:00:00+00:00")
        g.tag(ev, su)
        g.relate(su, "works on", fred, ts="2026-01-01T00:00:00+00:00")
        s.set_daily_log("2026-03-01", "A quiet day.")
        from weekly import Week
        Week(m, '2026-03-08', None)  # creates the weekly tables
        s.db.execute("INSERT INTO weekly_digests VALUES('2026-03-08','week digest','x')")
        s.db.commit()
        out = Path(d) / "backups"
        r = backup(m, out, since=None)
        got = Path(r["dir"])
        assert got.name.startswith("memory-backup-") and not got.name.endswith(".partial") and verify_backup(got) == []
        for part in ("events", "facts", "graph", "logs", "inferences", "model", "chapters", "digests", "review", "runs"):
            assert (got / part / f"{part}.json").exists() and (got / part / f"{part}.md").exists(), part
        assert (got / "database" / "memory.sqlite").exists() and (got / "archive" / "archive.md").exists()
        assert not r["notes"] or "no Unicode font" in r["notes"][0]
        if not r["notes"]:
            assert (got / "events" / "events.pdf").read_bytes()[:5] == b"%PDF-" and (got / "archive" / "archive.pdf").stat().st_size > 500
        assert (got / "archive/2026/03/01/conversation.jsonl").read_bytes() == (m.archive.root / "2026/03/01/conversation.jsonl").read_bytes()  # byte-identical
        assert any(k.startswith("archive/blobs/") for k in r["files"])                                                                  # blobs copied
        assert "FRED" in (got / "facts" / "facts.md").read_text(encoding="utf-8") and "1 (2026-02-01 to 2026-03-01)" in (got / "facts" / "facts.md").read_text(encoding="utf-8")
        assert "Rina | works_on | FRED" in (got / "graph" / "graph.md").read_text(encoding="utf-8")
        assert "(historical)" in (got / "archive" / "archive.md").read_text(encoding="utf-8")
        # a part, with a date range, no pdf, no blobs
        r2 = backup(m, out, ["events", "archive"], since="2026-03-05", pdf=False, blobs=False)
        g2 = Path(r2["dir"])
        assert g2.name.endswith("events+archive") and not list(g2.rglob("*.pdf")) and verify_backup(g2) == []
        assert "march 9 event" in (g2 / "events" / "events.md").read_text(encoding="utf-8") and "works on fred" not in (g2 / "events" / "events.md").read_text(encoding="utf-8")
        assert not (g2 / "archive/2026/03/01").exists() and (g2 / "archive/2026/03/09/conversation.jsonl").exists()
        assert not any(k.startswith("archive/blobs/") for k in r2["files"])
        # tamper detection, unknown part, repo guard, no leftover partial folders
        (got / "logs" / "logs.md").write_text("tampered", encoding="utf-8")
        assert verify_backup(got) == ["changed: logs/logs.md"]
        fake_repo = Path(d) / "repo"
        (fake_repo / ".git").mkdir(parents=True)
        for bad_call in (lambda: backup(m, out, ["nonsense"]), lambda: backup(m, fake_repo / "sub", ["logs"])):
            try:
                bad_call()
                raise SystemExit("bad call accepted")
            except ValueError:
                pass
        assert not (fake_repo / "sub").exists()                                                    # refused before writing anything
        assert not list(out.glob("*.partial"))
        # a part with a missing table (nothing consolidated yet) still backs up
        m2 = Memory(Path(d) / "mem2")
        r3 = backup(m2, out, ["inferences", "chapters", "runs"], pdf=False)
        assert verify_backup(r3["dir"]) == []
        m2.close()
    print("backup ok")
