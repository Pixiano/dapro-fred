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

from datetime import datetime
from pathlib import Path

from config.settings import VAULT_DIR

_TASKS_HEADING = "## Tasks"


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


def list_tasks(day: str = None) -> str:
    day = day or datetime.now().strftime("%Y-%m-%d")
    _, tasks, _ = _split_tasks_section(_read_lines(_daily_note_path(day)))

    parsed = [p for p in (_parse_task_line(t) for t in tasks) if p]
    if not parsed:
        return "No tasks logged for today."
    # Not bracket-tagged ("[open] ...") on purpose — clean_for_speech()
    # (Core/audio/tts_kokoro.py) strips any [bracket] as machine noise
    # like list_scheduled()'s job ids, which would silently swallow the
    # done/open status right along with it before this ever reached
    # speech. Plain words survive that pass.
    return "\n".join(f"{'Done' if done else 'Open'}: {text}" for done, text in parsed)


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
