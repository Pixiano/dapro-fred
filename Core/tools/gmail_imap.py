# Core/tools/gmail_imap.py
#
# FALLBACK read-only Gmail transport: IMAP + a Google App Password.
# tools/gmail_api.py is the real transport; it calls search() here only
# when the API is unreachable, so reads keep working through an outage.
# Everything else (new-mail alerts, drafts, sending, settings) is API-only.
# The old 5-minute IMAP rescans (missed replies / deadlines / tiers) were
# removed 2026-09-27, superseded by gmail_api's history-delta checks.
#
# Also home of the two text helpers BOTH transports share, so there is one
# place that decides what email text FRED is allowed to see:
#   _decode_header / _body_text -- every link is replaced with
#   "[REDACTED link to <domain>]". FRED never opens, fetches or reads a
#   link from an email; it can only say one was there and roughly where to.
#
# CREDENTIAL HANDLING: GMAIL_ADDRESS/GMAIL_APP_PASSWORD come from
# os.environ (config/settings.py), set once via
# scripts/setup_gmail_credentials.py -- never typed to or read by Claude.

import email
import email.header
import imaplib
import re

from config.settings import GMAIL_ADDRESS, GMAIL_APP_PASSWORD

_LINK_RE = re.compile(r"(?<![@\w.])(?:https?://|www\.)[^\s<>\"'()\[\]]+", re.IGNORECASE)


def redact_links(text: str) -> str:
    def _replace(match):
        host = re.sub(r"^(?:https?://)?(?:www\.)?", "", match.group(0), flags=re.IGNORECASE)
        host = re.split(r"[/?#:]", host, maxsplit=1)[0][:60]
        return f"[REDACTED link to {host}]" if host else "[REDACTED link]"
    return _LINK_RE.sub(_replace, text or "")


def _decode_header(raw) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    return redact_links("".join(
        p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, enc in parts
    ))


def _body_text(msg: email.message.Message) -> str:
    """Plain-text body (HTML stripped as a last resort), links redacted.
    HTML tags go first, so href targets never survive at all."""
    text = ""
    if msg.is_multipart():
        for kind in ("text/plain", "text/html"):
            part = next((p for p in msg.walk() if p.get_content_type() == kind), None)
            if part is not None:
                payload = part.get_payload(decode=True) or b""
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                if kind == "text/html":
                    text = re.sub(r"<[^>]+>", " ", text)
                break
    else:
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
    return redact_links(text)


def _fetch_message(conn, num) -> email.message.Message:
    # PEEK, not RFC822: an RFC822/BODY[] fetch sets \Seen, which silently
    # marked every polled email as read.
    _, data = conn.fetch(num, "(BODY.PEEK[])")
    return email.message_from_bytes(data[0][1])


def _imap_quote(query: str) -> str:
    return '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search(query: str, limit: int = 10) -> list:
    """Messages matching a Gmail search query, newest first, as
    email.message.Message objects. Same query syntax as the API (Gmail's
    own X-GM-RAW IMAP extension). Raises on any failure -- the caller
    decides what to tell Vatsal."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError("IMAP fallback isn't set up (no App Password)")
    conn = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        conn.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        # All Mail, read-only (EXAMINE): matches the API's search scope, and
        # nothing here can change a flag even if a fetch ever stopped peeking.
        conn.select('"[Gmail]/All Mail"', readonly=True)
        _, data = conn.search(None, "X-GM-RAW", _imap_quote(query))
        nums = (data[0].split() if data and data[0] else [])[::-1][:limit]
        return [_fetch_message(conn, n) for n in nums]
    finally:
        try:
            conn.logout()
        except Exception:
            pass
