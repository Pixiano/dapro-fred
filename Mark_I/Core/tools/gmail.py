"""Gmail for FRED's email skills (email_draft, email_send, email_read): a thin client over the Gmail REST API, nothing else.

Sockets
  in : search(query, limit=5) -> [Mail]             newest first; each Mail = dict(id, thread, from_name, from_addr, to, subject, date (epoch s), unread, text)
       thread(thread_id, last=3) -> [Mail]          the last few messages of a conversation, oldest first
       recipients(name, limit=5) -> [(name, addr, count)]   who the user has mailed or been mailed by under that name, most frequent first
       send(to, subject, body) -> id                a plain-text message from the user's own account
       gmail_query(sender="", span=None, text="", unread=False) -> str   Gmail search syntax from the pieces a person says
       clean_body(text) -> str                      drops quoted replies, signatures and link noise
  out: ToolError("unavailable", ...) for no token, no network, a revoked sign-in or a Gmail error; the caller speaks it.
Auth: the OAuth token file of the user's own Google client (config.gmail_token_path, default ~/VatsalDaPro/Keys/FRED_Gmail_token.json, outside the repo). It is only read and refreshed here,
  never printed or logged. Needs the modify or compose scope (sending); reading needs no more.
Tests set `TRANSPORT = fn(method, path, params, body) -> dict` and never touch the network.
Self-check: python tools/gmail.py
"""
import base64
import email.utils
import json
import re
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from tools.registry import ToolError

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TRANSPORT = None
_session = {"s": None}


def _token_path() -> Path:
    c = getattr(config.get(), "gmail_token_path", "")
    return Path(c) if c else Path.home() / "VatsalDaPro" / "Keys" / "FRED_Gmail_token.json"


def _auth():
    if _session["s"] is not None:
        return _session["s"]
    p = _token_path()
    if not p.exists():
        raise ToolError("unavailable", "Gmail isn't connected on this computer.", "no gmail token file")
    try:
        from google.auth.transport.requests import AuthorizedSession, Request
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(p))
        if not creds.valid:
            creds.refresh(Request())
            p.write_text(creds.to_json(), encoding="utf-8")
        _session["s"] = AuthorizedSession(creds)
        return _session["s"]
    except Exception as ex:
        raise ToolError("unavailable", "I can't sign in to Gmail right now.", f"gmail auth failed: {type(ex).__name__}")


def call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
    if TRANSPORT:
        return TRANSPORT(method, path, params or {}, body)
    s = _auth()
    for wait in (1, 2, None):
        try:
            r = s.request(method, f"{BASE}/{path}", params=params, json=body, timeout=30)
        except Exception as ex:
            raise ToolError("unavailable", "I can't reach Gmail right now.", f"gmail request failed: {type(ex).__name__}")
        if r.status_code in (429, 500, 503) and wait:
            time.sleep(wait)
            continue
        if r.status_code in (401, 403):
            raise ToolError("unavailable", "Gmail won't let me in; the sign-in may need renewing.", f"gmail http {r.status_code}")
        if r.status_code >= 400:
            raise ToolError("unavailable", "Gmail couldn't do that.", f"gmail http {r.status_code}")
        return r.json() if r.content else {}
    raise ToolError("unavailable", "Gmail is busy, try again in a moment.", "gmail rate limited")


# ---- parsing ---------------------------------------------------------------------------------------------------------------------------
def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _text(payload: dict) -> str:
    """text/plain preferred; else html with tags dropped."""
    plain, html = [], []

    def walk(p):
        mt, data = p.get("mimeType", ""), (p.get("body") or {}).get("data")
        if data and mt == "text/plain":
            plain.append(_b64(data))
        elif data and mt == "text/html":
            html.append(_b64(data))
        for c in p.get("parts") or []:
            walk(c)
    walk(payload)
    if plain:
        return "\n".join(plain)
    h = "\n".join(html)
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"&nbsp;", " ", re.sub(r"&amp;", "&", h))


def clean_body(text: str) -> str:
    out = []
    for line in text.replace("\r", "").split("\n"):
        s = line.strip()
        if re.match(r"^(on .{5,120} wrote:|-{2,}\s*original message|from:.*sent:|-- ?$|sent from my )", s, re.I) and out:
            break
        if s.startswith(">"):
            continue
        out.append(re.sub(r"https?://\S{60,}", "[link]", s))
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return re.sub(r"[ \t]+", " ", t)


def mail(m: dict) -> dict:
    h = {x["name"].lower(): x["value"] for x in (m.get("payload") or {}).get("headers", [])}
    name, addr = email.utils.parseaddr(h.get("from", ""))
    try:
        date = email.utils.parsedate_to_datetime(h["date"]).timestamp()
    except (KeyError, TypeError, ValueError):
        date = int(m.get("internalDate", "0")) / 1000
    return {"id": m["id"], "thread": m.get("threadId", ""), "from_name": name or addr, "from_addr": addr.lower(), "to": h.get("to", ""), "subject": h.get("subject", "(no subject)"),
            "date": date, "unread": "UNREAD" in m.get("labelIds", []), "text": clean_body(_text(m.get("payload") or {})) or (m.get("snippet") or "")}


# ---- operations ------------------------------------------------------------------------------------------------------------------------
def gmail_query(sender: str = "", span=None, text: str = "", unread: bool = False) -> str:
    q = []
    if sender.strip():
        q.append(f'from:("{sender.strip()}")' if "@" not in sender else f"from:{sender.strip()}")
    if span:
        q.append(f"after:{int(span[0])} before:{int(span[1])}")
    if text.strip():
        q.append(text.strip())
    if unread:
        q.append("is:unread")
    return " ".join(q) or "in:inbox"


