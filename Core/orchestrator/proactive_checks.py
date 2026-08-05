# Core/orchestrator/proactive_checks.py
#
# Observation B from the 2026-08-01 feedback session: the plumbing for
# unprompted speech already existed (utils/notifier.py,
# pill_app._speak_proactive) but nothing ever decided to use it outside
# of reminders. Three checks, scoped with Vatsal directly rather than
# guessed:
#
#   1. Vault staleness    — active-priorities.md untouched too long
#   2. Long session        — continuous machine use with no break
#   3. Deadline proximity  — a vault file's `deadline:` frontmatter
#
# Each fires through notify() AT MOST ONCE per stretch — persona.md is
# explicit that a second reminder is acceptable, a third is nagging, so
# every check here dedups against orchestrator/proactive_checks.py's own
# small state file rather than firing again on every interval tick.

import ctypes
import json
import re
from datetime import datetime, timedelta

from config.settings import (
    VAULT_DIR,
    ROLLOVER_IDLE_HOURS,
    PROACTIVE_CHECK_INTERVAL_MINUTES,
    PROACTIVE_STALE_DAYS,
    PROACTIVE_BREAK_IDLE_MINUTES,
    PROACTIVE_LONG_SESSION_HOURS,
    PROACTIVE_DEADLINE_WARN_DAYS,
    PROACTIVE_TASK_DUE_DAYS,
    PROACTIVE_STATE_PATH,
)
from tools import daily_tasks, session_summary
from utils.notifier import notify
from utils.vault_md import parse_frontmatter

_DATE_FMT = "%Y-%m-%d"


# =========================================================
# STATE (dedup so a check fires at most once per stretch)
# =========================================================

def _load_state() -> dict:
    if not PROACTIVE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict):
    PROACTIVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROACTIVE_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(PROACTIVE_STATE_PATH)


# =========================================================
# 1. VAULT STALENESS
# =========================================================

def check_vault_staleness():
    """
    Reads active-priorities.md's own `updated:` frontmatter date — not
    a per-item parse of its prose bullets. The file is hand-curated
    prose with no per-item machine-readable timestamp (checked before
    building this), so a whole-file signal is the honest granularity
    available, not "which project is stale."
    """
    path = VAULT_DIR / "active-priorities.md"
    if not path.exists():
        return

    try:
        fields = parse_frontmatter(path.read_text(encoding="utf-8"))
        updated_str = fields.get("updated", "")
        updated = datetime.strptime(updated_str, _DATE_FMT)
    except (ValueError, OSError):
        return

    days = (datetime.now() - updated).days
    if days < PROACTIVE_STALE_DAYS:
        return

    state = _load_state()
    stale = state.setdefault("stale", {})

    # Dedup key is the `updated` value itself, not just "already
    # notified" — so a fresh edit that later goes stale again DOES
    # notify again, rather than being silenced forever by one old flag.
    if stale.get("active-priorities.md") == updated_str:
        return

    notify(
        f"Active priorities hasn't been touched in {days} days, sir.",
        title="Active Priorities",
    )
    stale["active-priorities.md"] = updated_str
    _save_state(state)


# =========================================================
# 2. LONG SESSION (no break)
# =========================================================

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input, system-wide —
    Windows' own idle-time API, not FRED-specific activity."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return millis / 1000.0


