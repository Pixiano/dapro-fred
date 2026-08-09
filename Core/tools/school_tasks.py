# Core/tools/school_tasks.py
#
# School homework, projects and events — one persistent file
# (VAULT_DIR/school/work.md), not sharded by day like daily_tasks.py.
# A school item's whole point is that it needs to be found again days
# later by SUBJECT, not by which day it happened to be logged.
#
# Built 2026-08-09: Vatsal named unreliable school-deadline tracking as
# the one thing that would make him use FRED daily, done properly —
# capture "3 questions in Geography, due in 3 days" precisely, answer
# "what's left for tomorrow" from the file (never from conversation
# memory), track progress, and carry a still-open item into a proactive
# "did you finish it, or find a workaround" the day it's due.
#
# The LLM's job is entity extraction via the tool call's own arguments
# (subject/count/due/kind) — that's what tool-calling already is, and
# far more reliable than asking a small local model to freehand-parse a
# whole sentence. This module's job is turning those arguments into one
# deterministic line and back, never guessing at prose.

import re
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import VAULT_DIR
from orchestrator.scheduler import parse_when, describe_when

_ITEMS_HEADING = "## Items"
_KINDS = ("homework", "project", "event")


# =========================================================
# DATE PARSING
# =========================================================
#
# Neither existing parser fits: daily_tasks.parse_due requires the
# literal word "due" embedded in a task sentence (this is a dedicated
# `due` tool argument, not a sentence to search), and
# orchestrator.scheduler.parse_when always resolves to an exact minute
# (it's built for reminders, which need one). This covers the bare-date
# case neither owns — "in 3 days" with no time component — and defers
# to scheduler.parse_when whenever a clock time is actually given (see
# _resolve_when), reusing its weekday/tomorrow/ISO handling rather than
# duplicating it.

_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_TODAY_RE = re.compile(r"\btoday\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_NEXT_WEEK_RE = re.compile(r"\bnext\s+week\b", re.IGNORECASE)
_RELATIVE_DAYS_RE = re.compile(
    r"\bin\s+(a|an|\d+)\s*(day|days|week|weeks)\b", re.IGNORECASE
)
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_WEEKDAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

# A school notice reads dates as "13 August 2026", not ISO — confirmed
# gap: parse_due_date("13 August 2026") returned None the first time a
# real one was logged (2026-08-09). Day-month is the primary order
# (matches how Vatsal actually writes it, CBSE/Indian convention);
# month-day is included too since a transcribed or copy-pasted date
# could plausibly arrive that way as well. Year is optional in either
# order — inferred the same way the weekday branch below already
# infers a year-less date: if it would be in the past, it isn't what
# was meant, so roll forward.
_MONTH_INDEX = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_NAMES_RE = "|".join(sorted(_MONTH_INDEX, key=len, reverse=True))
_DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES_RE})\.?(?:\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)
_MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_NAMES_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?(?:\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)


def _named_month_date(text: str, now: datetime):
    for rx, day_first in ((_DAY_MONTH_RE, True), (_MONTH_DAY_RE, False)):
        m = rx.search(text)
        if not m:
            continue
        day, month_name, year = (m.group(1), m.group(2), m.group(3)) if day_first \
            else (m.group(2), m.group(1), m.group(3))
        try:
            candidate = datetime(int(year) if year else now.year, _MONTH_INDEX[month_name.lower()], int(day))
        except ValueError:
            continue
        if not year and candidate.date() < now.date():
            candidate = candidate.replace(year=candidate.year + 1)
        return candidate
    return None


