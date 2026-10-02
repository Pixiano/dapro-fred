# Core/tests/test_gmail_api.py
#
# gmail_api's real logic, no network: read fallback, reply timing, the
# new-mail delta, attachment filename safety, and "only a sent reply marks
# anything read".

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import gmail_api as ga


def _raw(headers: str, body: str = "hello") -> str:
    return base64.urlsafe_b64encode(f"{headers}\r\n\r\n{body}".encode()).decode()


def test_read_falls_back_to_imap_when_api_breaks(monkeypatch):
    def boom():
        raise RuntimeError("token revoked")
    monkeypatch.setattr(ga, "_session", boom)
    monkeypatch.setattr(ga.event_log, "log_error", lambda *a: None)
    monkeypatch.setattr(ga.gmail_imap, "search", lambda q, n: ["imap-msg"])
    assert ga._find("from:x", 1) == [(None, "imap-msg")]


def test_only_later_sent_mail_counts_as_reply(monkeypatch):
    thread = {"messages": [
        {"labelIds": ["SENT"], "internalDate": "1000"},   # Vatsal wrote first
        {"labelIds": ["INBOX"], "internalDate": "2000"},  # their email
    ]}
    monkeypatch.setattr(ga, "_get_json", lambda s, path, **p: thread)
    assert ga._replied_after(None, "t", 2000) is False
    thread["messages"].append({"labelIds": ["SENT"], "internalDate": "3000"})
    assert ga._replied_after(None, "t", 2000) is True


def test_attachment_names_cannot_escape_the_folder():
    assert ga._safe_filename("..\\..\\Windows\\evil.exe") == "evil.exe"
    assert ga._safe_filename("../../etc/passwd") == "passwd"
    assert ga._safe_filename('a<b>:c?.pdf') == "a_b__c_.pdf"
    assert ga._safe_filename("CON.txt") == "_CON.txt"
    assert ga._safe_filename("...") == "attachment"


def test_new_mail_delta(monkeypatch, tmp_path):
    token = tmp_path / "token.json"
    token.write_text("{}")
    overrides = tmp_path / "email-tiers.md"
    overrides.write_text("## basic\n- acme updates\n")
    monkeypatch.setattr(ga, "GMAIL_OAUTH_TOKEN_PATH", token)
    monkeypatch.setattr(ga, "GMAIL_TIER_OVERRIDES_PATH", overrides)
    monkeypatch.setattr(ga, "HISTORY_STATE_PATH", tmp_path / "h.json")
    monkeypatch.setattr(ga, "_session", lambda need=None: object())
    messages = {
        "vip": (["INBOX", "UNREAD"], "From: Priya <p@x.com>\r\nSubject: Report", "due by Friday"),
        "news": (["INBOX"], "From: Shop <s@x.com>\r\nList-Unsubscribe: <mailto:u@x.com>\r\nSubject: Sale", "x"),
        "acme": (["INBOX", "CATEGORY_UPDATES"],
                   "From: Acme Updates <news@acme.example>\r\nList-Unsubscribe: <x>\r\nSubject: M", "x"),
        "noreply": (["INBOX"], "From: Google <no-reply@accounts.google.com>\r\nSubject: Alert", "x"),
        "promo": (["INBOX", "CATEGORY_PROMOTIONS"], "From: Ad <a@x.com>\r\nSubject: Ad", "x"),
        "mine": (["INBOX", "SENT"], "From: Me <me@x.com>\r\nSubject: Note", "x"),
        "plain": (["INBOX"], "From: Bob <b@x.com>\r\nSubject: Hi", "hello"),
    }
    opened = []

    def fake_get_json(s, path, **p):
        if path == "profile":
            return {"historyId": "100"}
        if path == "history":
            assert p["startHistoryId"] == "100"
            return {"historyId": "200", "history": [{"messagesAdded": [{"message": {"id": k}} for k in messages]}]}
        if path == "labels":
            return {"labels": [{"name": n, "id": n} for n in ("FRED", *ga.TIER_LABELS.values())]}
        if path.startswith("messages/"):
            gid = path.split("/")[1]
            labels, headers, body = messages[gid]
            if p.get("format") == "raw":
                opened.append(gid)
            return {"id": gid, "threadId": "t", "labelIds": labels, "internalDate": "0", "raw": _raw(headers, body)}
        if path == "messages":  # _emailed_first: Vatsal has emailed Priya and Google
            return {"messages": [{"id": "sent"}]} if ("p@x.com" in p["q"] or "google" in p["q"]) else {}
        raise AssertionError(path)

    calls = []
    monkeypatch.setattr(ga, "_get_json", fake_get_json)
    monkeypatch.setattr(ga, "_call", lambda s, method, path, body=None, **p: calls.append((path, body)) or {})
    assert ga.check_new_mail() == []  # first run only sets the baseline
    assert ga.check_new_mail() == [
        ("vvip", 'New email from Priya: "Report" -- mentions: due by Friday'),
        ("basic", 'New email from Google: "Alert"'),   # no-reply sender: never VVIP
        ("basic", 'New email from Bob: "Hi"'),
    ]  # acme is Updates (labelled, not alerted); news is useless
    assert "promo" not in opened and "mine" not in opened  # other tabs never opened
    labelled = {body["addLabelIds"][0]: body["ids"] for path, body in calls if path == "messages/batchModify"}
    assert labelled == {"FRED/VVIP": ["vip"], "FRED/Basic": ["acme", "noreply", "plain"], "FRED/Useless": ["news"]}
    assert ga._load_json(ga.HISTORY_STATE_PATH, {}) == {"history_id": "200"}


def test_only_a_sent_reply_marks_anything_read(monkeypatch, tmp_path):
    monkeypatch.setattr(ga, "PENDING_DRAFT_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ga, "_session", lambda need=None: object())
    calls = []
    monkeypatch.setattr(ga, "_call", lambda s, method, path, body=None, **p: calls.append((method, path, body)) or {})

    ga._save_json(ga.PENDING_DRAFT_PATH, {"draft_id": "d1", "reply_to_id": None})
    assert ga.send_draft() == "Sent, sir."
    assert calls == [("POST", "drafts/send", {"id": "d1"})]

    calls.clear()
    ga._save_json(ga.PENDING_DRAFT_PATH, {"draft_id": "d2", "reply_to_id": "orig"})
    ga.send_draft()
    assert calls[1] == ("POST", "messages/orig/modify", {"removeLabelIds": ["UNREAD"]})
    assert not ga.PENDING_DRAFT_PATH.exists()


def test_rate_limit_is_retried_not_fatal(monkeypatch):
    class _R:
        def __init__(self, code, text):
            self.status_code, self.text, self.content = code, text, b"{}"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

        def json(self):
            return {"ok": True}

    replies = [_R(403, '{"reason": "rateLimitExceeded"}'), _R(429, ""), _R(200, "{}")]

    class _S:
        def request(self, *a, **k):
            return replies.pop(0)

    monkeypatch.setattr(ga.time, "sleep", lambda s: None)
    assert ga._call(_S(), "GET", "messages") == {"ok": True}
