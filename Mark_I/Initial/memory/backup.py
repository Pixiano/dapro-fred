"""Mark I memory: backups. Whole memory or any part, each part written as three files:
  native (lossless, restorable: .json / .jsonl tree / .sqlite)  +  .md (readable, complete)  +  .pdf (most compatible).

  backup(mem, out_dir, parts=None, since=None, until=None, pdf=True, blobs=True) -> {"dir": path, "files": {...}}
  verify_backup(dir) -> list of problems (empty = every file matches the manifest's hashes)
  CLI: python backup.py ROOT OUT_DIR [part ...] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--no-pdf] [--no-blobs]

Parts: archive events facts graph logs inferences model chapters digests review runs database (parts=None = all).
since/until narrow the time-based parts (archive, events, logs, digests, review, runs); facts, graph, model, inferences
and chapters are "state of the world" and always whole.
Formats: every part always gets its native file and the .md. Beyond that, each part has the format that suits it best:
the relationship graph and the life-chapter timeline default to a PNG picture (a node-and-arrow drawing, a per-track
timeline) instead of a PDF; everything else defaults to a PDF. Override per part with formats={"graph": ["md", "pdf", "png"]};
pdf=False switches PDFs off everywhere. PNGs need pillow + networkx (already in the environment); without them the PNG is
skipped with a note.
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
DEFAULT_FORMATS = {p: ("md", "pdf") for p in PARTS}
DEFAULT_FORMATS.update(graph=("md", "png"), chapters=("md", "png"))
FORMATS = {"md", "pdf", "png"}
MAX_NODES = 80  # the graph picture shows the best-connected entities; the .md and .json always list everything
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
    rel = _rows(db, "SELECT r.*, a.name AS subject, a.kind AS subject_kind, b.name AS object, b.kind AS object_kind FROM relationships r JOIN entities a ON a.id=r.subject_id JOIN entities b ON b.id=r.object_id "
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
    md = ["# Life chapters and turning points\n", "Chapters run on parallel tracks (a project, social life, school...); several can be open at once.\n"]
    for track in sorted({c.get("track", "general") for c in ch}):
        md.append(f"\n## Track: {track}\n")
        md += [f"- **{c['title']}** ({c['start_day']} to {c['end_day'] or 'now'}{', yours' if c['source'] == 'user' else ''}): {c['description'] or ''}"
               for c in ch if c.get("track", "general") == track]
    md += ["" if ch else "_(no chapters)_", "\n## Turning points\n",
           "\n".join(f"- **{t['day']} {t['title']}** [{t['kind'] or ''}]: {t['description'] or ''}" for t in tp) or "_(none)_"]
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


# ---- pictures (pillow + networkx) ----
_KIND_COLOR = {"person": "#3b82f6", "project": "#16a34a", "place": "#f59e0b", "goal": "#a855f7", "task": "#06b6d4", "preference": "#ec4899"}
_TRACK_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#0891b2", "#db2777", "#65a30d", "#dc2626"]


def _pil_font(size, bold=False):
    from PIL import ImageFont
    p = _font("bold" if bold else "body")
    return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)


def _dashed(dr, a, b, color, width, dash=14):
    import math
    L = math.dist(a, b) or 1
    for i in range(0, int(L // dash) + 1, 2):
        s, e = i * dash / L, min((i + 1) * dash / L, 1)
        dr.line([(a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s), (a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e)], fill=color, width=width)


def _png_graph(tables, path):
    """Node-and-arrow drawing of the relationship graph. Solid dark = true now, dashed pale = ended."""
    import math
    import networkx as nx
    from PIL import Image, ImageDraw
    rel = tables["relationships"]
    W, H, R = 2200, 1500, 34
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    dr.text((40, 28), "Relationship graph", font=_pil_font(34, True), fill="#0f172a")
    if not rel:
        dr.text((40, 100), "(no relationships yet)", font=_pil_font(22), fill="#475569")
        img.save(path)
        return
    G, kinds = nx.Graph(), {}
    for r in rel:
        G.add_edge(r["subject"], r["object"])
        kinds[r["subject"]], kinds[r["object"]] = r.get("subject_kind", ""), r.get("object_kind", "")
    total = G.number_of_nodes()
    if total > MAX_NODES:
        keep = {n for n, _ in sorted(G.degree, key=lambda x: -x[1])[:MAX_NODES]}
        G = G.subgraph(keep).copy()
    pos = nx.spring_layout(G, seed=1, k=1.8 / max(1, G.number_of_nodes()) ** 0.5, iterations=250)
    xs, ys = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
    fx = lambda v: 140 + (v - min(xs)) / ((max(xs) - min(xs)) or 1) * (W - 280) if len(xs) > 1 else W / 2
    fy = lambda v: 190 + (v - min(ys)) / ((max(ys) - min(ys)) or 1) * (H - 380) if len(ys) > 1 else H / 2
    P = {n: (fx(p[0]), fy(p[1])) for n, p in pos.items()}
    seen, small = {}, _pil_font(15)
    for r in sorted(rel, key=lambda r: r["valid_to"] is None):  # ended edges first, current ones drawn on top
        a, b = r["subject"], r["object"]
        if a not in P or b not in P:
            continue
        cur = r["valid_to"] is None
        color, width = ("#334155", 3) if cur else ("#cbd5e1", 2)
        p1, p2 = P[a], P[b]
        d = math.dist(p1, p2) or 1
        q2 = (p2[0] - (p2[0] - p1[0]) / d * R, p2[1] - (p2[1] - p1[1]) / d * R)
        (dr.line([p1, q2], fill=color, width=width) if cur else _dashed(dr, p1, q2, color, width))
        ux, uy = (p2[0] - p1[0]) / d, (p2[1] - p1[1]) / d
        dr.polygon([q2, (q2[0] - ux * 18 - uy * 8, q2[1] - uy * 18 + ux * 8), (q2[0] - ux * 18 + uy * 8, q2[1] - uy * 18 - ux * 8)], fill=color)
        k = seen.setdefault(frozenset((a, b)), 0)
        seen[frozenset((a, b))] = k + 1
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2 + k * 18 - 9
        dr.text((mx, my), r["relation"].replace("_", " "), font=small, fill="#0f172a" if cur else "#94a3b8", anchor="mm", stroke_width=3, stroke_fill="white")
    for n, (x, y) in P.items():
        dr.ellipse([x - R, y - R, x + R, y + R], fill=_KIND_COLOR.get(kinds.get(n), "#64748b"), outline="#0f172a", width=2)
        dr.text((x, y + R + 8), n, font=_pil_font(19, True), fill="#0f172a", anchor="mt", stroke_width=3, stroke_fill="white")
    lx = 40
    for kd in sorted({kinds[n] for n in P}):
        dr.ellipse([lx, H - 70, lx + 22, H - 48], fill=_KIND_COLOR.get(kd, "#64748b"), outline="#0f172a")
        dr.text((lx + 32, H - 59), kd or "other", font=_pil_font(18), fill="#0f172a", anchor="lm")
        lx += 40 + 12 * len(kd or "other") + 30
    dr.text((W - 40, H - 59), f"solid = true now, dashed pale = ended   |   {total} entities, {len(rel)} relationships"
            + (f"   |   showing the {MAX_NODES} best connected" if total > MAX_NODES else ""), font=_pil_font(17), fill="#475569", anchor="rm")
    img.save(path)


def _png_timeline(tables, path):
    """Chapters as horizontal bars, one row group per track (parallel chapters sit side by side; chapters that overlap
    inside one track stack in lanes), turning points as diamonds."""
    from datetime import date
    from PIL import Image, ImageDraw
    ch, tp = tables["chapters"], tables.get("turning_points", [])
    W = 2200
    if not ch:
        img = Image.new("RGB", (W, 260), "white")
        d = ImageDraw.Draw(img)
        d.text((40, 28), "Life chapters", font=_pil_font(34, True), fill="#0f172a")
        d.text((40, 100), "(no chapters yet)", font=_pil_font(22), fill="#475569")
        img.save(path)
        return
    D = date.fromisoformat
    today = _now().date()
    days = [D(c["start_day"]) for c in ch] + [D(c["end_day"]) for c in ch if c["end_day"]] + [D(t["day"]) for t in tp]
    lo, hi = min(days), max(days + [today])
    span = max(1, (hi - lo).days)
    tr_of = lambda c: c.get("track", "general")
    tracks = sorted({tr_of(c) for c in ch}, key=lambda t: min(c["start_day"] for c in ch if tr_of(c) == t))
    lane, nlanes = {}, {}
    for tr in tracks:  # greedy lane assignment inside a track
        ends = []
        for i, c in sorted(enumerate(ch), key=lambda x: x[1]["start_day"]):
            if tr_of(c) != tr:
                continue
            s_, e_ = D(c["start_day"]), (D(c["end_day"]) if c["end_day"] else hi)
            for k, en in enumerate(ends):
                if s_ > en:
                    ends[k], lane[i] = e_, k
                    break
            else:
                ends.append(e_)
                lane[i] = len(ends) - 1
        nlanes[tr] = max(1, len(ends))
    L, RM, top, row = 230, 60, 190, 62
    H = top + sum(nlanes.values()) * row + 130
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    X = lambda d: L + (d - lo).days / span * (W - L - RM)
    dr.text((40, 28), "Life chapters and turning points", font=_pil_font(34, True), fill="#0f172a")
    y, m = lo.year, lo.month
    while date(y, m, 1) <= hi:  # month grid, labelled every third month
        x = X(max(date(y, m, 1), lo))
        dr.line([(x, top - 30), (x, H - 90)], fill="#e2e8f0", width=1)
        if m in (1, 4, 7, 10) or (y, m) == (lo.year, lo.month):
            dr.text((x + 4, top - 52), f"{y}-{m:02d}", font=_pil_font(15), fill="#64748b")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    y0 = top
    for n, tr in enumerate(tracks):
        color = _TRACK_COLORS[n % len(_TRACK_COLORS)]
        dr.text((40, y0 + 22), tr, font=_pil_font(21, True), fill=color, anchor="lm")
        for i, c in enumerate(ch):
            if tr_of(c) != tr:
                continue
            yy = y0 + lane[i] * row
            x1, x2 = X(D(c["start_day"])), X(D(c["end_day"]) if c["end_day"] else hi)
            x2 = max(x2, x1 + 6)
            dr.rounded_rectangle([x1, yy, x2, yy + 46], radius=8, fill=color, outline="#0f172a" if c["source"] == "user" else color, width=4 if c["source"] == "user" else 1)
            if not c["end_day"]:
                dr.polygon([(x2, yy), (x2 + 18, yy + 23), (x2, yy + 46)], fill=color)
            label, f = c["title"], _pil_font(17, True)
            fits = dr.textlength(label, font=f) < (x2 - x1 - 12)
            dr.text((x1 + 8, yy + 23) if fits else (x2 + 26, yy + 23), label, font=f, fill="white" if fits else "#0f172a", anchor="lm")
        y0 += nlanes[tr] * row
    for t in tp:
        x, yy = X(D(t["day"])), H - 110
        dr.polygon([(x, yy - 12), (x + 12, yy), (x, yy + 12), (x - 12, yy)], fill="#dc2626", outline="#0f172a")
        dr.text((x, yy + 18), t["title"][:28], font=_pil_font(14), fill="#0f172a", anchor="mt")
    dr.text((40, H - 40), "bars = chapters, one row group per track (several can run at once)   |   outlined = yours   |   arrow = still open   |   red diamonds = turning points",
            font=_pil_font(16), fill="#475569")
    img.save(path)


_PNG = {"graph": _png_graph, "chapters": _png_timeline}


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


def backup(mem, out_dir, parts=None, since=None, until=None, pdf=True, blobs=True, formats=None):
    parts = list(PARTS) if parts in (None, "all") else list(parts)
    bad = [p for p in parts if p not in PARTS]
    if bad:
        raise ValueError(f"unknown part(s) {bad}; choose from {list(PARTS)}")
    formats = {p: set(f) for p, f in (formats or {}).items()}
    bad = [p for p in formats if p not in PARTS] + [f for fs in formats.values() for f in fs if f not in FORMATS]
    if bad:
        raise ValueError(f"unknown part or format in formats: {bad}; formats are {sorted(FORMATS)}")
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
                tables = None
                fm = set(formats.get(part) or DEFAULT_FORMATS[part])
                if not pdf:
                    fm.discard("pdf")
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
                    if "png" in fm and part not in _PNG:
                        notes.append(f"{part}: no picture view exists for this part, png skipped")
                    elif "png" in fm:
                        try:
                            _PNG[part](tables, d / f"{part}.png")
                        except ImportError:
                            notes.append(f"{part}.png skipped: install pillow and networkx")
                    if "pdf" in fm:
                        try:
                            if _pdf(md, d / f"{part}.pdf", f"FRED memory: {part}"):
                                notes.append(f"{part}.pdf: no Unicode font found, non-Latin characters replaced with ? (the .md is exact)")
                        except ImportError:
                            fm.discard("pdf")
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
    ap.add_argument("--format", action="append", default=[], metavar="PART=md,pdf,png", help="override a part's formats, e.g. graph=md,png,pdf")
    a = ap.parse_args()
    with Memory(a.root) as m:
        fmts = {k: v.split(",") for k, v in (f.split("=", 1) for f in a.format)}
        r = backup(m, a.out, a.parts or None, a.since, a.until, not a.no_pdf, not a.no_blobs, fmts or None)
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
        su, fred = g.resolve("person", "Priya"), g.resolve("project", "FRED")
        s.set_state(fred, "phase", "1", ts="2026-02-01T00:00:00+00:00")
        s.set_state(fred, "phase", "2", ts="2026-03-01T00:00:00+00:00")
        ev = s.add_event("note", "works on fred | at night", ts="2026-03-01T22:00:00+00:00")
        s.add_event("note", "march 9 event", ts="2026-03-09T22:00:00+00:00")
        g.tag(ev, su)
        g.relate(su, "works on", fred, ts="2026-01-01T00:00:00+00:00")
        s.set_daily_log("2026-03-01", "A quiet day.")
        from weekly import Week
        Week(m, '2026-03-08', None)  # creates the weekly tables
        from period import Month
        Month(m, "2026-03", None)  # creates the chapter tables
        for ti, st, en, tr, sc in (("FRED v2 build", "2026-02-01", None, "fred", "model"), ("Exam season", "2026-03-01", "2026-04-15", "school", "model"),
                                   ("Own earlier chapter", "2026-01-01", "2026-02-28", "general", "user")):
            s.db.execute("INSERT INTO chapters(title,start_day,end_day,description,source,updated_at,track) VALUES(?,?,?,?,?,?,?)", (ti, st, en, "", sc, "x", tr))
        s.db.execute("INSERT INTO turning_points(day,title,description,kind,period,created_at) VALUES('2026-03-01','Moved to phase 2','','project','2026-03','x')")
        g.relate(fred, "part_of", g.resolve("project", "Mark I"), ts="2026-02-01T00:00:00+00:00")
        g.relate(su, "lives_in", g.resolve("place", "Pune"), ts="2026-02-01T00:00:00+00:00")
        g.relate(su, "lives_in", g.resolve("place", "Delhi"), ts="2026-06-01T00:00:00+00:00")  # closes the Pune one: drawn dashed
        s.db.execute("INSERT INTO weekly_digests VALUES('2026-03-08','week digest','x')")
        s.db.commit()
        out = Path(d) / "backups"
        r = backup(m, out, since=None)
        got = Path(r["dir"])
        assert got.name.startswith("memory-backup-") and not got.name.endswith(".partial") and verify_backup(got) == []
        for part in ("events", "facts", "graph", "logs", "inferences", "model", "chapters", "digests", "review", "runs"):
            assert (got / part / f"{part}.json").exists() and (got / part / f"{part}.md").exists(), part
        PNG = b"\x89PNG\r\n\x1a\n"
        assert (got / "graph" / "graph.png").read_bytes()[:8] == PNG and (got / "chapters" / "chapters.png").read_bytes()[:8] == PNG   # pictures by default
        assert (got / "graph" / "graph.png").stat().st_size > 5000 and not (got / "graph" / "graph.pdf").exists()                     # ...instead of a PDF
        ct = (got / "chapters" / "chapters.md").read_text(encoding="utf-8")
        assert "## Track: fred" in ct and "## Track: school" in ct and "(yours)" not in ct and "yours" in ct
        assert (got / "database" / "memory.sqlite").exists() and (got / "archive" / "archive.md").exists()
        assert not r["notes"] or "no Unicode font" in r["notes"][0]
        if not r["notes"]:
            assert (got / "events" / "events.pdf").read_bytes()[:5] == b"%PDF-" and (got / "archive" / "archive.pdf").stat().st_size > 500
        assert (got / "archive/2026/03/01/conversation.jsonl").read_bytes() == (m.archive.root / "2026/03/01/conversation.jsonl").read_bytes()  # byte-identical
        assert any(k.startswith("archive/blobs/") for k in r["files"])                                                                  # blobs copied
        assert "FRED" in (got / "facts" / "facts.md").read_text(encoding="utf-8") and "1 (2026-02-01 to 2026-03-01)" in (got / "facts" / "facts.md").read_text(encoding="utf-8")
        assert "Priya | works_on | FRED" in (got / "graph" / "graph.md").read_text(encoding="utf-8")
        assert "(historical)" in (got / "archive" / "archive.md").read_text(encoding="utf-8")
        # a part, with a date range, no pdf, no blobs
        r2 = backup(m, out, ["events", "archive"], since="2026-03-05", pdf=False, blobs=False)
        g2 = Path(r2["dir"])
        assert g2.name.endswith("events+archive") and not list(g2.rglob("*.pdf")) and verify_backup(g2) == []
        assert "march 9 event" in (g2 / "events" / "events.md").read_text(encoding="utf-8") and "works on fred" not in (g2 / "events" / "events.md").read_text(encoding="utf-8")
        assert not (g2 / "archive/2026/03/01").exists() and (g2 / "archive/2026/03/09/conversation.jsonl").exists()
        assert not any(k.startswith("archive/blobs/") for k in r2["files"])
        # formats: override per part, pdf off still gives pictures, bad formats refused, no picture for a text part
        r4 = backup(m, out, ["graph", "events"], formats={"graph": ["md", "pdf", "png"], "events": ["md"]})
        g4 = Path(r4["dir"])
        assert (g4 / "graph" / "graph.png").exists() and (g4 / "graph" / "graph.pdf").exists() and not (g4 / "events" / "events.pdf").exists()
        r5 = backup(m, out, ["graph"], pdf=False)
        assert (Path(r5["dir"]) / "graph" / "graph.png").exists() and not list(Path(r5["dir"]).rglob("*.pdf"))
        r6 = backup(m, out, ["logs"], formats={"logs": ["md", "png"]})
        assert any("no picture view" in n for n in r6["notes"]) and not (Path(r6["dir"]) / "logs" / "logs.png").exists()
        try:
            backup(m, out, ["graph"], formats={"graph": ["gif"]})
            raise SystemExit("bad format accepted")
        except ValueError:
            pass
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
