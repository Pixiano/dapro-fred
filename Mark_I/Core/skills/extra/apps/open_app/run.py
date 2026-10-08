"""open_app: open an installed Windows app from how the user names it ("open spotify", "start the browser", "vs code"). Add-on skill (skills/extra).

Sockets
  in : run(app, exact=False)          `exact` is set only by the yes/no follow-up (never by the model): the name is then an index entry, taken as is
       reindex() -> int               rescans the installed apps; called by skills/reindex.py (the FRED reindex) and whenever the index is missing or a week old
       bind(ctx): optional "embed" (callable texts -> vectors) for matching by meaning, "registry" (its .confirmer asks the yes/no)
  out: one spoken sentence. A confident match opens at once ("Opening Spotify."); a near match or two close ones ask "Did you mean X? Yes or no?" through the engine's yes/no slot and the
       yes opens it; nothing plausible says so. Never guesses silently between different apps.
Index: <data dir>/apps_index.json = every Start Menu shortcut plus every Store/UWP app (Get-StartApps), minus uninstallers, readmes and help links. Usage counts in apps_usage.json break ties
  and favour what the user actually opens.
Matching: normalised names; score = best of string similarity, shared words and embedding similarity, +0.05 per doubling of past opens (max 0.15); a small alias table covers generic words
  (browser, editor, terminal, files). Confident = best >= 0.80 and leads the runner-up by 0.06; ask = best >= 0.55; else none. Meaning alone (embeddings) can only ask.
Tests replace `LAUNCH` and call `scan(folders, uwp)` with temp folders; nothing real is opened.
"""
import difflib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import config
from tools.registry import ToolError

_ctx = {"c": {}}
LAUNCH = None                                                        # tests: fn(entry) -> None
SKIP = re.compile(r"\b(uninstall|readme|read me|help|documentation|manual|website|license|release notes|changelog|support|report|about|eula|survey|feedback)\b", re.I)
ALIASES = {"browser": ["chrome", "edge", "firefox", "brave", "opera"], "web browser": ["chrome", "edge", "firefox", "brave"], "editor": ["code", "notepad", "sublime"], "text editor": ["notepad", "code"],
           "terminal": ["terminal", "powershell", "command prompt"], "command line": ["terminal", "command prompt", "powershell"], "music": ["spotify", "music"], "calculator": ["calculator"]}
FILLER = set("the a an my app application program please open launch start run up fire".split())
CONFIDENT, MARGIN, ASK = 0.80, 0.06, 0.55


def bind(ctx):
    _ctx["c"] = ctx


def _index_path() -> Path:
    return config.data_dir() / "apps_index.json"


def _usage_path() -> Path:
    return config.data_dir() / "apps_usage.json"


def norm(s: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z0-9+#.]+", s.lower()) if w not in FILLER)


# ---- the index -------------------------------------------------------------------------------------------------------------------------
def _start_menu_dirs() -> list:
    out = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return out


def _uwp() -> list:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"], capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = json.loads(r.stdout or "[]")
        return [rows] if isinstance(rows, dict) else rows
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def scan(folders=None, uwp=None) -> list:
    """[{name, kind: lnk|uwp, target}] one per distinct app name, shortcuts first."""
    found = {}
    for d in (folders if folders is not None else _start_menu_dirs()):
        for p in Path(d).rglob("*.lnk"):
            name = p.stem.strip()
            if name and not SKIP.search(name) and norm(name) not in found:
                found[norm(name)] = {"name": name, "kind": "lnk", "target": str(p)}
    for row in (uwp if uwp is not None else _uwp()):
        name, app_id = (row.get("Name") or "").strip(), row.get("AppID") or ""
        if name and app_id and not SKIP.search(name) and norm(name) not in found:
            found[norm(name)] = {"name": name, "kind": "uwp", "target": app_id}
    return sorted(found.values(), key=lambda e: e["name"].lower())


def reindex() -> int:
    entries = scan()
    if entries:                                                      # a failed scan never wipes a good index
        _index_path().write_text(json.dumps({"at": time.time(), "apps": entries}), encoding="utf-8")
    return len(entries)


def _load() -> list:
    try:
        d = json.loads(_index_path().read_text(encoding="utf-8"))
        if time.time() - d["at"] < 7 * 86400:
            return d["apps"]
    except (OSError, ValueError, KeyError):
        pass
    reindex()
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))["apps"]
    except (OSError, ValueError, KeyError):
        return []


