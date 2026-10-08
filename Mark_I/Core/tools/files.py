"""Tools: read, list, create, overwrite, delete and find files, with bounds and guards.

Sockets
  in : register(registry) adds read_text_file, list_directory, write_file, overwrite_file (destructive), delete_file (destructive), find_file_smart (isolated child process)
  out: strings for the model (file text is capped, untrusted: `reads_untrusted`); failures are ToolError(not_found | denied | bad_args)
Guards: relative paths resolve against the home folder; FRED's private data folder (config.data_dir(): vault, memory, voice data) is never readable or writable by a tool;
system folders are not writable; write_file refuses to replace an existing file (that is overwrite_file, which asks first); delete_file does not erase, it moves the file to
<data_dir>/trash/ (reversible). find_file_smart is a bounded name search (depth, result count and wall-clock limits) in a child process killed on timeout, so a huge drive cannot hang a turn.
Self-check: python tools/files.py
"""
import os
import shutil
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from tools.registry import ToolError, ToolSpec

READ_CAP = 200_000
SYSTEM = [Path(os.environ.get(k, d)).resolve() for k, d in (("SystemRoot", "C:/Windows"), ("ProgramFiles", "C:/Program Files"), ("ProgramFiles(x86)", "C:/Program Files (x86)"))]


def _p(path: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(str(path))))
    return (p if p.is_absolute() else Path.home() / p).resolve()


def _inside(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def _guard(path: str, write: bool = False) -> Path:
    p = _p(path)
    if _inside(p, config.data_dir().resolve()):
        raise ToolError("denied", "That's FRED's private folder; I don't open it from here.", f"{p} is inside the private data folder")
    if write and any(_inside(p, s) for s in SYSTEM):
        raise ToolError("denied", "I won't change system folders.", f"{p} is a system folder")
    return p


def read_text_file(path: str, max_chars: int = 4000) -> str:
    p = _guard(path)
    if not p.is_file():
        raise ToolError("not_found", "I can't find that file.", f"no file at {p}")
    if p.stat().st_size > READ_CAP * 5:
        raise ToolError("bad_args", "That file is too big to read aloud.", f"{p.stat().st_size} bytes")
    text = p.read_bytes()[:READ_CAP].decode("utf-8", "replace")
    return text[:max_chars] + (f"\n[... {len(text) - max_chars} more characters]" if len(text) > max_chars else "")


def list_directory(path: str = "~", limit: int = 50) -> str:
    p = _guard(path)
    if not p.is_dir():
        raise ToolError("not_found", "I can't find that folder.", f"no folder at {p}")
    items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    shown = [f"{x.name}{'/' if x.is_dir() else ''}" for x in items[:limit]]
    return "\n".join(shown) + (f"\n[... {len(items) - limit} more]" if len(items) > limit else "") if shown else "(empty)"


def write_file(path: str, content: str) -> str:
    p = _guard(path, write=True)
    if p.exists():
        raise ToolError("denied", "That file already exists; I won't replace it unless you say so.", f"{p} exists; overwrite_file replaces it")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Created {p.name}."


def overwrite_file(path: str, content: str) -> str:
    p = _guard(path, write=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {p.name}."


def delete_file(path: str) -> str:
    p = _guard(path, write=True)
    if not p.is_file():
        raise ToolError("not_found", "I can't find that file.", f"no file at {p}")
    t = config.data_dir() / "trash"
    t.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(t / f"{time.strftime('%Y%m%d_%H%M%S')}_{p.name}"))
    return f"Deleted {p.name}; it's in FRED's trash if you need it back."


def _find(name: str, root: str, depth: int, limit: int, secs: float) -> str:
    """Module-level and picklable: runs in a child process the registry kills on timeout."""
    needle, base, end, out = name.lower(), Path(root), time.monotonic() + secs, []
    skip = {"node_modules", ".git", "__pycache__", "AppData", "$Recycle.Bin", "Windows"}
    for dp, dns, fns in os.walk(base):
        if time.monotonic() > end or len(out) >= limit:
            break
        dns[:] = [d for d in dns if d not in skip and not d.startswith(".")]
        if len(Path(dp).relative_to(base).parts) >= depth:
            dns[:] = []
        out += [str(Path(dp) / f) for f in fns + dns if needle in f.lower()][: limit - len(out)]
    return "\n".join(out) if out else "(no match)"


def find_files(name: str, root: str = "~", depth: int = 6) -> str:
    r = _guard(root)
    if not r.is_dir():
        raise ToolError("not_found", "I can't find that folder.", f"no folder at {r}")
    return _find(name, str(r), min(depth, 8), 50, 8.0)


def _find_isolated(name: str, root: str = "~", depth: int = 6) -> str:
    """Picklable entry for the isolated child (the guard runs in the child too: it only needs config defaults)."""
    return find_files(name, root, depth)


def register(reg):
    P = {"path": {"type": "string", "format": "path"}}
    reg.register(ToolSpec("read_text_file", read_text_file, {"type": "object", "required": ["path"], "properties": {**P, "max_chars": {"type": "integer", "minimum": 100, "maximum": 20000, "default": 4000}}},
                          "Read a text file and return its content (capped).", group="files", reads_untrusted=True,
                          cues=(r"read (?:the |my |this )?(?:file|note)", r"open (?:the )?file", r"what(?:'s| is) in (?:the |my )?file", r"\.(?:txt|md|json|csv|py)\b"), examples=("read that file", "what's in my notes file")))
    reg.register(ToolSpec("list_directory", list_directory, {"type": "object", "properties": {**P, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}},
                          "List the files and folders in a directory.", group="files", cues=(r"list (?:the )?(?:files|folder|directory)", r"what(?:'s| is) in (?:the |my )?(?:folder|directory|downloads|documents|desktop)"),
                          examples=("show me my downloads folder", "what's in this folder")))
    reg.register(ToolSpec("write_file", write_file, {"type": "object", "required": ["path", "content"], "properties": {**P, "content": {"type": "string"}}},
                          "Create a NEW text file with the given content. Fails if the file exists.", group="files", cues=(r"(?:create|make|write|save)(?: a| the| new)? (?:file|note)",), examples=("create a file called todo", "save this to a note")))
    reg.register(ToolSpec("overwrite_file", overwrite_file, {"type": "object", "required": ["path", "content"], "properties": {**P, "content": {"type": "string"}}},
                          "Replace the content of an existing file (asks the user first).", group="files", destructive=True, cues=(r"overwrite", r"replace the (?:file|content)"), examples=("overwrite that file",)))
    reg.register(ToolSpec("delete_file", delete_file, {"type": "object", "required": ["path"], "properties": P},
                          "Delete a file (moved to FRED's trash; asks the user first).", group="files", destructive=True, cues=(r"delete", r"remove (?:the |this )?file", r"get rid of"), examples=("delete that file", "remove the old draft")))
    reg.register(ToolSpec("find_file_smart", _find_isolated, {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, **{"root": {"type": "string", "format": "path", "default": "~"}}, "depth": {"type": "integer", "minimum": 1, "maximum": 8, "default": 6}}},
                          "Find files and folders whose name contains the text, searching under a folder (home by default).", group="files", isolate=True, timeout_s=12, reads_untrusted=False,
                          cues=(r"find (?:the |my |a |that |this )?[\w' -]+", r"search for", r"look for", r"locate", r"where(?:'s| is) (?:the |my )", r"look for (?:the |a )?file", r"search for (?:the |a )?file"), examples=("find my budget spreadsheet", "where is the report")))


