"""email_draft: write an email, show it in Notepad (a temporary file, not a saved document), ask before sending; the yes sends exactly what is in the file, so edits made in Notepad are sent.
Add-on skill (skills/extra).

Sockets
  in : run(to="", gist="", subject="", body="", send_draft="")
         gist: what the email should say, in a line; the brain (registry.brain, the 4B) writes subject and body from it while the talker (registry.talk) speaks a short filler line (registry.say)
         subject/body given by the caller are used as they are
         to: a name ("Dad", "Rohan") or an address; names are resolved from the user's own Gmail history (most frequent match; several close ones are asked about, never guessed)
         missing to/subject/body -> ToolError bad_args naming what to ask the user for
         send_draft: set only by the yes/no follow-up (never by the model): the draft file to send
       cancel(args): the engine calls it on "no"; deletes the draft file
       bind(ctx): "registry" (its .confirmer holds the yes/no)
  out: one sentence ("Draft is open in Notepad. Send it? Yes or no?"), later "Sent to X." The file is deleted after send or cancel; leftovers older than an hour are swept on the next draft.
Files: <data dir>/email_drafts/<id>.txt   "To: addr / Subject: ... / blank line / body"
Tests set LAUNCH (fn(path)) and tools.gmail.TRANSPORT; nothing is opened or sent.
"""
import json
import os
import random
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
FILLERS = ["Sure, drafting that now.", "On it, give me a second.", "Let me write that up.", "Okay, putting it together.", "One moment, writing it."]
SCHEMA = {"type": "object", "required": ["subject", "body"], "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}, "additionalProperties": False}
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


def _filler(talk, who: str) -> str:
    """One short spoken line from the talker while the brain writes; a canned one if the talker is silent or rambles."""
    try:
        t = talk.complete([{"role": "user", "content": f"Say one short natural sentence (at most 8 words) telling the user you are drafting an email to {who}. No emojis, no quotes."}], think=False, max_tokens=24).text.strip().strip('"')
        if 3 <= len(t) <= 80 and "\n" not in t:
            return t
    except Exception:
        pass
    return random.choice(FILLERS)


def _compose(who: str, gist: str) -> tuple:
    """The brain (4B) writes the subject and body; the talker speaks first so the wait is not silent."""
    reg = _ctx["c"].get("registry")
    brain = getattr(reg, "brain", None)
    if brain is None:
        raise ToolError("unavailable", "I can't write emails right now.", "no brain bound to email_draft")
    say = getattr(reg, "say", None)
    if say:
        say(_filler(getattr(reg, "talk", None) or brain, who))
    try:
        d = brain.complete([{"role": "system", "content": "You write emails for the user, in their voice: short, natural, polite, plain text. End with a short sign-off but no name. Reply as JSON with subject and body."},
                            {"role": "user", "content": f"Write an email to {who}. What it should say: {gist}"}], schema=SCHEMA, think=False).json()
        subject, body = str(d["subject"]).strip(), str(d["body"]).strip()
    except Exception as ex:
        raise ToolError("failed", "I couldn't write that email just now.", f"compose failed: {type(ex).__name__}")
    if not subject or not body:
        raise ToolError("failed", "I couldn't write that email just now.", "empty compose")
    return subject, body


def run(to: str = "", gist: str = "", subject: str = "", body: str = "", send_draft: str = "") -> str:
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
    if not to.strip():
        raise ToolError("bad_args", "Who is the email to?", "missing: to")
    if not (body.strip() or gist.strip()):
        raise ToolError("bad_args", "What should the email say?", "missing: gist")
    addr = _resolve(to)
    if not body.strip():
        subject, body = _compose(to.strip(), gist.strip())
    elif not subject.strip():
        subject = gist.strip()[:60] or "(no subject)"
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