def check_long_session():
    """
    Flags continuous machine use with no break, using real OS-level
    idle time rather than FRED-conversation activity — the two are
    different things, and "no one has spoken to FRED in an hour" says
    nothing about whether Vatsal stepped away.
    """
    state = _load_state()
    session = state.setdefault("long_session", {})
    now = datetime.now()

    idle = _idle_seconds()
    if idle >= PROACTIVE_BREAK_IDLE_MINUTES * 60:
        # A real break — reset the clock and the notified flag, so the
        # NEXT long stretch can notify again.
        session["last_break"] = now.isoformat()
        session.pop("notified", None)
        _save_state(state)
        return

    last_break_str = session.get("last_break")
    if last_break_str is None:
        # First check since launch — start counting from now rather
        # than assuming a long session was already underway.
        session["last_break"] = now.isoformat()
        _save_state(state)
        return

    try:
        last_break = datetime.fromisoformat(last_break_str)
    except ValueError:
        session["last_break"] = now.isoformat()
        _save_state(state)
        return

    if session.get("notified"):
        return

    if now - last_break >= timedelta(hours=PROACTIVE_LONG_SESSION_HOURS):
        notify(
            f"You've been at this {PROACTIVE_LONG_SESSION_HOURS} hours "
            f"straight with no break, sir.",
            title="Break?",
        )
        session["notified"] = True
        _save_state(state)


# =========================================================
# 3. DEADLINE PROXIMITY
# =========================================================

def check_deadlines():
    """
    Reads an optional `deadline: YYYY-MM-DD` frontmatter field on any
    vault file. No vault file uses this field yet — as of 2026-08-01,
    exam dates aren't recorded (see active-priorities.md's "Recently
    closed" — board exam dates were explicitly removed pending real
    dates). This is the read path for that field once it exists, not
    speculative parsing of dates out of prose.
    """
    if not VAULT_DIR.exists():
        return

    state = _load_state()
    notified = state.setdefault("deadlines", {})
    now = datetime.now()
    changed = False

    for path in VAULT_DIR.rglob("*.md"):
        try:
            fields = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue

        deadline_str = fields.get("deadline", "")
        if not deadline_str:
            continue

        try:
            deadline = datetime.strptime(deadline_str, _DATE_FMT)
        except ValueError:
            continue

        days_until = (deadline - now).days
        if not (0 <= days_until <= PROACTIVE_DEADLINE_WARN_DAYS):
            continue

        key = f"{path.name}|{deadline_str}"
        if notified.get(key):
            continue

        label = fields.get("type", "deadline")
        notify(
            f"{path.stem}, {label}, in {days_until} day(s), sir.",
            title="Deadline",
        )
        notified[key] = True
        changed = True

    if changed:
        _save_state(state)


# =========================================================
# 4. TASK DEADLINES (daily notes, not frontmatter)
# =========================================================

def check_task_deadlines():
    """
    Warns about a still-open task whose own text says when it's due.

    check_deadlines above reads a `deadline:` frontmatter field that no
    vault file has ever used. Real deadlines live in the daily notes'
    task lines instead — "Chemistry journal completion — due Thursday in
    school" (daily/2026-08/2026-08-03.md). Confirmed 2026-08-04: that
    task, and a physics one alongside it, went unmentioned until Vatsal
    pushed back twice asking what else was pending. Nothing was watching
    them.

    Dedup is per task per due date, so a task that gets re-dated warns
    again but an unchanged one doesn't nag every 15 minutes.
    """
    state = _load_state()
    notified = state.setdefault("task_deadlines", {})
    changed = False

    try:
        due = daily_tasks.open_due_tasks(within_days=PROACTIVE_TASK_DUE_DAYS)
    except Exception as e:
        print(f"[proactive] task deadline check failed: {e}")
        return

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for deadline, text in due:
        key = f"{text}|{deadline:%Y-%m-%d}"
        if notified.get(key):
            continue

        days = (deadline - today).days
        if days < 0:
            when = f"{abs(days)} day(s) overdue"
        elif days == 0:
            when = "due today"
        elif days == 1:
            when = "due tomorrow"
        else:
            when = f"due in {days} day(s)"

        notify(f"{text} — {when}, sir.", title="Task deadline")
        notified[key] = True
        changed = True

    if changed:
        _save_state(state)


# =========================================================
# 5. OVERNIGHT DAY ROLLOVER
# =========================================================

