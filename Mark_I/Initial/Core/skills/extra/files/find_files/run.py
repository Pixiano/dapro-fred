"""find_files: find (and open) a file on this PC from how the user describes it: words from the name, the kind ("the pdf", "that photo"), when ("from yesterday", "last March") and where
("in downloads"). Add-on skill (skills/extra).

Sockets
  in : run(query="", kind="any", when="", folder="", action="find", path="")
         action find  -> up to 5 best files, one line each ("name, folder, date, size"); the model words it
         action open  -> opens the clear winner with its default app; two close ones are listed to pick from, a likely one asks "Open X? Yes or no?" through the engine's yes/no slot
         action show  -> same selection, shows it in File Explorer
         `path` is set only by the yes/no follow-up (never by the model): that exact indexed file is opened
       reindex() -> int            walks the roots again (incremental: only changed files are re-read, vanished ones are dropped); called by skills/reindex.py and when the index is missing or a day old
       bind(ctx): optional "embed" (re-ranks the best candidates by meaning of the name), "registry" (its .confirmer asks the yes/no)
  out: text for the model or one spoken sentence; never file CONTENTS, only names, folders, dates and sizes.
Index: <data dir>/files_index.sqlite (outside the repo): path, name, normalised name, folder, extension, mtime, size, opens (how often FRED opened it), recent (when Windows last listed it in
  Recent). Roots = Documents, Desktop, Downloads, Pictures, Videos, Music of this user (and their OneDrive twins) unless `config.file_roots` lists others. Skipped: hidden and system folders,
  package and build folders (node_modules, venvs, .git, site-packages, __pycache__, AppData), temp and cache extensions.
Ranking: every query word must match the file name (whole word +2, word start +1.2, close spelling +1) else the file is out; then +log(opens), +0.6 if Windows lists it as recent, +0.4 when the
  folder hint matches, -1 for icons, assets and tiny images inside app folders, a little for newer files, and up to +1.5 from the embedder over the top 25. `kind` and `when` are hard filters; if they leave nothing, they are relaxed once and the answer says so.
Tests use temp roots with invented files and a fake launcher.
"""
import datetime
import difflib
import math
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path

import config
from tools.registry import ToolError
from tools.timewords import WEEKDAYS, MONTHS, parse_when, strip_when   # noqa: F401  (re-exported: the check and the skill use them)

_ctx = {"c": {}}
LAUNCH = None                                                       # tests: fn(path, how) -> None
ROOTS = None                                                        # tests: list of folders
STOP = set("the a an my that this those these file files document documents please find open show me where is of from in on for about with named called called".split())
SKIP_DIRS = {"node_modules", "__pycache__", "site-packages", "venv", ".venv", "appdata", "$recycle.bin", "system volume information", "windows", "program files", "program files (x86)", "build", "dist"}
SKIP_EXT = {".tmp", ".pyc", ".log", ".crdownload", ".part", ".ds_store", ".ini", ".db", ".sqlite-journal"}
KINDS = {
    "pdf": {".pdf"}, "document": {".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pdf"}, "spreadsheet": {".xls", ".xlsx", ".csv", ".ods"}, "presentation": {".ppt", ".pptx", ".odp", ".key"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg", ".tiff"}, "video": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"}, "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"},
    "code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".html", ".css", ".json", ".ipynb"}, "archive": {".zip", ".rar", ".7z", ".tar", ".gz"}, "installer": {".exe", ".msi"},
}
KIND_WORDS = {"photo": "image", "photos": "image", "picture": "image", "pictures": "image", "image": "image", "images": "image", "screenshot": "image", "pdf": "pdf", "pdfs": "pdf", "doc": "document", "docs": "document",
              "word": "document", "excel": "spreadsheet", "sheet": "spreadsheet", "spreadsheet": "spreadsheet", "ppt": "presentation", "slides": "presentation", "presentation": "presentation", "video": "video", "videos": "video",
              "movie": "video", "song": "audio", "songs": "audio", "music": "audio", "audio": "audio", "recording": "audio", "zip": "archive", "archive": "archive", "installer": "installer", "script": "code", "code": "code"}