def search(query: str, limit: int = 5) -> list:
    ids = call("GET", "messages", {"q": query, "maxResults": limit}).get("messages", [])
    return [mail(call("GET", f"messages/{i['id']}", {"format": "full"})) for i in ids[:limit]]


def thread(thread_id: str, last: int = 3) -> list:
    msgs = call("GET", f"threads/{thread_id}", {"format": "full"}).get("messages", [])
    return [mail(m) for m in msgs[-last:]]


def recipients(name: str, limit: int = 5) -> list:
    n = name.strip().lower()
    found = {}
    for q in (f'from:("{name}")', f'to:("{name}")'):
        for i in call("GET", "messages", {"q": q, "maxResults": 15}).get("messages", [])[:15]:
            m = call("GET", f"messages/{i['id']}", {"format": "metadata", "metadataHeaders": ["From", "To"]})
            for hdr in (m.get("payload") or {}).get("headers", []):
                for nm, ad in email.utils.getaddresses([hdr["value"]]):
                    if ad and n in (nm + " " + ad).lower():
                        key = ad.lower()
                        old = found.get(key, (nm, 0))
                        found[key] = (old[0] or nm, old[1] + 1)
    return sorted(((v[0], k, v[1]) for k, v in found.items()), key=lambda x: -x[2])[:limit]


def send(to: str, subject: str, body: str) -> str:
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"], msg["Subject"] = to, subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return call("POST", "messages/send", None, {"raw": raw}).get("id", "")


def _selfcheck():
    import tempfile
    config.set_data_dir(tempfile.mkdtemp())

    def enc(t):
        return base64.urlsafe_b64encode(t.encode()).decode().rstrip("=")

    def msg(i, frm, subj, body, date="Tue, 06 Oct 2026 09:30:00 +0530", labels=("INBOX",), html=False, thread="t1"):
        part = {"mimeType": "text/html" if html else "text/plain", "body": {"data": enc(body)}}
        return {"id": i, "threadId": thread, "labelIds": list(labels), "snippet": "snip", "payload": {"mimeType": "multipart/alternative", "parts": [part],
                "headers": [{"name": "From", "value": frm}, {"name": "To", "value": "me@example.com"}, {"name": "Subject", "value": subj}, {"name": "Date", "value": date}]}}
    store = {"1": msg("1", "Rohan Das <rohan@example.com>", "Sunday lunch", "Hi,\nCome at 1pm?\n\nOn Mon, 5 Oct 2026 at 10:00, Me <me@example.com> wrote:\n> are we on\n> yes", labels=("INBOX", "UNREAD")),
             "2": msg("2", "Rohan D <rohan@example.com>", "Re: Sunday lunch", "<div>Great,<br>see you</div><script>x()</script>", html=True),
             "3": msg("3", "Shop <deals@example.com>", "Sale", "Buy now https://example.com/" + "a" * 90 + "\n-- \nUnsubscribe")}
    sent = []

    def fake(method, path, params, body):
        if method == "POST":
            sent.append(body)
            return {"id": "sent1"}
        if path == "messages":
            q = params.get("q", "")
            ids = ["1", "2"] if "rohan" in q.lower() else ["3"] if "sale" in q.lower() else []
            return {"messages": [{"id": i} for i in ids]}
        if path.startswith("messages/"):
            return store[path.split("/")[1]]
        if path.startswith("threads/"):
            return {"messages": [store["1"], store["2"]]}
        raise AssertionError(path)
    global TRANSPORT
    TRANSPORT = fake
    got = search("from:rohan")
    assert [g["id"] for g in got] == ["1", "2"] and got[0]["unread"] and not got[1]["unread"]
    assert got[0]["from_name"] == "Rohan Das" and got[0]["from_addr"] == "rohan@example.com" and got[0]["subject"] == "Sunday lunch" and got[0]["date"] > 1.7e9
    assert got[0]["text"] == "Hi,\nCome at 1pm?", got[0]["text"]                                  # the quoted reply is dropped
    assert got[1]["text"].split() == ["Great,", "see", "you"], got[1]["text"]                     # html reduced to text, script dropped
    assert "[link]" in search("sale")[0]["text"] and "Unsubscribe" not in search("sale")[0]["text"]
    assert [m["id"] for m in thread("t1")] == ["1", "2"]
    r = recipients("rohan")
    assert r[0][1] == "rohan@example.com" and r[0][2] >= 2, r
    assert recipients("nobody") == []
    assert send("rohan@example.com", "Hi", "héllo") == "sent1"
    raw = base64.urlsafe_b64decode(sent[0]["raw"]).decode()
    assert "To: rohan@example.com" in raw and "Subject: Hi" in raw and "utf-8" in raw.lower()
    assert gmail_query("Dad", (1, 2), "lunch", True) == 'from:("Dad") after:1 before:2 lunch is:unread' and gmail_query() == "in:inbox" and gmail_query("a@b.com") == "from:a@b.com"
    TRANSPORT = None
    config.get().gmail_token_path = str(Path(tempfile.mkdtemp()) / "none.json")
    try:
        call("GET", "messages")
        raise AssertionError
    except ToolError as ex:
        assert ex.kind == "unavailable" and "isn't connected" in ex.speakable
    config.reload()
    print("gmail ok")


if __name__ == "__main__":
    _selfcheck()