def _recent_transcript(today: str) -> str:
    """
    What was actually said over the stretch being closed out: the
    previous day plus today's own turns, in order. Both, because a
    rollover that fires at 01:00 sits on the far side of midnight from
    the evening it is summarising, and a session that ran past midnight
    lands in today's log.
    """
    prev = (
        datetime.strptime(today, _DATE_FMT) - timedelta(days=1)
    ).strftime(_DATE_FMT)
    parts = [session_summary.transcript(d) for d in (prev, today)]
    return "\n".join(p for p in parts if p)


def _judge_carryover(candidates: list, llm, today: str) -> list:
    """
    Which of yesterday's open tasks are still worth carrying. Judged by
    the Deep tier (Qwen3-14B) and pinned local_only — this reads the
    vault's task text AND the day's raw conversation, which is exactly
    the material that shouldn't leave the machine for a filtering job a
    local model does fine.

    Anything the model can't be matched back to a real candidate line is
    dropped, so a hallucinated task can never enter the note. If the
    judgement fails outright, everything carries: losing a task silently
    is worse than carrying one that was already dead.
    """
    if llm is None:
        return candidates

    listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(candidates))
    convo = _recent_transcript(today) or "(nothing logged)"
    messages = [
        {
            "role": "system",
            "content": (
                "You decide which unfinished tasks are still worth carrying "
                "into the next day. Use the conversation log: a task he said "
                "he finished, dropped, or that the log shows is no longer "
                "relevant should not carry. Drop tasks that are clearly "
                "obsolete or tied to a date that has passed and cannot be "
                "redone. When unsure, keep the task. Answer with the numbers "
                "to keep, comma-separated, and nothing else."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Conversation log:\n{convo}\n\n"
                f"Unfinished tasks:\n{listing}"
            ),
        },
    ]

    try:
        answer = llm.generate(messages, tier="Deep", local_only=True)
    except Exception as e:
        print(f"[proactive] carryover judgement failed, keeping all: {e}")
        return candidates

    kept = [
        candidates[int(n) - 1]
        for n in re.findall(r"\d+", answer or "")
        if 0 < int(n) <= len(candidates)
    ]
    # An empty or unparseable answer means "no judgement", not "drop
    # everything" — the model refusing to answer must not wipe the list.
    return kept or candidates


def check_day_rollover(llm=None):
    """
    Starts the new day's note with whatever is still outstanding.

    Fires when the machine has been idle for ROLLOVER_IDLE_HOURS — the
    overnight gap in practice. Keyed on the wall date, so a two-hour gap
    inside the same day does nothing: the date has to have turned over
    since the last rollover for there to be a new day to write.
    """
    if _idle_seconds() < ROLLOVER_IDLE_HOURS * 3600:
        return

    today = datetime.now().strftime(_DATE_FMT)
    state = _load_state()
    if state.get("rollover_day") == today:
        return

    try:
        candidates = daily_tasks.carryover_candidates(today)
    except Exception as e:
        print(f"[proactive] rollover read failed: {e}")
        return

    # The note gets created even with nothing to carry — "there is a log
    # for today" is the point; an empty one still gets appended to by
    # add_task and the session recap later in the day.
    daily_tasks.ensure_day_note(today)
    for text in _judge_carryover(candidates, llm, today):
        daily_tasks.add_task(text, day=today)

    state["rollover_day"] = today
    _save_state(state)


# =========================================================
# WIRING
# =========================================================

def register(scheduler, llm=None):
    """Call once at orchestrator startup — see orchestrator.py's __init__."""
    scheduler.add_periodic(
        lambda: check_day_rollover(llm), PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_day_rollover"
    )
    scheduler.add_periodic(
        check_vault_staleness, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_vault_staleness"
    )
    scheduler.add_periodic(
        check_long_session, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_long_session"
    )
    scheduler.add_periodic(
        check_deadlines, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_deadlines"
    )
    scheduler.add_periodic(
        check_task_deadlines, PROACTIVE_CHECK_INTERVAL_MINUTES, "proactive_task_deadlines"
    )