FOLDER_WORDS = {"downloads": "downloads", "download": "downloads", "desktop": "desktop", "documents": "documents", "pictures": "pictures", "videos": "videos", "music": "music"}


def bind(ctx):
    _ctx["c"] = ctx


def _db_path() -> Path:
    return config.data_dir() / "files_index.sqlite"


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path())
    con.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, name TEXT, norm TEXT, folder TEXT, ext TEXT, mtime REAL, size INTEGER, opens INTEGER DEFAULT 0, recent REAL DEFAULT 0, seen REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v REAL)")
    return con


def roots() -> list:
    if ROOTS is not None:
        return [Path(r) for r in ROOTS]
    custom = getattr(config.get(), "file_roots", ())
    if custom:
        return [Path(r).expanduser() for r in custom]
    home, out = Path.home(), []
    for n in ("Documents", "Desktop", "Downloads", "Pictures", "Videos", "Music"):
        for base in (home, home / "OneDrive"):
            if (base / n).is_dir() and (base / n) not in out:
                out.append(base / n)
    return out


def norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", s).lower()))


# ---- indexing --------------------------------------------------------------------------------------------------------------------------
def _recent_targets() -> dict:
    """{path lower: time Windows last listed it as recent}; empty off Windows or without the shell COM object."""
    base = os.environ.get("APPDATA")
    if not base:
        return {}
    d = Path(base) / "Microsoft" / "Windows" / "Recent"
    out = {}
    try:
        import win32com.client
        sh = win32com.client.Dispatch("WScript.Shell")
        for p in d.glob("*.lnk"):
            try:
                t = sh.CreateShortCut(str(p)).TargetPath
                if t:
                    out[t.lower()] = p.stat().st_mtime
            except Exception:
                continue
    except Exception:
        return {}
    return out


def reindex(limit_s: float = 150.0) -> int:
    t0, now = time.time(), time.time()
    recent = _recent_targets()
    con = _connect()
    total = 0
    try:
        for root in roots():
            if not root.is_dir():
                continue
            have = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, mtime, size FROM files WHERE path LIKE ?", (str(root) + os.sep + "%",))}
            seen, rows = set(), []
            stack = [root]
            while stack and time.time() - t0 < limit_s:
                d = stack.pop()
                try:
                    it = list(os.scandir(d))
                except OSError:
                    continue
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not e.name.startswith(".") and e.name.lower() not in SKIP_DIRS:
                                stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False) and not e.name.startswith(("~$", ".")) and Path(e.name).suffix.lower() not in SKIP_EXT:
                            st = e.stat()
                            seen.add(e.path)
                            if have.get(e.path) != (st.st_mtime, st.st_size):
                                p = Path(e.path)
                                rows.append((e.path, p.name, norm(p.stem), str(p.parent), p.suffix.lower(), st.st_mtime, st.st_size, now))
                    except OSError:
                        continue
            if time.time() - t0 < limit_s:                                  # a walk cut short by the time cap must not delete what it did not reach
                gone = [(p,) for p in have if p not in seen]
                con.executemany("DELETE FROM files WHERE path=?", gone)
            con.executemany("INSERT INTO files (path, name, norm, folder, ext, mtime, size, seen) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET name=excluded.name, norm=excluded.norm, "
                            "folder=excluded.folder, ext=excluded.ext, mtime=excluded.mtime, size=excluded.size, seen=excluded.seen", rows)
        for path, ts in recent.items():
            con.execute("UPDATE files SET recent=? WHERE lower(path)=?", (ts, path))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('at', ?)", (time.time(),))
        con.commit()
        total = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    finally:
        con.close()
    return total


def _ready() -> sqlite3.Connection:
    con = _connect()
    at = con.execute("SELECT v FROM meta WHERE k='at'").fetchone()
    if not at or time.time() - at[0] > 86400:
        con.close()
        reindex()
        con = _connect()
    return con


