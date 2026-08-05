# Core/tools/daily_tasks.py
#
# A same-day scratch task list, backed by the vault's daily note
# (VAULT_DIR/daily/<month>/<day>.md) — the same file and header format
# tools/session_summary.py already writes for its recap.
#
# Confirmed 2026-08-03: before this, "add to today's tasks" and "mark X
# complete" had no tool behind them at all. The model was narrating a
# save that never happened — active-priorities.md hadn't been touched
# since 2026-08-01, and no daily/2026-08 notes existed, despite FRED
# having said "your goals for today are logged" on more than one
# occasion. This gives it something real to call.
#
# No confirmation gate, unlike session_summary.py's recap: that writes
# free-form prose to a file rules.md treats carefully, so it shows the
# text before saving. This only ever appends one short checkbox line at
# a time to a same-day scratch section — asking first would be friction
# on every single item for something this low-stakes and disposable.

import re
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import VAULT_DIR

_TASKS_HEADING = "## Tasks"

# Due dates written the way they actually get spoken and saved — the
# real line that motivated this, from daily/2026-08/2026-08-03.md, is:
#
#     - [ ] Chemistry journal completion — due Thursday in school
#
# Nothing parsed that, so a task with a real deadline was
# indistinguishable from one without, and the deadline check in
# proactive_checks.py only ever read a `deadline:` FRONTMATTER field
# that no vault file has ever used. The task said Thursday; FRED had no
# idea Thursday meant anything.
_DUE_RE = re.compile(
    r"\bdue\s+(?:on\s+|by\s+)?"
    r"(?P<when>tomorrow|today|"
    r"\d{4}-\d{2}-\d{2}|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def parse_due(text: str, today: datetime = None):
    """
    The date a task line says it's due, or None.

    A bare weekday means the NEXT occurrence including today — "due
    Thursday" said on Thursday morning is due right now, not in seven
    days. That matches how the phrase is used in practice; the
    alternative (always forward-looking, minimum one day) would silence
    the warning on exactly the day it matters most.
    """
    if not text:
        return None

    match = _DUE_RE.search(text)
    if not match:
        return None

    today = (today or datetime.now()).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    when = match.group("when").lower()

    if when == "today":
        return today
    if when == "tomorrow":
        return today + timedelta(days=1)

    if when in _WEEKDAY_INDEX:
        offset = (_WEEKDAY_INDEX[when] - today.weekday()) % 7
        return today + timedelta(days=offset)

    try:
        return datetime.strptime(when, "%Y-%m-%d")
    except ValueError:
        return None


def _daily_note_path(day: str = None) -> Path:
    day = day or datetime.now().strftime("%Y-%m-%d")
    month = day[:7]
    return VAULT_DIR / "daily" / month / f"{day}.md"


def _ensure_note(path: Path, day: str):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"---\ntype: log\nstatus: active\nupdated: {day}\n---\n\n"
        f"# {datetime.strptime(day, '%Y-%m-%d').strftime('%A, %B %d, %Y')}\n"
    )
    path.write_text(header, encoding="utf-8")


def _split_tasks_section(lines):
    """(before, tasks, after) — `before`/`after` are everything outside
    the Tasks section, with the heading itself stripped out of `before`
    either way; _rewrite() always puts it back in the same spot. That
    means the caller never has to care whether the heading existed yet."""
    if _TASKS_HEADING not in lines:
        return lines, [], []
    i = lines.index(_TASKS_HEADING)
    j = i + 1
    while j < len(lines) and not lines[j].startswith("## "):
        j += 1
    tasks = [ln for ln in lines[i + 1:j] if ln.strip()]
    return lines[:i], tasks, lines[j:]


def _read_lines(path: Path):
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _rewrite(path: Path, before, tasks, after):
    parts = before + ["", _TASKS_HEADING, ""] + tasks
    if after:
        parts += [""] + after
    path.write_text("\n".join(parts).strip("\n") + "\n", encoding="utf-8")


def _task_line(text: str, done: bool = False) -> str:
    return f"- [{'x' if done else ' '}] {text}"


def _parse_task_line(line: str):
    """(done, text) or None if this isn't a checkbox line."""
    if not (line.startswith("- [ ] ") or line.startswith("- [x] ")):
        return None
    return line[3] == "x", line[6:]