def _usage() -> dict:
    try:
        return json.loads(_usage_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _bump(name: str):
    u = _usage()
    u[name] = u.get(name, 0) + 1
    try:
        _usage_path().write_text(json.dumps(u), encoding="utf-8")
    except OSError:
        pass                                                         # usage is only a tie-break


# ---- matching (pure) -------------------------------------------------------------------------------------------------------------------
def _abbrev(qw: list, nw: list) -> bool:
    """'vs code' for 'visual studio code', 'ms word' for 'microsoft word': each query word is a name word or the initials of the next few, and at least one is initials."""
    i, short = 0, False
    for w in qw:
        if i < len(nw) and w == nw[i]:
            i += 1
        elif len(w) >= 2 and i + len(w) <= len(nw) and w == "".join(x[0] for x in nw[i:i + len(w)]):
            i, short = i + len(w), True
        else:
            return False
    return short


def _sim(query: str, name: str) -> float:
    q, n = norm(query), norm(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    qw, nw = set(q.split()), set(n.split())
    inter = len(qw & nw)
    extra = min(0.2, 0.07 * (len(nw) - len(qw & nw)))                                              # "chrome": Google Chrome beats Chrome Remote Desktop (fewer words the user did not say)
    word = inter / max(len(qw), 1) * (0.9 if inter == len(qw) else 0.7) - extra if inter else 0.0   # every query word present in the name: nearly exact
    if q in n or n in q:
        word = max(word, (0.85 - extra) if len(q) >= 3 else 0.0)
    if _abbrev(q.split(), n.split()):
        word = max(word, 0.88)
    return max(word, difflib.SequenceMatcher(None, q, n).ratio() * 0.78)               # spelling alone never reaches "confident": a mishearing asks


def rank(query: str, entries: list, usage=None, sims=None) -> list:
    """[(score, entry)] best first. `sims` = {name: embedding similarity in 0..1}, optional."""
    usage, sims = usage or {}, sims or {}
    q = norm(query)
    pool = []
    for e in entries:
        s = _sim(query, e["name"])
        for alias in ALIASES.get(q, []):
            if alias in norm(e["name"]):
                s = max(s, 0.82 - min(0.2, 0.07 * (len(norm(e["name"]).split()) - len(alias.split()))))
        s = max(s, sims.get(e["name"], 0.0))
        n = usage.get(e["name"], 0)
        if n and s >= ASK:
            s += min(0.15, 0.05 * n.bit_length())
        pool.append((min(s, 1.0), e))
    return sorted(pool, key=lambda x: (-x[0], x[1]["name"].lower()))


def decide(ranked: list):
    """("open", entry) | ("ask", entry) | ("pick", [entries]) | ("none",)"""
    if not ranked or ranked[0][0] < ASK:
        return ("none",)
    top = ranked[0]
    close = [e for s, e in ranked if top[0] - s < MARGIN and s >= ASK]
    if len(close) > 1 and top[0] < 0.97:
        return ("pick", close[:3])
    return ("open", top[1]) if top[0] >= CONFIDENT else ("ask", top[1])


_NAMEVEC: dict = {}                                                                # app name -> unit vector, embedded once per run


def _sims(query: str, entries: list) -> dict:
    """Meaning match from the embedder, 0 below cosine 0.80 (measured on a real 220-app list: unrelated names reach 0.78, true matches 0.81 to 0.94), capped at 0.74 so that meaning
    alone only ever ASKS ("a music app" -> "Did you mean Spotify?"), never opens."""
    emb = _ctx["c"].get("embed")
    q = norm(query)
    if not emb or not q:
        return {}
    try:
        import math
        unit = lambda a: [x / (math.sqrt(sum(y * y for y in a)) or 1.0) for x in a]
        todo = [e["name"] for e in entries if e["name"] not in _NAMEVEC]
        if todo:
            for nm, v in zip(todo, emb(todo)):
                _NAMEVEC[nm] = unit(v)
        qv = unit(emb([q])[0])
        out = {}
        for e in entries:
            c = sum(a * b for a, b in zip(qv, _NAMEVEC[e["name"]]))
            out[e["name"]] = min(0.74, 0.55 + (c - 0.80) / 0.14 * 0.19) if c >= 0.80 else 0.0
        return out
    except Exception:
        return {}


# ---- the skill -------------------------------------------------------------------------------------------------------------------------
def _open(e: dict) -> None:
    if LAUNCH:
        return LAUNCH(e)
    try:
        if e["kind"] == "lnk":
            os.startfile(e["target"])
        else:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + e["target"]], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as ex:
        raise ToolError("failed", f"I couldn't open {e['name']}.", f"launch failed: {ex!r}")


def run(app: str, exact: bool = False) -> str:
    q = (app or "").strip()
    if not norm(q):
        raise ToolError("bad_args", "Which app?", "app name is empty")
    if norm(q) in ("file explorer", "explorer", "files", "my files", "this pc"):
        _open({"name": "File Explorer", "kind": "lnk", "target": "explorer.exe"}) if not LAUNCH else LAUNCH({"name": "File Explorer", "kind": "lnk", "target": "explorer.exe"})
        return "Opening File Explorer."
    entries = _load()
    if not entries:
        raise ToolError("unavailable", "I can't see the installed apps right now.", "app index is empty")
    if exact:
        hit = next((e for e in entries if e["name"] == q), None)
        if not hit:
            return f"I can't find {q} any more."
        _open(hit)
        _bump(hit["name"])
        return f"Opening {hit['name']}."
    verdict = decide(rank(q, entries, _usage(), _sims(q, entries)))
    conf = getattr(_ctx["c"].get("registry"), "confirmer", None)
    if verdict[0] == "open":
        _open(verdict[1])
        _bump(verdict[1]["name"])
        return f"Opening {verdict[1]['name']}."
    if verdict[0] == "ask":
        if conf is None:
            return f"I'm not sure; did you mean {verdict[1]['name']}? Ask for it by that name."
        return conf.ask("open_app", {"app": verdict[1]["name"], "exact": True}, f"I'm not sure which app you mean. Did you mean {verdict[1]['name']}? Yes or no?")
    if verdict[0] == "pick":
        names = [e["name"] for e in verdict[1]]
        return f"Which one: {', '.join(names[:-1])} or {names[-1]}? Say the name."
    return f"I can't find an app called {q}."
