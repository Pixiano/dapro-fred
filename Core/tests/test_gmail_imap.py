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

    def search(self, charset, *criteria):
        entries = self.mailboxes.get(self.current, [])
        if len(criteria) >= 2 and criteria[0] == "TO":
            # Crude substring match against the raw message bytes --
            # good enough for tests, real Gmail does this server-side.
            addr = criteria[1].strip('"').lower()
            entries = [(n, raw) for n, raw in entries if addr in raw.decode(errors="ignore").lower()]
        nums = [str(n).encode() for n, _ in entries]
        return "OK", [b" ".join(nums)]

    def fetch(self, num, spec):
        for n, raw in self.mailboxes.get(self.current, []):
            if str(n) == num.decode() if isinstance(num, bytes) else str(n) == num:
                return "OK", [(b"1", raw)]
        return "OK", [(b"1", b"")]

    def logout(self):
        pass


def _msg(msg_id, frm, subject, date_days_ago=0, body="hello", in_reply_to=None, list_unsubscribe=None):
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
    if list_unsubscribe:
        headers += f"List-Unsubscribe: {list_unsubscribe}\r\n"
    return (headers + "\r\n" + body).encode()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "MISSED_REPLY_SEEN_PATH", tmp_path / "missed_seen.json")
    monkeypatch.setattr(gi, "DEADLINE_SEEN_PATH", tmp_path / "deadline_seen.json")
    monkeypatch.setattr(gi, "TIER_SEEN_PATH", tmp_path / "tier_seen.json")
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


def test_inbox_search_scoped_to_primary_category(monkeypatch):
    """Vatsal's own call 2026-08-28: only the Primary tab, not the whole
    flat INBOX (which also holds Promotions/Social/Updates/Forums) --
    verifies the actual X-GM-RAW query sent, not just the mailbox result."""
    calls = []

    class _RecordingIMAP(_FakeIMAP):
        def search(self, charset, *criteria):
            calls.append(criteria)
            return super().search(charset, *criteria)

    mailboxes = {"INBOX": [], "[Gmail]/Sent Mail": []}
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _RecordingIMAP(mailboxes))

    gi.check_missed_replies()
    gi.check_email_deadlines()

    inbox_calls = [c for c in calls if c[0] == "X-GM-RAW"]
    assert inbox_calls, "expected an X-GM-RAW search"
    for criteria in inbox_calls:
        assert "category:primary" in criteria[1]


def test_connection_failure_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionRefusedError("no network")
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", _boom)

    assert gi.check_missed_replies() == ""
    assert gi.check_email_deadlines() == ""
    assert "Couldn't reach Gmail" in gi.read_recent_primary()


def test_auth_failure_never_raises(monkeypatch):
    class _BadAuth(_FakeIMAP):
        def login(self, addr, pw):
            raise gi.imaplib.IMAP4.error("auth failed")

    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _BadAuth({}))

    assert gi.check_missed_replies() == ""
    assert gi.check_email_deadlines() == ""
    assert "Couldn't reach Gmail" in gi.read_recent_primary()


# =========================================================
# read_recent_primary -- on-demand "check my email" tool, fills the gap
# found live 2026-08-28 (FRED had no email tool, defaulted to the
# WhatsApp reader for "get me my mail").
# =========================================================

def test_read_recent_primary_no_credentials(monkeypatch):
    monkeypatch.setattr(gi, "GMAIL_APP_PASSWORD", None)
    assert "isn't set up" in gi.read_recent_primary().lower()


def test_read_recent_primary_without_llm_lists_sender_subject(monkeypatch):
    mailboxes = {
        "INBOX": [(2, _msg("<b@x>", "Boss <boss@x.com>", "Re: project")),
                  (1, _msg("<a@x>", "School <school@x.com>", "Notice"))],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    out = gi.read_recent_primary(count=5, llm=None)
    assert "Boss" in out and "project" in out
    assert "School" in out and "Notice" in out


def test_read_recent_primary_respects_count(monkeypatch):
    mailboxes = {
        "INBOX": [(3, _msg("<c@x>", "C <c@x.com>", "Three")),
                  (2, _msg("<b@x>", "B <b@x.com>", "Two")),
                  (1, _msg("<a@x>", "A <a@x.com>", "One"))],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    out = gi.read_recent_primary(count=1, llm=None)
    assert "Three" in out  # newest (highest num) kept
    assert "Two" not in out
    assert "One" not in out


def test_read_recent_primary_with_llm_summarizes(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<a@x>", "Boss <boss@x.com>", "Re: project", body="Please send the report."))],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    calls = []

    class _FakeLLM:
        def generate(self, prompt, local_only=False, force_no_thinking=False):
            calls.append((prompt, local_only, force_no_thinking))
            return "Boss wants the report."

    out = gi.read_recent_primary(count=5, llm=_FakeLLM())
    assert out == "Boss wants the report."
    assert len(calls) == 1
    _, local_only, force_no_thinking = calls[0]
    assert local_only is True  # unattended raw-email content, never cloud
    assert force_no_thinking is True


def test_read_recent_primary_empty_inbox(monkeypatch):
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP({"INBOX": []}))
    assert "Nothing" in gi.read_recent_primary()


# =========================================================
# check_email_tiers -- three-tier classification for newly-arrived
# email (useless/basic/vvip), Vatsal's own 2026-08-28 design.
# =========================================================

def test_tier_useless_for_list_unsubscribe_header(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<n@x>", "Newsletter <news@x.com>", "This week",
                            list_unsubscribe="<mailto:unsub@x.com>"))],
        "[Gmail]/Sent Mail": [],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    results = gi.check_email_tiers()
    assert results == [("useless", results[0][1])]


def test_tier_vvip_when_vatsal_emailed_first(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<f@x>", "Friend <friend@x.com>", "Hey"))],
        "[Gmail]/Sent Mail": [(1, _msg("<s@x>", "me@example.com", "intro", body="hi friend@x.com"))],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    results = gi.check_email_tiers()
    assert len(results) == 1
    tier, summary = results[0]
    assert tier == "vvip"
    assert "Friend" in summary


def test_tier_basic_for_everything_else(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<x@x>", "Stranger <stranger@x.com>", "Hi there"))],
        "[Gmail]/Sent Mail": [],  # never emailed this address
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    results = gi.check_email_tiers()
    assert results == [("basic", results[0][1])]


def test_tier_dedups_across_calls(monkeypatch):
    mailboxes = {
        "INBOX": [(1, _msg("<x@x>", "Stranger <stranger@x.com>", "Hi there"))],
        "[Gmail]/Sent Mail": [],
    }
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: _FakeIMAP(mailboxes))

    first = gi.check_email_tiers()
    second = gi.check_email_tiers()
    assert len(first) == 1
    assert second == []


def test_tier_check_never_raises_on_connection_failure(monkeypatch):
    monkeypatch.setattr(gi.imaplib, "IMAP4_SSL", lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError()))
    assert gi.check_email_tiers() == []
