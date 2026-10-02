# Core/tools/gmail_api.py
#
# Gmail over the real REST API (OAuth). No LLM anywhere in here -- every
# check and every reply string is hardcoded.
#
# READ (falls back to tools/gmail_imap.search when the API is down):
#   unread_today, search_email, read_email, download_attachment
# WRITE (API only, and the send/save/settings ones are confirmation-gated
# by the orchestrator, see orchestrator.py's tool registrations):
#   draft_email, send_draft, set_email_signature
# ALERTS (API only):
#   check_new_mail   -- History API delta: only what arrived since the last
#                       check. Replaced the old every-5-minutes rescan of
#                       14 days of mail (removed 2026-09-27).
#   check_missed_replies -- hourly, time-based ("3 days, no reply").
#
# Rules this module enforces, not the model:
#   - Reading/alerting NEVER marks mail read. The only thing that marks an
#     email read is FRED sending a reply to it (send_draft).
#   - Links in email text are redacted (see gmail_imap.redact_links).
#   - Attachment filenames are sanitised before touching the disk.

import base64
import email
import email.header
import email.policy
import email.utils
import json
import re
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from config.settings import (
    DATA_DIR,
    GMAIL_ATTACHMENT_DIR,
    GMAIL_MISSED_REPLY_DAYS,
    GMAIL_OAUTH_TOKEN_PATH,
    GMAIL_SEARCH_LIMIT,
    GMAIL_TIER_OVERRIDES_PATH,
)
from tools import gmail_imap
from tools.gmail_imap import _body_text, _decode_header
from utils import event_log

SCOPE_MODIFY = "https://www.googleapis.com/auth/gmail.modify"      # read + mark read
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"    # drafts + send
SCOPE_SETTINGS = "https://www.googleapis.com/auth/gmail.settings.basic"  # signature
SCOPES = [SCOPE_MODIFY, SCOPE_COMPOSE, SCOPE_SETTINGS]
BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

MISSED_REPLY_SEEN_PATH = DATA_DIR / "gmail_missed_reply_seen.json"
HISTORY_STATE_PATH = DATA_DIR / "gmail_history_state.json"
PENDING_DRAFT_PATH = DATA_DIR / "gmail_pending_draft.json"