# ---- understanding the request ---------------------------------------------------------------------------------------------------------
def words(query: str) -> list:
    return [w for w in norm(query).split() if w not in STOP and w not in KIND_WORDS and w not in FOLDER_WORDS and len(w) > 1]


def infer(query: str, kind: str, folder: str):
    """Kind and folder hints also sitting in the free words ("the invoice pdf in downloads")."""
    qw = norm(query).split()
    if kind in ("", "any"):
        kind = next((KIND_WORDS[w] for w in qw if w in KIND_WORDS), "any")
    if not folder:
        folder = next((FOLDER_WORDS[w] for w in qw if w in FOLDER_WORDS), "")
    return kind, FOLDER_WORDS.get(norm(folder), norm(folder))


def name_score(ws: list, norm_name: str) -> float | None:
    """None when any query word is missing from the name; else the match strength."""
    tokens = norm_name.split()
    total = 0.0
    for w in ws:
        if w in tokens:
            total += 2.0
        elif any(t.startswith(w) or (len(w) >= 4 and w in t) for t in tokens):
            total += 1.2
        elif any(difflib.SequenceMatcher(None, w, t).ratio() >= 0.8 for t in tokens if abs(len(t) - len(w)) <= 2):
            total += 1.0
        else:
            return None
    return total


ASSETY = re.compile(r"[\\/](icons?|assets?|static|public|drawable|res|sidepanel|toolbar|thumbnails?|sprites?|fonts)([\\/]|$)", re.I)      # app and web-project internals: rarely what "that photo" means


def rank(rows: list, ws: list, folder: str, now: float | None = None) -> list:
    """[(score, row)] best first; row = dict from the files table."""
    now = now or time.time()
    out = []
    for r in rows:
        s = name_score(ws, r["norm"]) if ws else 0.0
        if s is None:
            continue
        s += math.log1p(r["opens"] or 0) + (0.6 if r["recent"] else 0.0)
        if folder and folder in r["folder"].lower():
            s += 0.4
        s += 0.3 * math.exp(-(now - r["mtime"]) / (86400 * 90))
        if ASSETY.search(r["folder"]) or (r["ext"] in KINDS["image"] and r["size"] < 3000):
            s -= 1.0
        out.append((s, r))
    return sorted(out, key=lambda x: (-x[0], -x[1]["mtime"]))


def _rows(con, kind: str, span) -> list:
    q, args = "SELECT path, name, norm, folder, ext, mtime, size, opens, recent FROM files", []
    cond = []
    if kind in KINDS:
        cond.append("ext IN (%s)" % ",".join("?" * len(KINDS[kind])))
        args += sorted(KINDS[kind])
    if span:
        cond.append("mtime >= ? AND mtime < ?")
        args += [span[0], span[1]]
    cur = con.execute(q + (" WHERE " + " AND ".join(cond) if cond else ""), args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def _semantic(ws: list, ranked: list) -> list:
    emb = _ctx["c"].get("embed")
    if not emb or not ws or len(ranked) < 2:
        return ranked
    try:
        top = ranked[:25]
        v = emb([" ".join(ws)] + [r["norm"] or r["name"] for _, r in top])
        unit = lambda a: [x / (math.sqrt(sum(y * y for y in a)) or 1.0) for x in a]
        q = unit(v[0])
        out = []
        for (s, r), vec in zip(top, v[1:]):
            c = sum(a * b for a, b in zip(q, unit(vec)))
            out.append((s + 1.5 * max(0.0, (c - 0.6) / 0.4), r))
        return sorted(out, key=lambda x: (-x[0], -x[1]["mtime"])) + ranked[25:]
    except Exception:
        return ranked


# ---- the skill -------------------------------------------------------------------------------------------------------------------------
def _size(n: int) -> str:
    for unit, k in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= k:
            return f"{n / k:.1f} {unit}"
    return f"{n} B"


def _line(r: dict) -> str:
    d = datetime.datetime.fromtimestamp(r["mtime"]).strftime("%d %b %Y")
    return f"{r['name']} ({Path(r['folder']).name}, {d}, {_size(r['size'])})"


RUNNABLE = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".scr", ".com", ".lnk", ".jar", ".reg", ".hta", ".cpl", ".msc"}      # opening one of these runs code: always a yes first


