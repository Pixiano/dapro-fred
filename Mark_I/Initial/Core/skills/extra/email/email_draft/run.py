"""email_draft: write an email, show it in Notepad (a temporary file, not a saved document), ask before sending; the yes sends exactly what is in the file, so edits made in Notepad are sent.
Add-on skill (skills/extra).

Sockets
  in : run(to="", subject="", body="", send_draft="")
         to: a name ("Dad", "Rohan") or an address; names are resolved from the user's own Gmail history (most frequent match; several close ones are asked about, never guessed)
         missing to/subject/body -> ToolError bad_args naming what to ask the user for
         send_draft: set only by the yes/no follow-up (never by the model): the draft file to send
       cancel(args): the engine calls it on "no"; deletes the draft file
       bind(ctx): "registry" (its .confirmer holds the yes/no)
  out: one sentence ("Draft is open in Notepad. Send it? Yes or no?"), later "Sent to X." The file is deleted after send or cancel; leftovers older than an hour are swept on the next draft.
Files: <data dir>/email_drafts/<id>.txt   "To: addr / Subject: ... / blank line / body"
Tests set LAUNCH (fn(path)) and tools.gmail.TRANSPORT; nothing is opened or sent.
"""
import os
import re
import subprocess
import time
import uuid

import config
from tools import gmail
from tools.registry import ToolError

_ctx = {"c": {}}
LAUNCH = None
_ISSUED = set()                                                       # draft files this run created: only these can be sent, so a model-invented send_draft is ignored
ADDR = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def bind(ctx):
    _ctx["c"] = ctx


def _dir():
    d = config.data_dir() / "email_drafts"
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("*.txt"):
        if time.time() - f.stat().st_mtime > 3600:
            f.unlink(missing_ok=True)
    return d


def _resolve(to: str) -> str:
    to = to.strip()
    if ADDR.match(to):
        return to
    found = gmail.recipients(to)
    if not found:
        raise ToolError("bad_args", f"I can't find an email address for {to}. What is it?", "no gmail history for that name")
    top = found[0]
    if len(found) > 1 and found[1][2] * 2 > top[2]:                          # a close second: ask
        raise ToolError("bad_args", "Which one: " + " or ".join(f"{n or a} ({a})" for n, a, _ in found[:3]) + "? Say the address.", "ambiguous recipient")
    return top[1]


def _parse(path) -> tuple:
    head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    h = dict(re.findall(r"^(To|Subject):\s*(.*)$", head, re.M))
    return h.get("To", "").strip(), h.get("Subject", "").strip(), body.strip()


def _open(path):
    if LAUNCH:
        return LAUNCH(str(path))
    subprocess.Popen(["notepad.exe", str(path)])


def run(to: str = "", subject: str = "", body: str = "", send_draft: str = "") -> str:
    if send_draft and send_draft in _ISSUED:
        f = config.data_dir() / "email_drafts" / os.path.basename(send_draft)
        if not f.exists():
            return "That draft is gone, so nothing was sent."
        addr, subj, text = _parse(f)
        if not ADDR.match(addr) or not text:
            raise ToolError("bad_args", "The draft needs a valid address and some text before I can send it.", "draft file incomplete")
        gmail.send(addr, subj or "(no subject)", text)
        f.unlink(missing_ok=True)
        _ISSUED.discard(send_draft)
        return f"Sent to {addr}."
    missing = [n for n, v in (("who it's to", to), ("what it's about", subject), ("what it should say", body)) if not v.strip()]
    if missing:
        raise ToolError("bad_args", "Before I draft it, tell me " + " and ".join(missing) + ".", "missing: " + ", ".join(missing))
    addr = _resolve(to)
    f = _dir() / f"{uuid.uuid4().hex[:10]}.txt"
    f.write_text(f"To: {addr}\nSubject: {subject.strip()}\n\n{body.strip()}\n", encoding="utf-8")
    _ISSUED.add(f.name)
    _open(f)
    conf = getattr(_ctx["c"].get("registry"), "confirmer", None)
    q = f"Draft to {addr} is open in Notepad. Send it? Yes or no?"
    return conf.ask("email_draft", {"send_draft": f.name}, q) if conf else q


def cancel(args: dict):
    _ISSUED.discard(args.get("send_draft"))
    if args.get("send_draft"):
        (config.data_dir() / "email_drafts" / os.path.basename(args["send_draft"])).unlink(missing_ok=True)
