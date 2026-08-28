# Core/tools/gmail_imap.py
#
# TEMPORARY Gmail bridge over IMAP + a Google App Password, not the real
# Gmail API. Vatsal hit his GCP project limit 2026-08-28 — deletion/reuse
# won't clear for ~1 month, likely past this session's tool-calling LoRA
# fine-tune — so this exists to unblock the two Gmail features from
# roadmap_pre-finetune_2026-08-26.md section 3(d) in the meantime.
# Read-only, same practical scope as the `gmail.readonly` OAuth scope
# would have been. Swap this module for a real gmail_api.py later; the
# proactive_checks.py wiring and dedup logic can stay as-is, only the
# transport here changes.
#
# CREDENTIAL HANDLING: GMAIL_ADDRESS/GMAIL_APP_PASSWORD come from
# os.environ (config/settings.py), set once via
# scripts/setup_gmail_credentials.py — never typed to or read by Claude,
# never written to a file. Same "no-op until enrolled" convention
# orchestrator/headphone_watch.py uses before scripts/enroll_headphones.py
# has run: every public function here returns "" immediately if the
# credentials aren't set, rather than attempting a connection.

import email
import email.header
import email.utils
import imaplib
import json
import re
from datetime import datetime, timedelta

from config.settings import (
    DATA_DIR,
    GMAIL_ADDRESS,
    GMAIL_APP_PASSWORD,
    GMAIL_DEADLINE_LOOKBACK_DAYS,
    GMAIL_MISSED_REPLY_DAYS,
)

MISSED_REPLY_SEEN_PATH = DATA_DIR / "gmail_missed_reply_seen.json"
DEADLINE_SEEN_PATH = DATA_DIR / "gmail_deadline_seen.json"

# Same lightweight date-phrase shapes tools/agenda.py's parse_due_date
# matches (today/tomorrow/in N days/weekday/ISO/"13 August 2026") — this
# scans free-text email BODIES rather than a structured `due` argument,
# so it's a standalone scanner, not an import of agenda.py's parser.
_MONTH_NAMES = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    "aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DEADLINE_PATTERNS = [
    re.compile(r"\bdue\s+(?:by\s+|on\s+)?\w[\w ,]{0,25}", re.IGNORECASE),
    re.compile(r"\bdeadline[:\s]+\w[\w ,]{0,25}", re.IGNORECASE),
    re.compile(r"\bby\s+(tomorrow|today|next week|end of \w+)", re.IGNORECASE),
    re.compile(r"\b(tomorrow|today)\b", re.IGNORECASE),
    re.compile(r"\bin\s+\d+\s+(day|days|week|weeks)\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_NAMES})\.?\s*\d{{0,4}}\b", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]


def _primary_search(conn, since: datetime):
    """IMAP has no native concept of Gmail's Primary/Social/Promotions/
    Updates/Forums tabs -- those are a Gmail-web-UI-only categorization
    over one flat INBOX. X-GM-RAW is Gmail's own IMAP extension (only
    works against imap.gmail.com) that accepts a real Gmail search query
    string, which DOES support `category:primary` -- the only way to
    actually scope this to Primary rather than reading everything.
    Vatsal's own call 2026-08-28: only Primary, not the whole inbox."""
    query = f'"category:primary after:{since.strftime("%Y/%m/%d")}"'
    return conn.search(None, "X-GM-RAW", query)


