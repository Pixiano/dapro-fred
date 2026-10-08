"""Tools: open an application (from an index of what is installed), a website, or a file/folder.

Sockets
  in : register(registry) adds launch_application, open_website, open_path
       AppIndex(path=None).match(name) -> (name, appid) | None ; .refresh() ; .names()      index file <data_dir>/app_index.json from `Get-StartApps` (Win32 and Store apps alike)
  out: "Opening X." (the tool cannot see the window appear, so it never says "launched"); an unknown app is not_found, naming the closest candidates
Matching: exact name, then name starting with the words, then substring (shortest wins: "code" -> Visual Studio Code, not a longer name), then difflib >= 0.75; the index is refreshed
once when a name misses (a just-installed app) and at most weekly otherwise. Launching goes through the shell's own AppsFolder (explorer shell:AppsFolder\\<AppID>), never a guessed path.
Websites: http/https only (a bare "google.com" gets https://); javascript:, file: and credentials-in-URL are refused. open_path refuses programs and scripts (.exe .bat .cmd .ps1 .msi .vbs .js
.lnk ...): applications go through launch_application; the private data folder is closed (tools/files.py guard).
Self-check: python tools/apps.py (index logic on a fixed app list; nothing is launched)
"""
import difflib
import json
import os
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from tools.files import _guard
from tools.registry import ToolError, ToolSpec

MAX_AGE_S = 7 * 86400
BLOCKED_EXT = {".exe", ".bat", ".cmd", ".ps1", ".msi", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".lnk", ".scr", ".com", ".reg", ".hta", ".jar", ".dll", ".msc"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def best(name: str, apps: list):
    """apps: [{name, appid}]. Returns the best entry or None. Pure."""
    q = _norm(name)
    if not q:
        return None
    named = [(_norm(a["name"]), a) for a in apps]
    for pick in (lambda n: n == q, lambda n: n.startswith(q) or q.startswith(n) and len(n) > 2, lambda n: q in n or (len(n) > 2 and n in q)):
        hits = sorted((a for n, a in named if pick(n)), key=lambda a: len(a["name"]))
        if hits:
            return hits[0]
    close = difflib.get_close_matches(q, [n for n, _ in named], n=1, cutoff=0.75)
    return next((a for n, a in named if n == close[0]), None) if close else None


class AppIndex:
    def __init__(self, path=None):
        self.path = Path(path) if path else config.data_dir() / "app_index.json"
        self._apps = None

    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if time.time() - d["ts"] < MAX_AGE_S and d["apps"]:
                return d["apps"]
        except (OSError, ValueError, KeyError):
            pass
        return None

    def refresh(self) -> list:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"], capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            raw = json.loads(out)
            apps = [{"name": a["Name"], "appid": a["AppID"]} for a in (raw if isinstance(raw, list) else [raw]) if a.get("Name") and a.get("AppID")]
        except Exception as ex:
            raise ToolError("unavailable", "I can't list the installed apps right now.", f"Get-StartApps: {ex!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"ts": time.time(), "apps": apps}), encoding="utf-8")
        self._apps = apps
        return apps

    def apps(self) -> list:
        self._apps = self._apps or self._load() or self.refresh()
        return self._apps

    def match(self, name: str):
        hit = best(name, self.apps())
        if hit is None:                                                             # maybe installed since the index was made
            hit = best(name, self.refresh())
        return (hit["name"], hit["appid"]) if hit else None

    def names(self) -> list:
        return [a["name"] for a in self.apps()]


def launch_application(app_name: str) -> str:
    idx = AppIndex()
    hit = idx.match(app_name)
    if hit is None:
        close = difflib.get_close_matches(_norm(app_name), [_norm(n) for n in idx.names()], n=3, cutoff=0.4)
        raise ToolError("not_found", f"I can't find an app called {app_name}.", f"no installed app matches {app_name!r}; closest: {close}")
    name, appid = hit
    try:
        if "://" in appid:
            os.startfile(appid)
        else:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{appid}"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as ex:
        raise ToolError("failed", f"I couldn't open {name}.", f"launch {appid!r}: {ex!r}")
    return f"Opening {name}."


def clean_url(url: str) -> str:
    u = url.strip().strip("<>\"'")
    if re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I) and not re.match(r"^https?://", u, re.I):
        if not re.match(r"^[\w.-]+:\d+", u):                                         # "localhost:8080" has a colon but is a host
            raise ToolError("denied", "I only open web addresses.", f"scheme not allowed: {u[:40]!r}")
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    host = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0]
    if "@" in host or not re.search(r"[\w-]+(\.[\w-]+)+|localhost", host.split(":")[0]):
        raise ToolError("bad_args", "That doesn't look like a web address.", f"unusable url {url!r}")
    return u


