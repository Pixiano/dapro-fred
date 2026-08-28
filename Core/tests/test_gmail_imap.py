# Core/tests/test_gmail_imap.py
#
# Pure logic tests for tools/gmail_imap.py's temporary IMAP bridge --
# imaplib.IMAP4_SSL is entirely mocked, no real network, matching this
# repo's existing convention for network-adjacent modules (see
# test_vision_server.py). "test-app-password" below is an obviously fake
# mock value, not a real credential shape to worry about.

import email.utils
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from tools import gmail_imap as gi


class _FakeIMAP:
    """Minimal imaplib.IMAP4_SSL stand-in: select()/search()/fetch()
    return whatever the test wired up in `mailboxes` ({folder: [(num,
    raw_bytes), ...]})."""

    def __init__(self, mailboxes):
        self.mailboxes = mailboxes
        self.current = None

    def login(self, addr, pw):
        pass

    def select(self, folder):
        self.current = folder.strip('"')
        return "OK", [b"1"]

    def search(self, charset, criterion):
        nums = [str(n).encode() for n, _ in self.mailboxes.get(self.current, [])]
        return "OK", [b" ".join(nums)]

    def fetch(self, num, spec):
        for n, raw in self.mailboxes.get(self.current, []):
            if str(n) == num.decode() if isinstance(num, bytes) else str(n) == num:
                return "OK", [(b"1", raw)]
        return "OK", [(b"1", b"")]

    def logout(self):
        pass


def _msg(msg_id, frm, subject, date_days_ago=0, body="hello", in_reply_to=None):
    date = email.utils.format_datetime(
        __import__("datetime").datetime.now() - __import__("datetime").timedelta(days=date_days_ago)
    )
    headers = (
        f"Message-ID: {msg_id}\r\n"
        f"From: {frm}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {date}\r\n"
    )
    if in_reply_to:
        headers += f"In-Reply-To: {in_reply_to}\r\n"
    return (headers + "\r\n" + body).encode()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "MISSED_REPLY_SEEN_PATH", tmp_path / "missed_seen.json")
    monkeypatch.setattr(gi, "DEADLINE_SEEN_PATH", tmp_path / "deadline_seen.json")
    monkeypatch.setattr(gi, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(gi, "GMAIL_APP_PASSWORD", "test-app-password")  # mock only
    monkeypatch.setattr(gi, "GMAIL_MISSED_REPLY_DAYS", 3)
    monkeypatch.setattr(gi, "GMAIL_DEADLINE_LOOKBACK_DAYS", 7)


def test_noop_without_credentials(monkeypatch):
    monkeypatch.setattr(gi, "GMAIL_APP_PASSWORD", None)
    monkeypatch.setattr(imaplib_module := gi.imaplib, "IMAP4_SSL",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect")))
    assert gi.check_missed_replies() == ""
    assert gi.check_email_deadlines() == ""


def test_missed_reply_flagged_when_no_sent_match(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<a@x>", "Boss <boss@x.com>", "Re: project", date_days_ago=5))],
        "[Gmail]/Sent Mail": [],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    summary = gi.check_missed_replies()
    assert "Boss" in summary
    assert "project" in summary


def test_missed_reply_not_flagged_when_sent_has_matching_reply(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<a@x>", "Boss <boss@x.com>", "Re: project", date_days_ago=5))],
        "[Gmail]/Sent Mail": [(1, _msg("<r@x>", "me@example.com", "Re: Re: project", in_reply_to="<a@x>"))],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    assert gi.check_missed_replies() == ""


def test_missed_reply_dedups_across_calls(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<a@x>", "Boss <boss@x.com>", "Re: project", date_days_ago=5))],
        "[Gmail]/Sent Mail": [],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    first = gi.check_missed_replies()
    second = gi.check_missed_replies()
    assert first != ""
    assert second == ""


def test_deadline_phrase_detected(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<d@x>", "School <school@x.com>", "Notice", date_days_ago=1,
                            body="Please submit the form by tomorrow, thanks."))],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    summary = gi.check_email_deadlines()
    assert "School" in summary
    assert "tomorrow" in summary.lower()


def test_no_deadline_phrase_returns_empty(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<d@x>", "Someone <s@x.com>", "Hi", date_days_ago=1, body="just saying hello"))],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    assert gi.check_email_deadlines() == ""


def test_deadline_dedups_across_calls(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<d@x>", "School <school@x.com>", "Notice", date_days_ago=1,
                            body="due tomorrow"))],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    first = gi.check_email_deadlines()
    second = gi.check_email_deadlines()
    assert first != ""
    assert second == ""


def test_connection_failure_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionRefusedError("no network")
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", _boom)

    assert gi.check_missed_replies() == ""
    assert gi.check_email_deadlines() == ""


def test_auth_failure_never_raises(monkeypatch):
    class _BadAuth(_FakeIMAP):
        def login(self, addr, pw):
            raise gi.imaplib.IMAP4.error("auth failed")

    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _BadAuth({}))

    assert gi.check_missed_replies() == ""
    assert gi.check_email_deadlines() == ""