def parse_due_date(text: str, now: datetime = None):
    """
    A bare due-DATE phrase -> midnight of that date, or None.

    "today", "tomorrow", "next week", "in 3 days", "in 2 weeks", a
    weekday name, or an ISO date. No clock-time handling — that's
    scheduler.parse_when's job, reused directly by _resolve_when
    whenever a `time` argument is also given.
    """
    if not text or not str(text).strip():
        return None

    text = str(text).strip().lower()
    now = (now or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)

    if _TODAY_RE.search(text):
        return now
    if _TOMORROW_RE.search(text):
        return now + timedelta(days=1)
    if _NEXT_WEEK_RE.search(text):
        return now + timedelta(days=7)

    relative = _RELATIVE_DAYS_RE.search(text)
    if relative:
        amount = 1 if relative.group(1) in ("a", "an") else int(relative.group(1))
        unit_days = 7 if relative.group(2).lower().startswith("week") else 1
        return now + timedelta(days=amount * unit_days)

    iso = _ISO_RE.search(text)
    if iso:
        try:
            return datetime(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None

    named_month = _named_month_date(text, now)
    if named_month is not None:
        return named_month

    weekday = _WEEKDAY_RE.search(text)
    if weekday:
        target = _WEEKDAY_INDEX[weekday.group(1).lower()]
        offset = (target - now.weekday()) % 7
        return now + timedelta(days=offset)

    return None


def _resolve_when(due: str, time: str = "", now: datetime = None):
    """
    (datetime, has_time) for the combined due+time arguments, or
    (None, False) if `due` didn't parse at all.

    Delegates to scheduler.parse_when for the WITH-TIME case — it
    already owns date+time resolution, including "tomorrow 9am" and
    "wednesday 6pm", exactly what an event's start needs — and only
    falls back to parse_due_date if that combined phrase somehow
    doesn't parse (e.g. `time` was junk) but `due` alone is good.
    """
    if time and str(time).strip():
        when = parse_when(f"{due} {time}".strip(), now=now)
        if when is not None:
            return when, True

    return parse_due_date(due, now=now), False


def _describe_date(d: datetime, now: datetime = None) -> str:
    """Human phrasing for a date with no meaningful clock time."""
    now = now or datetime.now()
    days = (d.date() - now.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 0:
        n = abs(days)
        return f"{d.strftime('%A %d %B')} ({n} day{'s' if n != 1 else ''} overdue)"
    return d.strftime("%A %d %B")


def _describe(item: dict, now: datetime = None) -> str:
    now = now or datetime.now()
    kind = item["kind"]
    when_str = (
        describe_when(item["when"], now) if item.get("has_time")
        else _describe_date(item["when"], now)
    )
    anchor = "starts" if kind == "event" else "due"

    head = item["subject"]
    if item.get("detail"):
        head += f", {item['detail']}"

    parts = [head, f"{anchor} {when_str}"]

    total = item.get("total_count")
    if total:
        parts.append(f"{item.get('done_count', 0)} of {total} done")

    if kind == "event" and item.get("prep_minutes") and item.get("has_time"):
        prep_start = item["when"] - timedelta(minutes=item["prep_minutes"])
        parts.append(f"start getting ready {describe_when(prep_start, now)}")

    if item.get("next_step"):
        parts.append(f"next: {item['next_step']}")

    if item.get("note"):
        parts.append(item["note"])

    return ", ".join(parts) + "."


# =========================================================
# FILE I/O — same read/split/rewrite shape as daily_tasks.py, sized for
# one persistent file instead of one-per-day.
# =========================================================

def _path() -> Path:
    return VAULT_DIR / "school" / "work.md"


def _ensure_file(path: Path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: log\nstatus: active\n---\n\n# School\n", encoding="utf-8")


def _read_lines(path: Path):
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _split_items(lines):
    """(before, item_lines, after) — mirrors
    daily_tasks._split_tasks_section's contract exactly: `before`/
    `after` are everything outside the Items section, heading stripped
    out of `before` either way, so the caller never has to care whether
    the heading existed yet."""
    if _ITEMS_HEADING not in lines:
        return lines, [], []
    i = lines.index(_ITEMS_HEADING)
    j = i + 1
    while j < len(lines) and not lines[j].startswith("## "):
        j += 1
    items = [ln for ln in lines[i + 1:j] if ln.strip()]
    return lines[:i], items, lines[j:]


def _write(path: Path, before, items, after):
    parts = before + ["", _ITEMS_HEADING, ""] + items
    if after:
        parts += [""] + after
    path.write_text("\n".join(parts).strip("\n") + "\n", encoding="utf-8")


def _fmt_when(when: datetime, has_time: bool) -> str:
    return when.strftime("%Y-%m-%d %H:%M") if has_time else when.strftime("%Y-%m-%d")


def _parse_when_field(text: str):
    text = text.strip()
    for fmt, has_time in (("%Y-%m-%d %H:%M", True), ("%Y-%m-%d", False)):
        try:
            return datetime.strptime(text, fmt), has_time
        except ValueError:
            continue
    return None, False


def _serialize(item: dict) -> str:
    box = "x" if item.get("done") else " "
    fields = [
        item["kind"],
        item["subject"],
        item.get("detail", "") or "",
        "when " + _fmt_when(item["when"], item.get("has_time", False)),
    ]
    total = item.get("total_count")
    if total is not None:
        fields.append(f"progress {item.get('done_count', 0)}/{total}")
    prep = item.get("prep_minutes")
    if prep is not None:
        fields.append(f"prep {prep}")
    if item.get("next_step"):
        fields.append("next: " + item["next_step"])
    if item.get("note"):
        fields.append("note: " + item["note"])
    return f"- [{box}] " + " | ".join(fields)


def _parse_line(line: str):
    """A parsed item dict, or None for anything that isn't a well-formed
    item line — a malformed or foreign line must never crash the whole
    file read, only get silently skipped (fail-open, same convention as
    _all_tasks in daily_tasks.py)."""
    if not (line.startswith("- [ ] ") or line.startswith("- [x] ")):
        return None
    done = line[3] == "x"
    parts = [p.strip() for p in line[6:].split(" | ")]
    if len(parts) < 4:
        return None
    kind, subject, detail = parts[0], parts[1], parts[2]
    if kind not in _KINDS or not subject:
        return None

    item = {
        "kind": kind, "subject": subject, "detail": detail, "done": done,
        "when": None, "has_time": False,
        "total_count": None, "done_count": 0,
        "prep_minutes": None, "next_step": "", "note": "",
    }

    for field in parts[3:]:
        if field.startswith("when "):
            item["when"], item["has_time"] = _parse_when_field(field[5:])
        elif field.startswith("progress "):
            m = re.match(r"progress (\d+)/(\d+)", field)
            if m:
                item["done_count"], item["total_count"] = int(m.group(1)), int(m.group(2))
        elif field.startswith("prep "):
            m = re.match(r"prep (\d+)", field)
            if m:
                item["prep_minutes"] = int(m.group(1))
        elif field.startswith("next: "):
            item["next_step"] = field[6:]
        elif field.startswith("note: "):
            item["note"] = field[6:]

    return item if item["when"] is not None else None


def _load_items() -> list:
    _, lines, _ = _split_items(_read_lines(_path()))
    return [i for i in (_parse_line(ln) for ln in lines) if i is not None]


def _save_items(items: list):
    path = _path()
    _ensure_file(path)
    before, _, after = _split_items(_read_lines(path))
    # Open items first (soonest due), then done ones — the file itself
    # reads like a to-do list, not an append log.
    ordered = sorted(items, key=lambda i: (i["done"], i["when"]))
    _write(path, before, [_serialize(i) for i in ordered], after)


# =========================================================
# TOOLS
# =========================================================

def add_item(kind: str, subject: str, detail: str = "", count: int = None,
             due: str = "", time: str = "", prep_minutes: int = None,
             next_step: str = "") -> str:
    """Log one homework, project or event. One item per call — a turn
    naming two subjects needs two calls; see intent.looks_compound's
    multi-item tell for why the model gets asked again for the second
    one instead of the request silently dropping."""
    kind = (kind or "").strip().lower()
    subject = (subject or "").strip()

    if kind not in _KINDS:
        return f"\"{kind}\" isn't something I track, sir — homework, project, or event."
    if not subject:
        return "I need a subject or title to log this against, sir."

    when, has_time = _resolve_when(due, time)
    if when is None:
        return f"I couldn't work out when \"{subject}\" is due, sir — try a date or day name."

    count = int(count) if count and int(count) > 0 else None
    prep_minutes = int(prep_minutes) if prep_minutes and int(prep_minutes) > 0 else None

    if kind == "event" and prep_minutes and not has_time:
        return f"I need a start time for \"{subject}\" to work out prep, sir — what time does it start?"

    item = {
        "kind": kind, "subject": subject, "detail": (detail or "").strip(),
        "when": when, "has_time": has_time, "done": False,
        "total_count": count, "done_count": 0,
        "prep_minutes": prep_minutes,
        "next_step": (next_step or "").strip(), "note": "",
    }

    items = _load_items()
    items.append(item)
    _save_items(items)

    return "Logged, sir — " + _describe(item)


def list_items(when: str = "", kind: str = "", subject: str = "") -> str:
    """Answer any question about what's due, what's left, or progress
    on something — always read fresh from the file, never answered from
    conversation memory."""
    when = (when or "all").strip().lower()
    kind = (kind or "").strip().lower()
    subject = (subject or "").strip().lower()
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    items = [i for i in _load_items() if not i["done"]]

    if kind:
        items = [i for i in items if i["kind"] == kind]
    if subject:
        items = [i for i in items if subject in i["subject"].lower()]

    if when == "today":
        items = [i for i in items if i["when"].date() == today.date()]
    elif when == "tomorrow":
        items = [i for i in items if i["when"].date() == (today + timedelta(days=1)).date()]
    elif when == "week":
        end = (today + timedelta(days=7)).date()
        items = [i for i in items if today.date() <= i["when"].date() <= end]
    elif when == "overdue":
        items = [i for i in items if i["when"].date() < today.date()]
    # "all" (default): no date filter — every still-open item.

    items.sort(key=lambda i: i["when"])

    if not items:
        label = {
            "today": "today", "tomorrow": "tomorrow", "week": "this week",
            "overdue": "overdue",
        }.get(when, "open")
        return f"Nothing {label}, sir."

    header = f"{len(items)} {'item' if len(items) == 1 else 'items'}:"
    return "\n".join([header] + [f"- {_describe(i, now)}" for i in items])


def update_item(match: str, done: bool = None, add_progress: int = None,
                 set_progress: int = None, new_due: str = "", new_time: str = "",
                 note: str = "", next_step: str = "") -> str:
    """Update an existing item: progress, done state, reschedule, or a
    note. This is where a reply to a proactive question actually lands
    — 'did you finish the geography questions' -> the answer updates
    the record here, it never just gets acknowledged in speech and
    forgotten."""
    match = (match or "").strip().lower()
    if not match:
        return "I need something to match the item by, sir."

    items = _load_items()
    candidates = [
        i for i in items
        if match in i["subject"].lower() or match in i.get("detail", "").lower()
    ]
    if not candidates:
        return f"Nothing matching \"{match}\", sir."

    # Soonest due among matches — the one most likely just discussed.
    # `item` is the same dict object living inside `items`, so mutating
    # it below mutates it in place there too; no separate write-back.
    item = min(candidates, key=lambda i: i["when"])
    changed = False

    if add_progress is not None or set_progress is not None:
        if item.get("total_count"):
            base = item.get("done_count", 0)
            new_count = (base + int(add_progress)) if add_progress is not None else int(set_progress)
            item["done_count"] = max(0, min(item["total_count"], new_count))
            item["done"] = item["done_count"] >= item["total_count"]
        else:
            # No count to track progress against — any progress mention
            # on an uncounted item is the best signal available that
            # it's handled.
            item["done"] = True
        changed = True

    if done is not None:
        item["done"] = bool(done)
        changed = True

    if new_due:
        when, has_time = _resolve_when(new_due, new_time)
        if when is None:
            return f"I couldn't work out the new date for \"{item['subject']}\", sir."
        item["when"], item["has_time"] = when, has_time
        changed = True

    if note:
        item["note"] = note.strip()
        changed = True

    if next_step:
        item["next_step"] = next_step.strip()
        changed = True

    if not changed:
        return f"Nothing to update on \"{item['subject']}\", sir — say what changed."

    _save_items(items)
    return "Updated, sir — " + _describe(item)


# =========================================================
# PROACTIVE QUERY HELPERS — read-only, consumed by
# orchestrator/proactive_checks.py. Each returns item dicts, never
# spoken text; phrasing is the caller's job (it knows which dedup key
# and framing applies).
# =========================================================

def open_items() -> list:
    return [i for i in _load_items() if not i["done"]]


def due_within(days: int, kind_filter: str = None) -> list:
    """Open homework/project items due within `days` (overdue included),
    soonest first — the school equivalent of daily_tasks.open_due_tasks.
    Events are excluded; they have their own prep/upcoming checks."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    items = [i for i in open_items() if i["kind"] != "event"]
    if kind_filter:
        items = [i for i in items if i["kind"] == kind_filter]
    due = [i for i in items if (i["when"].date() - today.date()).days <= days]
    return sorted(due, key=lambda i: i["when"])


def events_needing_prep(now: datetime = None) -> list:
    """Events whose prep window has just opened: now is within
    [start - prep_minutes, start)."""
    now = now or datetime.now()
    return [
        i for i in open_items()
        if i["kind"] == "event" and i.get("prep_minutes") and i.get("has_time")
        and (i["when"] - timedelta(minutes=i["prep_minutes"])) <= now < i["when"]
    ]


def events_upcoming(within_hours: float = 24) -> list:
    """Events starting within `within_hours` from now, soonest first."""
    now = datetime.now()
    cutoff = now + timedelta(hours=within_hours)
    items = [
        i for i in open_items()
        if i["kind"] == "event" and now <= i["when"] <= cutoff
    ]
    return sorted(items, key=lambda i: i["when"])


def carryover_candidates(today: datetime = None) -> list:
    """Homework/project items due today or earlier, still open — the
    raw material for proactive_checks.check_school_carryover."""
    today = (today or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        i for i in open_items()
        if i["kind"] != "event" and i["when"].date() <= today.date()
    ]