def open_website(url: str) -> str:
    u = clean_url(url)
    if not webbrowser.open(u):
        raise ToolError("failed", "I couldn't open the browser.", f"webbrowser.open({u!r}) returned False")
    return f"Opening {u.split('//', 1)[1].split('/')[0]}."


def open_path(path: str) -> str:
    p = _guard(path)
    if not p.exists():
        raise ToolError("not_found", "I can't find that.", f"no such path {p}")
    if p.is_file() and p.suffix.lower() in BLOCKED_EXT:
        raise ToolError("denied", "I won't run programs or scripts from here; I can open an app by name instead.", f"blocked extension {p.suffix}")
    os.startfile(str(p))
    return f"Opening {p.name}."


def register(reg):
    reg.register(ToolSpec("launch_application", launch_application, {"type": "object", "required": ["app_name"], "properties": {"app_name": {"type": "string", "description": "the app's name, e.g. Spotify"}}},
                          "Open an installed application by name.", group="apps", say=True,
                          cues=(r"open", r"launch", r"start", r"fire up", r"run"), examples=("open spotify", "start vs code", "fire up the browser")))
    reg.register(ToolSpec("open_website", open_website, {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}}, "Open a web address in the browser.", group="apps", say=True,
                          cues=(r"go to", r"open (?:the )?(?:website|site|url|link)", r"take me to", r"\b[\w-]+\.(?:com|org|net|io|in|co|dev|ai)\b", r"dot com"), examples=("go to youtube", "open google dot com", "take me to that website")))
    reg.register(ToolSpec("open_path", open_path, {"type": "object", "required": ["path"], "properties": {"path": {"type": "string", "format": "path"}}}, "Open a file or folder with its default program.", group="apps", say=True,
                          cues=(r"open (?:the |my |that |this )?(?:file|folder|directory|downloads|documents|desktop|pictures)", r"show me (?:the |my |this )?(?:folder|file)"), examples=("open my downloads folder", "open that file")))


def _selfcheck():
    import tempfile
    apps = [{"name": "Spotify", "appid": "Spotify.exe"}, {"name": "Visual Studio Code", "appid": "vsc"}, {"name": "Visual Studio Installer", "appid": "vsi"}, {"name": "Google Chrome", "appid": "chrome"},
            {"name": "Notepad", "appid": "notepad"}, {"name": "Microsoft Edge", "appid": "edge"}]
    pick = lambda q: (best(q, apps) or {}).get("name")
    assert pick("spotify") == "Spotify" and pick("code") == "Visual Studio Code" and pick("visual studio") == "Visual Studio Code" and pick("chrome") == "Google Chrome"
    assert pick("notepd") == "Notepad" and pick("quantum photon browser") is None and pick("") is None and pick("edge") == "Microsoft Edge"
    for u, ok in (("google.com", "https://google.com"), ("https://a.b/c?d=1", "https://a.b/c?d=1"), ("localhost:8080", "https://localhost:8080"), ("<youtube.com/watch?v=1>", "https://youtube.com/watch?v=1")):
        assert clean_url(u) == ok, u
    for bad in ("javascript:alert(1)", "file:///C:/Windows/system32", "https://user:pw@evil.com", "hello", "ms-settings:network"):
        try:
            clean_url(bad)
            raise AssertionError(bad)
        except ToolError:
            pass
    with tempfile.TemporaryDirectory() as t:
        real = config.data_dir()
        config.set_data_dir(t)
        (Path(t) / "x.exe").write_text("x")
        idx = AppIndex()
        idx.path.write_text(json.dumps({"ts": time.time(), "apps": apps}), encoding="utf-8")
        assert idx.match("chrome") == ("Google Chrome", "chrome") and idx.names()[0] == "Spotify"
        idx.path.write_text(json.dumps({"ts": time.time() - 9 * 86400, "apps": apps}), encoding="utf-8")
        assert AppIndex()._load() is None                                              # a stale index is rebuilt
        home = Path.home() / "fred_apps_selfcheck.exe"
        home.write_text("x")
        try:
            from tools.registry import Registry
            r = Registry()
            register(r)
            assert r.call("open_path", {"path": str(home)}).error_kind == "denied" and r.call("open_path", {"path": "~/no_such_file_zz.txt"}).error_kind == "not_found"
            assert r.call("open_website", {"url": "javascript:x"}).error_kind == "denied"
        finally:
            home.unlink()
        config.set_data_dir(real)
    print("apps ok")


if __name__ == "__main__":
    _selfcheck()