def _selfcheck():
    import tempfile
    from tools.registry import Registry
    real = config.data_dir()
    with tempfile.TemporaryDirectory() as priv, tempfile.TemporaryDirectory() as work:
        config.set_data_dir(priv)
        (Path(priv) / "secret.md").write_text("private", encoding="utf-8")
        r = Registry()
        register(r)
        w = Path(work)
        assert r.call("write_file", {"path": str(w / "a.txt"), "content": "hello"}).data == "Created a.txt."
        assert r.call("write_file", {"path": str(w / "a.txt"), "content": "x"}).error_kind == "denied"             # never replaces silently
        assert r.call("overwrite_file", {"path": str(w / "a.txt"), "content": "bye"}).ok and (w / "a.txt").read_text() == "bye"
        assert r.call("read_text_file", {"path": str(w / "a.txt")}).data == "bye"
        assert r.call("read_text_file", {"path": str(w / "none.txt")}).error_kind == "not_found"
        assert r.call("read_text_file", {"path": str(Path(priv) / "secret.md")}).error_kind == "denied"             # the private folder is closed
        assert r.call("write_file", {"path": str(Path(priv) / "x.txt"), "content": "x"}).error_kind == "denied"
        assert r.call("write_file", {"path": "C:/Windows/x.txt", "content": "x"}).error_kind == "denied"
        (w / "big.txt").write_text("y" * 9000, encoding="utf-8")
        t = r.call("read_text_file", {"path": str(w / "big.txt"), "max_chars": 100}).data
        assert t.startswith("y" * 100) and "more characters" in t
        (w / "sub" / "deep").mkdir(parents=True)
        (w / "sub" / "deep" / "Budget_2026.xlsx").write_text("x")
        assert "Budget_2026.xlsx" in r.call("find_file_smart", {"name": "budget", "root": str(w)}).data
        assert r.call("find_file_smart", {"name": "zzz", "root": str(w)}).data == "(no match)"
        assert r.call("find_file_smart", {"name": "budget", "root": str(w), "depth": 1}).data == "(no match)"             # depth bound
        assert "a.txt" in r.call("list_directory", {"path": str(w)}).data and "sub/" in r.call("list_directory", {"path": str(w)}).data
        d = r.call("delete_file", {"path": str(w / "a.txt")})
        assert d.ok and not (w / "a.txt").exists() and any(Path(priv, "trash").iterdir())                          # moved, recoverable
        assert r.call("delete_file", {"path": str(w / "a.txt")}).error_kind == "not_found"
        assert {s.name for s in map(r.spec, r.names()) if s.destructive} == {"overwrite_file", "delete_file"}
        config.set_data_dir(real)
    print("files ok")


if __name__ == "__main__":
    _selfcheck()