def _connect():
    """IMAP4_SSL logged in, or None if credentials aren't set / login
    fails. Caller must close() it when done."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return None
    conn = imaplib.IMAP4_SSL("imap.gmail.com")
    conn.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    return conn


def _load_seen(path) -> set:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_seen(path, seen: set):
    keep = sorted(seen)[-500:]  # tail only, this is dedup not an archive
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(keep), encoding="utf-8")
    tmp.replace(path)


def _decode_header(raw) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    return "".join(
        p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, enc in parts
    )


def _fetch_message(conn, num) -> email.message.Message:
    _, data = conn.fetch(num, "(RFC822)")
    return email.message_from_bytes(data[0][1])


def _body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                return re.sub(r"<[^>]+>", " ", html)
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def check_missed_replies() -> str:
    """An inbox email >= GMAIL_MISSED_REPLY_DAYS old, not from Vatsal
    himself, with no matching In-Reply-To/References in [Gmail]/Sent
    Mail — one short summary of the most notable one, or "" if nothing
    new. Dedup via a seen-set keyed on Message-ID, same shape as
    whatsapp_tools.check_vip_messages's own seen-stamp file."""
    try:
        conn = _connect()
        if conn is None:
            return ""
        try:
            since = datetime.now() - timedelta(days=GMAIL_MISSED_REPLY_DAYS + 4)
            conn.select("INBOX")
            _, data = _primary_search(conn, since)
            inbox_nums = data[0].split() if data and data[0] else []

            conn.select('"[Gmail]/Sent Mail"')  # standard English-locale Gmail folder name
            _, sent_data = conn.search(None, "ALL")
            sent_nums = sent_data[0].split() if sent_data and sent_data[0] else []
            replied_to_ids = set()
            for num in sent_nums:
                msg = _fetch_message(conn, num)
                for header in ("In-Reply-To", "References"):
                    value = msg.get(header, "")
                    replied_to_ids.update(value.split())

            conn.select("INBOX")
            seen = _load_seen(MISSED_REPLY_SEEN_PATH)
            cutoff = datetime.now() - timedelta(days=GMAIL_MISSED_REPLY_DAYS)
            missed = []
            for num in inbox_nums:
                msg = _fetch_message(conn, num)
                msg_id = msg.get("Message-ID", "")
                if not msg_id or msg_id in seen:
                    continue
                from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
                if GMAIL_ADDRESS and from_addr.lower() == GMAIL_ADDRESS.lower():
                    continue
                try:
                    sent_at = email.utils.parsedate_to_datetime(msg.get("Date", ""))
                    if sent_at.tzinfo is not None:
                        sent_at = sent_at.replace(tzinfo=None)
                except (TypeError, ValueError):
                    continue
                if sent_at > cutoff:
                    continue  # not old enough yet
                if msg_id in replied_to_ids:
                    seen.add(msg_id)  # replied -- never surface, never re-check
                    continue
                days = (datetime.now() - sent_at).days
                missed.append((msg_id, _decode_header(msg.get("From", "")), _decode_header(msg.get("Subject", "")), days))

            _save_seen(MISSED_REPLY_SEEN_PATH, seen | {m[0] for m in missed})

            if not missed:
                return ""
            sender, subject, days = missed[0][1], missed[0][2], missed[0][3]
            return f'{sender} emailed "{subject}" {days} day(s) ago, still no reply'
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception:
        return ""


def check_email_deadlines() -> str:
    """Inbox emails from the last GMAIL_DEADLINE_LOOKBACK_DAYS days,
    body-scanned for a date-like phrase — one short summary of the most
    notable one, or "" if nothing new. Dedup via its own seen-set."""
    try:
        conn = _connect()
        if conn is None:
            return ""
        try:
            since = datetime.now() - timedelta(days=GMAIL_DEADLINE_LOOKBACK_DAYS)
            conn.select("INBOX")
            _, data = _primary_search(conn, since)
            nums = data[0].split() if data and data[0] else []

            seen = _load_seen(DEADLINE_SEEN_PATH)
            found = []
            for num in nums:
                msg = _fetch_message(conn, num)
                msg_id = msg.get("Message-ID", "")
                if not msg_id or msg_id in seen:
                    continue
                body = _body_text(msg)
                match = None
                for pattern in _DEADLINE_PATTERNS:
                    m = pattern.search(body)
                    if m:
                        match = m.group(0).strip()
                        break
                seen.add(msg_id)
                if match:
                    found.append((_decode_header(msg.get("From", "")), _decode_header(msg.get("Subject", "")), match))

            _save_seen(DEADLINE_SEEN_PATH, seen)

            if not found:
                return ""
            sender, subject, phrase = found[0]
            return f'{sender}\'s email "{subject}" mentions: {phrase}'
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception:
        return ""