def add_task(text: str, day: str = None) -> str:
    """Append a task to today's daily note, creating the note and/or
    the Tasks section if this is the first one today."""
    text = text.strip()
    if not text:
        return "No task text given."

    day = day or datetime.now().strftime("%Y-%m-%d")
    path = _daily_note_path(day)
    _ensure_note(path, day)

    before, tasks, after = _split_tasks_section(_read_lines(path))
    tasks.append(_task_line(text))
    _rewrite(path, before, tasks, after)

    return f"Added to today's tasks: {text}"


def _all_tasks(day: str = None) -> dict:
    """
    {task_text: (done, origin_day)} across every daily note up to and
    including `day`, later days winning on status. `origin_day` is the
    note the winning entry came from — list_tasks needs it to say which
    tasks are today's and which rolled forward, because a merged list
    that doesn't distinguish them reads as "today's tasks" to FRED and
    to Vatsal alike (confirmed 2026-08-05: yesterday's list was spoken
    as today's).

    Shared by list_tasks and open_due_tasks so the roll-forward rule
    (see list_tasks) has exactly one implementation — a proactive
    deadline warning that used a different notion of "still open" from
    the spoken task list would be a bug nobody would spot for weeks.
    """
    day = day or datetime.now().strftime("%Y-%m-%d")
    month_dir = _daily_note_path(day).parent

    by_text = {}
    if month_dir.is_dir():
        for path in sorted(month_dir.glob("*.md")):
            if path.stem > day:
                continue
            _, tasks, _ = _split_tasks_section(_read_lines(path))
            for parsed in (_parse_task_line(t) for t in tasks):
                if parsed:
                    done, text = parsed
                    by_text[text] = (done, path.stem)
    return by_text


def open_due_tasks(day: str = None, within_days: int = 2) -> list:
    """
    [(due_date, text)] for still-open tasks due within `within_days`,
    soonest first. Overdue tasks are included — a missed deadline is
    more worth raising than an upcoming one, not less.
    """
    today = datetime.strptime(
        day or datetime.now().strftime("%Y-%m-%d"), "%Y-%m-%d"
    )

    due = []
    for text, (done, _origin) in _all_tasks(day).items():
        if done:
            continue
        deadline = parse_due(text, today=today)
        if deadline is None:
            continue
        if (deadline - today).days <= within_days:
            due.append((deadline, text))

    return sorted(due)


def list_tasks(day: str = None) -> str:
    """Today's tasks, plus any still-open task from earlier in the
    month rolled forward — confirmed 2026-08-04: a chemistry journal
    task logged 2026-08-03 (due Thursday) was invisible the next day
    because this only ever looked at `day`'s note. Later days win on
    conflicting status, so completing a rolled-forward task today
    still marks it Done.

    Each line carries the day it came from, and the header says outright
    whether today's note exists — without that, a roll-forward-only list
    is indistinguishable from a list of today's own tasks."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    by_text = _all_tasks(day)
    today_exists = _daily_note_path(day).exists()

    if not by_text:
        return (
            f"No tasks logged for {day}"
            + ("." if today_exists else f"; no daily note for {day} yet.")
        )
    # Not bracket-tagged ("[open] ...") on purpose — clean_for_speech()
    # (Core/audio/tts_kokoro.py) strips any [bracket] as machine noise
    # like list_scheduled()'s job ids, which would silently swallow the
    # done/open status right along with it before this ever reached
    # speech. Plain words survive that pass.
    header = (
        f"Tasks as of {day}:" if today_exists
        else f"No daily note for {day} yet; these carried over from earlier days:"
    )
    lines = [
        f"{'Done' if done else 'Open'}: {text}"
        + ("" if origin == day else f" (from {origin})")
        for text, (done, origin) in by_text.items()
    ]
    return "\n".join([header] + lines)


def complete_task(match: str, done: bool = True, day: str = None) -> str:
    """Toggle the first task whose text contains `match`, case-insensitive."""
    match = match.strip().lower()
    if not match:
        return "No task named."

    day = day or datetime.now().strftime("%Y-%m-%d")
    path = _daily_note_path(day)
    before, tasks, after = _split_tasks_section(_read_lines(path))

    for i, line in enumerate(tasks):
        parsed = _parse_task_line(line)
        if parsed and match in parsed[1].lower():
            tasks[i] = _task_line(parsed[1], done=done)
            _rewrite(path, before, tasks, after)
            return f'Marked "{parsed[1]}" as {"complete" if done else "incomplete"}.'

    return f'No task matching "{match}" found for today.'