_ASKED: set = set()                                                       # programs a yes was asked for: the model cannot reach the follow-up with a path of its own


def _launch(path: str, how: str):
    if LAUNCH:
        return LAUNCH(path, how)
    try:
        if how == "show":
            subprocess.Popen(["explorer.exe", "/select,", path])
        else:
            os.startfile(path)
    except OSError as ex:
        raise ToolError("failed", "I couldn't open that file.", f"open failed: {ex!r}")


def _bump(con, path: str):
    con.execute("UPDATE files SET opens = opens + 1 WHERE path=?", (path,))
    con.commit()


def run(query: str = "", kind: str = "any", when: str = "", folder: str = "", action: str = "find", path: str = "", reason: str = "") -> str:
    if action in ("open", "show") and path:                          # the yes/no follow-up: that exact file
        if action == "open" and Path(path).suffix.lower() in RUNNABLE and path not in _ASKED:
            raise ToolError("denied", "I need your yes before I run a program.", "runnable path given without a confirmation")
        _ASKED.discard(path)
        con = _ready()
        try:
            hit = con.execute("SELECT path FROM files WHERE path=?", (path,)).fetchone()
            if not hit or not os.path.exists(path):
                return "That file isn't there any more."
            _launch(path, action)
            _bump(con, path)
            return f"{'Showing' if action == 'show' else 'Opening'} {Path(path).name}."
        finally:
            con.close()
    kind, folder = infer(query, kind, folder)
    ws = words(strip_when(query))
    span = parse_when(when) or parse_when(query)
    if not ws and kind == "any" and not span and not folder:
        raise ToolError("bad_args", "What should I look for?", "give some words from the name, a kind of file, or a time")
    con = _ready()
    try:
        relaxed = ""
        ranked = rank(_rows(con, kind, span), ws, folder)
        if not ranked and (kind != "any" or span):
            ranked = rank(_rows(con, "any", None), ws, folder)
            relaxed = " (nothing matched the type or date you gave, so I ignored them)"
        ranked = _semantic(ws, ranked)
        if not ranked:
            return "I couldn't find a file like that." + (" The file index may be out of date; it refreshes daily." if _stale(con) else "")
        top = ranked[:5]
        if action == "find":
            return f"Best matches{relaxed}: " + "; ".join(f"{i}. {_line(r)}" for i, (_, r) in enumerate(top, 1)) + "."
        clear = len(top) == 1 or top[0][0] - top[1][0] >= 1.0
        conf = getattr(_ctx["c"].get("registry"), "confirmer", None)
        best = top[0][1]
        runs = action == "open" and Path(best["path"]).suffix.lower() in RUNNABLE
        if runs and not reason.strip():
            raise ToolError("bad_args", "Why should I run it?", f"{best['name']} is a program: give a short reason (what the user wants it for) in `reason`")
        if clear and not runs and (top[0][0] >= 2.0 * max(len(ws), 1) or not ws):
            _launch(best["path"], action)
            _bump(con, best["path"])
            return f"{'Showing' if action == 'show' else 'Opening'} {best['name']}."
        if clear and conf is not None:
            runs and _ASKED.add(best["path"])
            return conf.ask("find_files", {"action": action, "path": best["path"]}, f"I found {_line(best)}. {'Show' if action == 'show' else 'Open'} it?{' This program will run, to ' + reason.strip().rstrip('.') + '.' if runs else ''} Yes or no?")
        if runs:
            return f"That is a program, so I won't run it without a yes: {_line(best)}."
        return "Which one: " + "; ".join(f"{i}. {_line(r)}" for i, (_, r) in enumerate(top[:3], 1)) + "? Say more of the name."
    finally:
        con.close()


def _stale(con) -> bool:
    at = con.execute("SELECT v FROM meta WHERE k='at'").fetchone()
    return not at or time.time() - at[0] > 3600
