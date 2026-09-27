# Core/tests/test_gmail_imap.py
#
# The IMAP fallback's two safety properties (never marks mail read, never
# lets a link through) plus how it hands a query to Gmail. No network.

import email
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import gmail_imap as gi


def test_links_are_redacted_to_domain_only():
    text = "Pay here https://evil.example.com/login?x=1 or www.shop.in/deal now, mail me@www.site.com"
    out = gi.redact_links(text)
    assert "evil.example.com/login" not in out and "shop.in/deal" not in out
    assert "[REDACTED link to evil.example.com]" in out
    assert "[REDACTED link to shop.in]" in out
    assert "me@www.site.com" in out  # an email address is not a link


def test_html_body_hrefs_never_survive():
    msg = email.message_from_string(
        'Content-Type: text/html\n\n<a href="https://track.example.com/abc">Click here</a> and https://x.io/y'
    )
    out = gi._body_text(msg)
    assert "track.example.com" not in out
    assert "Click here" in out and "[REDACTED link to x.io]" in out


def test_fetch_never_marks_email_read():
    specs = []

    class _Conn:
        def fetch(self, num, spec):
            specs.append(spec)
            return "OK", [(b"1", b"Subject: x\r\n\r\nbody")]

    gi._fetch_message(_Conn(), b"1")
    assert specs == ["(BODY.PEEK[])"]


def test_search_is_read_only_all_mail_newest_first(monkeypatch):
    calls = {}

    class _Conn:
        def __init__(self, host):
            pass

        def login(self, a, p):
            pass

        def select(self, folder, readonly=False):
            calls["select"] = (folder, readonly)

        def search(self, charset, *criteria):
            calls["search"] = criteria
            return "OK", [b"1 2 3"]

        def fetch(self, num, spec):
            return "OK", [(b"1", b"Subject: " + num + b"\r\n\r\nx")]

        def logout(self):
            pass

    monkeypatch.setattr(gi, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(gi, "GMAIL_APP_PASSWORD", "fake")
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", _Conn)
    found = gi.search('subject:"a b"', limit=2)
    assert calls["select"] == ('"[Gmail]/All Mail"', True)
    assert calls["search"] == ("X-GM-RAW", '"subject:\\"a b\\""')
    assert [m["Subject"] for m in found] == ["3", "2"]