_NON_PRIMARY = {"CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}
_NEEDS_SIGN_IN = "Gmail needs one more sign-in for that, sir -- run setup_gmail_oauth.py."

_MONTH_NAMES = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    "aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
# Vatsal's call 2026-09-27: FRED only touches the Primary and Updates tabs.
# Social/Promotions/Forums mail is never searched, read, labelled or opened.
_EXCLUDED_TABS = {"CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS", "CATEGORY_FORUMS"}
_ALLOWED_TABS_QUERY = "(category:primary OR category:updates)"

# ponytail: naive phrase scan -- no year rollover / timezone handling.
# Tuned 2026-09-27 on 5 days of real mail: "due to ..." isn't a deadline,
# and bare "today" was only ever delivery-status noise.
_DEADLINE_PATTERNS = [
    re.compile(r"\bdue\s+(?!to\b)(?:by\s+|on\s+)?\w[\w ,]{0,25}", re.IGNORECASE),
    re.compile(r"\bdeadline[:\s]+\w[\w ,]{0,25}", re.IGNORECASE),
    re.compile(r"\bby\s+(tomorrow|today|next week|end of \w+)", re.IGNORECASE),
    re.compile(r"\btomorrow\b", re.IGNORECASE),
    re.compile(r"\bin\s+\d+\s+(day|days|week|weeks)\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_NAMES})\.?\s*\d{{0,4}}\b", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]


# ---------------------------------------------------------------- plumbing

def _session(need: str = None):
    """AuthorizedSession, or None if setup_gmail_oauth.py never ran.
    `need`: a scope the call requires -- raises if the saved token predates
    it (Vatsal re-runs the setup script once to grant it)."""
    if not GMAIL_OAUTH_TOKEN_PATH.exists():
        if need:
            raise PermissionError(_NEEDS_SIGN_IN)
        return None
    creds = Credentials.from_authorized_user_file(str(GMAIL_OAUTH_TOKEN_PATH))
    if need and need not in (creds.scopes or []):
        raise PermissionError(_NEEDS_SIGN_IN)
    if not creds.valid:
        creds.refresh(Request())
        GMAIL_OAUTH_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return AuthorizedSession(creds)


def _call(s, method: str, path: str, body: dict = None, **params) -> dict:
    # Gmail rate-limits bursts with 429 or a 403 "rateLimitExceeded" (hit
    # live 2026-09-27 on a few hundred back-to-back reads): back off, retry.
    for wait in (1, 2, 4, None):
        r = s.request(method, f"{BASE}/{path}", json=body, params=params, timeout=30)
        limited = r.status_code == 429 or (r.status_code == 403 and "ateLimit" in r.text)
        if not limited or wait is None:
            break
        time.sleep(wait)
    r.raise_for_status()
    return r.json() if r.content else {}


def _get_json(s, path: str, **params) -> dict:
    return _call(s, "GET", path, **params)


def _list(s, query: str, limit: int = 500) -> list:
    """Message ids matching a Gmail search query, newest first."""
    ids, token = [], None
    while len(ids) < limit:
        page = _get_json(s, "messages", q=query, maxResults=min(100, limit - len(ids)), pageToken=token)
        ids += [m["id"] for m in page.get("messages", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    return ids


def _get(s, msg_id: str):
    """(metadata with id/threadId/labelIds/internalDate, parsed message)."""
    meta = _get_json(s, f"messages/{msg_id}", format="raw")
    return meta, email.message_from_bytes(base64.urlsafe_b64decode(meta["raw"]))


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _find(query: str, limit: int) -> list:
    """[(meta or None, message)] newest first, Primary/Updates only. API
    first; on any API failure, the IMAP fallback (meta is None there)."""
    query = f"({query}) {_ALLOWED_TABS_QUERY}"
    try:
        s = _session()
        if s is not None:
            return [_get(s, gid) for gid in _list(s, query, limit)]
    except Exception as e:
        event_log.log_error("gmail_api_read_fallback_to_imap", e)
    return [(None, m) for m in gmail_imap.search(query, limit)]


# ---------------------------------------------------------------- formatting

def _sender(msg) -> str:
    name, addr = email.utils.parseaddr(_decode_header(msg.get("From", "")))
    return name or addr or "unknown sender"


def _when(meta, msg) -> datetime:
    if meta:
        return datetime.fromtimestamp(int(meta["internalDate"]) / 1000)
    try:
        return email.utils.parsedate_to_datetime(msg.get("Date", "")).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.now()


def _stamp(meta, msg) -> str:
    when = _when(meta, msg)
    return when.strftime("%H:%M") if when.date() == datetime.now().date() else when.strftime("%d %b")


def _line(meta, msg) -> str:
    return f'{_sender(msg)} -- "{_decode_header(msg.get("Subject", "")) or "(no subject)"}" ({_stamp(meta, msg)})'


def _attachments(msg) -> list:
    return [p for p in msg.walk() if p.get_filename()]


def _deadline_phrase(text: str):
    for pattern in _DEADLINE_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0).strip()
    return None


# ---------------------------------------------------------------- read tools

def unread_today() -> str:
    """Today's unread Primary email -- the only "list my mail" there is."""
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    query = f"in:inbox category:primary is:unread after:{int(midnight.timestamp())}"
    try:
        found = _find(query, GMAIL_SEARCH_LIMIT + 1)
    except Exception as e:
        return f"Couldn't reach Gmail: {e}"
    if not found:
        return "No unread email today, sir."
    lines = [_line(meta, msg) for meta, msg in found[:GMAIL_SEARCH_LIMIT]]
    more = " (and more)" if len(found) > GMAIL_SEARCH_LIMIT else ""
    return f"{len(lines)} unread today{more}:\n" + "\n".join(f"- {line}" for line in lines)


def search_email(query: str) -> str:
    """Gmail search syntax, straight through: from:, to:, subject:,
    has:attachment, newer_than:2d, is:unread, label:, ..."""
    query = (query or "").strip()
    if not query:
        return "What should I search for?"
    try:
        found = _find(query, GMAIL_SEARCH_LIMIT)
    except Exception as e:
        return f"Couldn't reach Gmail: {e}"
    if not found:
        return f"No email matches '{query}'."
    return f"{len(found)} match(es) for '{query}', newest first:\n" + "\n".join(
        f"- {_line(meta, msg)}" for meta, msg in found
    )


def read_email(query: str) -> str:
    """Full text of the newest email matching a Gmail search query.
    Doesn't mark it read."""
    query = (query or "").strip()
    if not query:
        return "Which email? Give me a sender, subject or search."
    try:
        found = _find(query, 1)
    except Exception as e:
        return f"Couldn't reach Gmail: {e}"
    if not found:
        return f"No email matches '{query}'."
    meta, msg = found[0]
    names = [_decode_header(p.get_filename()) for p in _attachments(msg)]
    body = _body_text(msg).strip()
    # ponytail: hard 3000-char cut, a long thread's quoted history gets truncated.
    if len(body) > 3000:
        body = body[:3000] + " [...]"
    return (
        f"From: {_decode_header(msg.get('From', ''))}\n"
        f"Subject: {_decode_header(msg.get('Subject', ''))}\n"
        f"When: {_when(meta, msg):%d %b %Y %H:%M}\n"
        + (f"Attachments: {', '.join(names)}\n" if names else "")
        + f"\n{body}"
    )


_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe_filename(name: str) -> str:
    """An attachment's name is attacker-controlled: no directories, no
    path separators, no Windows device names."""
    name = _BAD_FILENAME_CHARS.sub("_", Path(name or "").name).strip(" .")[:150] or "attachment"
    if Path(name).stem.upper() in _WINDOWS_RESERVED:
        name = "_" + name
    return name


def _unique(path: Path) -> Path:
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def _pick_attachments(query: str, filename: str):
    """(message, [parts]) for the newest email matching `query`, filtered
    to attachments whose name contains `filename` (all of them if empty)."""
    found = _find(query, 1)
    if not found:
        return None, []
    _, msg = found[0]
    parts = [p for p in _attachments(msg)
             if not filename or filename.lower() in _decode_header(p.get_filename()).lower()]
    return msg, parts


def attachment_preview(query: str, filename: str = "") -> tuple:
    """(ok, confirmation question) -- what would be saved, before Vatsal says yes."""
    msg, parts = _pick_attachments(query, filename)
    if msg is None:
        return False, f"No email matches '{query}'."
    if not parts:
        return False, "That email has no matching attachment."
    files = ", ".join(
        f"{_safe_filename(_decode_header(p.get_filename()))} "
        f"({len(p.get_payload(decode=True) or b'') // 1024} KB)" for p in parts
    )
    return True, f"Save {files} from {_sender(msg)} to {GMAIL_ATTACHMENT_DIR}? (yes/no)"


def download_attachment(query: str, filename: str = "") -> str:
    """Saves attachments from the newest matching email. Confirmation-gated
    (registered destructive) -- attachment_preview is what Vatsal sees."""
    try:
        msg, parts = _pick_attachments(query, filename)
    except Exception as e:
        return f"Couldn't reach Gmail: {e}"
    if msg is None or not parts:
        return "Nothing to save -- no matching email or attachment."
    GMAIL_ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for part in parts:
        path = _unique(GMAIL_ATTACHMENT_DIR / _safe_filename(_decode_header(part.get_filename())))
        path.write_bytes(part.get_payload(decode=True) or b"")
        saved.append(path.name)
    return f"Saved {', '.join(saved)} to {GMAIL_ATTACHMENT_DIR}."


# ---------------------------------------------------------------- write tools

def _resolve_address(s, to: str) -> str:
    """An address as-is; a name -> the From address of their newest email."""
    to = (to or "").strip()
    if "@" in to:
        return to
    name = to.replace('"', "")
    ids = _list(s, f'from:"{name}" {_ALLOWED_TABS_QUERY}', 1)
    if not ids:
        raise LookupError(f"I don't have an email address for {to}.")
    _, msg = _get(s, ids[0])
    return email.utils.parseaddr(msg.get("From", ""))[1]


def draft_email(to: str = "", subject: str = "", body: str = "", reply_to: str = "") -> str:
    """Save a Gmail draft -- nothing is sent. `reply_to` is a search query
    for the email being answered; it sets recipient, subject and thread."""
    body = (body or "").strip()
    if not body:
        return "What should the email say?"
    try:
        s = _session(need=SCOPE_COMPOSE)
        message = EmailMessage()
        thread_id = original_id = None
        if reply_to:
            ids = _list(s, f"({reply_to}) {_ALLOWED_TABS_QUERY}", 1)
            if not ids:
                return f"No email matches '{reply_to}' to reply to."
            meta, original = _get(s, ids[0])
            thread_id, original_id = meta["threadId"], meta["id"]
            to = email.utils.parseaddr(original.get("Reply-To") or original.get("From", ""))[1]
            old_subject = str(email.header.make_header(email.header.decode_header(original.get("Subject", ""))))
            subject = old_subject if old_subject.lower().startswith("re:") else f"Re: {old_subject}"
            if original.get("Message-ID"):
                message["In-Reply-To"] = original["Message-ID"]
                message["References"] = f'{original.get("References", "")} {original["Message-ID"]}'.strip()
        else:
            if not to:
                return "Who's it to?"
            to = _resolve_address(s, to)
        message["To"] = to
        message["Subject"] = subject or "(no subject)"
        message.set_content(body)
        payload = {"message": {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}}
        if thread_id:
            payload["message"]["threadId"] = thread_id
        draft = _call(s, "POST", "drafts", payload)
    except (PermissionError, LookupError) as e:
        return str(e)
    except Exception as e:
        return f"Couldn't save the draft: {e}"

    _save_json(PENDING_DRAFT_PATH, {"draft_id": draft["id"], "reply_to_id": original_id})
    return f'Draft saved to {to}: "{message["Subject"]}". Say "send the draft" to send it.'


def draft_preview() -> tuple:
    """(ok, confirmation question) built from the draft as it is IN GMAIL
    right now -- so an edit made in the Gmail app is what gets confirmed."""
    pending = _load_json(PENDING_DRAFT_PATH, None)
    if not pending:
        return False, "There's no draft waiting to be sent."
    try:
        s = _session(need=SCOPE_COMPOSE)
        draft = _get_json(s, f"drafts/{pending['draft_id']}", format="raw")
    except PermissionError as e:
        return False, str(e)
    except Exception:
        return False, "That draft is gone from Gmail -- nothing to send."
    msg = email.message_from_bytes(base64.urlsafe_b64decode(draft["message"]["raw"]), policy=email.policy.default)
    body = msg.get_body(("plain",))
    text = (body.get_content() if body else "").strip()
    return True, (
        f"Send this email to {msg.get('To', '')}?\n"
        f"Subject: {msg.get('Subject', '')}\n{text[:500]}\n(yes/no)"
    )


def send_draft() -> str:
    """Sends FRED's pending draft. Confirmation-gated (registered
    destructive). A reply also marks the email it answers as read --
    the one and only place FRED marks anything read."""
    pending = _load_json(PENDING_DRAFT_PATH, None)
    if not pending:
        return "There's no draft waiting to be sent."
    try:
        s = _session(need=SCOPE_COMPOSE)
        _call(s, "POST", "drafts/send", {"id": pending["draft_id"]})
    except PermissionError as e:
        return str(e)
    except Exception as e:
        return f"Couldn't send it: {e}"
    PENDING_DRAFT_PATH.unlink(missing_ok=True)
    if pending.get("reply_to_id"):
        try:
            _call(s, "POST", f"messages/{pending['reply_to_id']}/modify", {"removeLabelIds": ["UNREAD"]})
        except Exception as e:
            event_log.log_error("gmail_mark_read_after_reply", e)
    return "Sent, sir."


def signature_preview(text: str) -> tuple:
    return True, f'Change your Gmail signature to:\n"{text}"\n(yes/no)'


def set_email_signature(text: str = "") -> str:
    """Replaces the signature on Vatsal's primary address. Confirmation-gated."""
    try:
        s = _session(need=SCOPE_SETTINGS)
        send_as = _get_json(s, "settings/sendAs").get("sendAs", [])
        primary = next(a for a in send_as if a.get("isPrimary"))
        html = "<br>".join(
            line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            for line in (text or "").splitlines()
        )
        _call(s, "PATCH", f"settings/sendAs/{primary['sendAsEmail']}", {"signature": html})
    except PermissionError as e:
        return str(e)
    except Exception as e:
        return f"Couldn't change the signature: {e}"
    return "Signature updated." if text else "Signature cleared."


# ---------------------------------------------------------------- alerts

def _emailed_first(s, address: str) -> bool:
    """VVIP proxy: has Vatsal ever sent TO this address?"""
    return bool(_get_json(s, "messages", q=f"in:sent to:{address}", maxResults=1).get("messages"))


_AUTOMATED_SENDER = ("noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
                     "do_not_reply", "return", "bounce", "mailer-daemon", "notification")


def _is_automated(address: str) -> bool:
    """A sender nobody can (or should) reply to -- never VVIP, never a missed reply."""
    return any(word in address.split("@")[0].lower() for word in _AUTOMATED_SENDER)


def _tier_overrides() -> list:
    """[(needle, tier)] from the vault's email-tiers.md, hand-edited."""
    overrides, tier = [], None
    try:
        lines = GMAIL_TIER_OVERRIDES_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if line.startswith("## "):
            tier = line[3:].strip().lower()
        elif line.startswith("- ") and tier in TIER_LABELS:
            overrides.append((line[2:].strip().lower(), tier))
    return overrides


def _tier(s, msg, cache: dict, overrides: list = ()) -> str:
    """Hand overrides first, then: useless (List-Unsubscribe) / vvip (a
    real person Vatsal has emailed before) / basic. `cache`: address ->
    emailed-first, per run."""
    sender = _decode_header(msg.get("From", "")).lower()
    for needle, tier in overrides:
        if needle in sender:
            return tier
    if msg.get("List-Unsubscribe"):
        return "useless"
    address = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    if not address or _is_automated(address):
        return "basic"
    if address not in cache:
        cache[address] = _emailed_first(s, address)
    return "vvip" if cache[address] else "basic"


TIER_LABELS = {"vvip": "FRED/VVIP", "basic": "FRED/Basic", "useless": "FRED/Useless"}


def _tier_label_ids(s) -> dict:
    """tier -> Gmail label id, creating the FRED/ labels if Vatsal deleted them."""
    existing = {l["name"]: l["id"] for l in _get_json(s, "labels").get("labels", [])}
    for name in ("FRED", *TIER_LABELS.values()):
        if name not in existing:
            existing[name] = _call(s, "POST", "labels", {
                "name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"})["id"]
    return {tier: existing[name] for tier, name in TIER_LABELS.items()}


def _history_added(s, start: str):
    """(new INBOX message ids since `start`, oldest first; newest historyId)."""
    ids, token, latest = [], None, start
    while True:
        page = _get_json(s, "history", startHistoryId=start, historyTypes="messageAdded",
                         labelId="INBOX", pageToken=token)
        latest = page.get("historyId", latest)
        for record in page.get("history", []):
            for added in record.get("messagesAdded", []):
                if added["message"]["id"] not in ids:
                    ids.append(added["message"]["id"])
        token = page.get("nextPageToken")
        if not token:
            return ids, latest


def check_new_mail() -> list:
    """Labels every new Primary/Updates email FRED/VVIP, FRED/Basic or
    FRED/Useless -- labels only, never archived, never marked read -- and
    returns [(tier, summary)] to alert on: Primary only, never useless.
    vvip -> urgent; basic -> normal gate. A date-like phrase in the body is
    appended. Other tabs are skipped on labels alone, never opened.
    Raises on API failure; the caller reports an outage."""
    if not GMAIL_OAUTH_TOKEN_PATH.exists():
        return []
    s = _session(need=SCOPE_MODIFY)
    state = _load_json(HISTORY_STATE_PATH, {})
    if not state.get("history_id"):
        # First run: baseline only, never alert on the existing backlog.
        _save_json(HISTORY_STATE_PATH, {"history_id": _get_json(s, "profile")["historyId"]})
        return []
    try:
        ids, latest = _history_added(s, state["history_id"])
    except Exception as e:
        if getattr(getattr(e, "response", None), "status_code", None) != 404:
            raise
        # Cursor too old (Gmail keeps ~a week of history): re-baseline.
        _save_json(HISTORY_STATE_PATH, {"history_id": _get_json(s, "profile")["historyId"]})
        return []

    results, cache, overrides = [], {}, _tier_overrides()
    by_tier = {tier: [] for tier in TIER_LABELS}
    for gid in ids:
        try:
            labels = set(_get_json(s, f"messages/{gid}", format="minimal").get("labelIds", []))
            if "SENT" in labels or "INBOX" not in labels or labels & _EXCLUDED_TABS:
                continue
            meta, msg = _get(s, gid)
        except Exception:
            continue  # deleted between arriving and now
        tier = _tier(s, msg, cache, overrides)
        by_tier[tier].append(gid)
        if tier == "useless" or labels & _NON_PRIMARY:
            continue
        summary = f'New email from {_sender(msg)}: "{_decode_header(msg.get("Subject", ""))}"'
        phrase = _deadline_phrase(_body_text(msg))
        if phrase:
            summary += f" -- mentions: {phrase}"
        results.append((tier, summary))

    if any(by_tier.values()):
        label_ids = _tier_label_ids(s)
        for tier, gids in by_tier.items():
            if gids:
                _call(s, "POST", "messages/batchModify", {"ids": gids, "addLabelIds": [label_ids[tier]]})

    _save_json(HISTORY_STATE_PATH, {"history_id": latest})
    return results


def _replied_after(s, thread_id: str, received_ms: int) -> bool:
    """Did Vatsal send anything in this thread AFTER this email arrived?"""
    thread = _get_json(s, f"threads/{thread_id}", format="minimal")
    return any(
        "SENT" in m.get("labelIds", []) and int(m["internalDate"]) > received_ms
        for m in thread.get("messages", [])
    )


def check_missed_replies() -> str:
    """One Primary email >= GMAIL_MISSED_REPLY_DAYS old with no reply from
    Vatsal since, or "". Only VVIP senders count (a real person he has
    emailed before) -- tuned 2026-09-27 on real mail, where "no reply" to
    shop, broker and security-alert notifications was pure noise. Each email is surfaced
    at most once. Raises on API failure."""
    s = _session()
    if s is None:
        return ""
    days = GMAIL_MISSED_REPLY_DAYS
    query = f"in:inbox category:primary -from:me older_than:{days}d newer_than:{days + 4}d"
    seen = set(_load_json(MISSED_REPLY_SEEN_PATH, []))
    cache, overrides = {}, _tier_overrides()
    missed = None
    for gid in _list(s, query):
        meta, msg = _get(s, gid)
        msg_id = msg.get("Message-ID", "")
        if not msg_id or msg_id in seen:
            continue
        seen.add(msg_id)
        if _tier(s, msg, cache, overrides) != "vvip" or _replied_after(s, meta["threadId"], int(meta["internalDate"])):
            continue
        missed = (msg, (datetime.now() - _when(meta, msg)).days)
        break
    _save_json(MISSED_REPLY_SEEN_PATH, sorted(seen)[-500:])
    if not missed:
        return ""
    msg, age = missed
    return f'{_sender(msg)} emailed "{_decode_header(msg.get("Subject", ""))}" {age} day(s) ago, still no reply'
