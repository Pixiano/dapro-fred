# Core/orchestrator/scheduler.py
#
# Phase 15 — "He Speaks First." Background scheduler for reminders
# and periodic checks, so FRED can interrupt with something that
# matters instead of only ever responding when spoken to.

import re
from pathlib import Path
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from config.settings import SCHEDULER_DB_PATH
from utils.notifier import notify

# Safety valve for "tell me when X shows up" — without a cap, a typo'd
# path would poll forever, silently, until the process is killed.
_MAX_FILE_WATCH_HOURS = 24

# "in 20 minutes", "in 2 hours"
_RELATIVE_RE = re.compile(
    r"\bin\s+(\d+(?:\.\d+)?)\s*"
    r"(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\b",
    re.IGNORECASE,
)

# "7pm", "at 7:30 pm", "19:00", "7"
_CLOCK_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?",
    re.IGNORECASE,
)

_UNIT_MINUTES = {
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "d": 1440, "day": 1440, "days": 1440,
}


def parse_when(text: str, now: datetime = None) -> datetime:
    """
    Resolve a spoken time expression to an absolute datetime.

    Handles "7pm", "at 7:30 pm", "19:00", "noon", "midnight",
    "tomorrow at 8am", and relative forms like "in 20 minutes".
    Returns None if nothing time-like is found.

    Two deliberate rules, because speech is ambiguous in ways a clock
    isn't:

    - A time that has already passed rolls to the next day rather than
      firing instantly or erroring. "Remind me at 7am" said at 9am
      plainly means tomorrow.
    - A bare hour with no am/pm resolves to whichever of H or H+12 comes
      next. "At 7" said at 6am is 7am; said at 9am it's 7pm. Treating it
      as a 24-hour clock instead would make "at 7" in the evening mean
      7am tomorrow, which is almost never what was meant.
    """
    if not text or not str(text).strip():
        return None

    now = now or datetime.now()
    text = str(text).strip().lower()

    relative = _RELATIVE_RE.search(text)
    if relative:
        amount = float(relative.group(1))
        unit = _UNIT_MINUTES.get(relative.group(2).lower(), 1)
        return now + timedelta(minutes=amount * unit)

    day_offset = 1 if "tomorrow" in text else 0

    # "tonight at 10" means 22:00, not 10:00 — an evening word carries
    # the same information as an explicit "pm" for a bare hour.
    evening_implied = any(
        word in text for word in ("tonight", "evening", "at night", "tonite")
    )
    morning_implied = "morning" in text

    if "midnight" in text:
        base = (now + timedelta(days=day_offset + 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return base
    if "noon" in text or "midday" in text:
        candidate = (now + timedelta(days=day_offset)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        return candidate if candidate > now else candidate + timedelta(days=1)

    match = _CLOCK_RE.search(text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").replace(".", "")

    if hour > 23 or minute > 59:
        return None

    if meridiem.startswith("p") and hour < 12:
        hour += 12
    elif meridiem.startswith("a") and hour == 12:
        hour = 0
    elif not meridiem and evening_implied and hour < 12:
        hour += 12
    elif not meridiem and morning_implied and hour == 12:
        hour = 0

    candidate = (now + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )

    # Bare hour, no am/pm given, and 12-hour-clock plausible: allow the
    # afternoon reading if the morning one has already gone by.
    if not meridiem and hour < 12 and candidate <= now:
        afternoon = candidate + timedelta(hours=12)
        if afternoon > now:
            return afternoon

    while candidate <= now:
        candidate += timedelta(days=1)

    return candidate


def describe_when(when: datetime, now: datetime = None) -> str:
    """Human phrasing for a confirmation that gets spoken aloud."""
    now = now or datetime.now()
    clock = when.strftime("%I:%M %p").lstrip("0")

    days = (when.date() - now.date()).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    return f"{when.strftime('%A %d %B')} at {clock}"


class ReminderScheduler:
    """
    Wraps APScheduler's BackgroundScheduler with the two proactive
    triggers Phase 15 asks for: one-off reminders, and "tell me when
    this file shows up" watches. Runs on its own thread, so it fires
    even while main.py is blocked on input() waiting for the user.

    Reminders persist to a local SQLite file (SCHEDULER_DB_PATH) so
    they survive FRED being restarted or killed mid-wait — if it's
    been off past the reminder's due time, it fires the moment it's
    next running, late rather than lost. File-watches stay in-memory
    only: they poll a bound method, which isn't safely picklable into
    a persistent store, and a "watch for this file" request is more
    reasonably session-scoped anyway.
    """

    def __init__(self):

        self._scheduler = BackgroundScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(
                    url=f"sqlite:///{SCHEDULER_DB_PATH}"
                ),
                "memory": MemoryJobStore(),
            }
        )
        self._scheduler.start()
        self._job_counter = 0

    def shutdown(self):

        self._scheduler.shutdown(wait=False)

    # =========================================================
    # ONE-OFF REMINDERS (persistent)
    # =========================================================

    def schedule_reminder(
        self,
        message: str,
        minutes: float = None,
        when: str = None,
    ) -> str:
        """
        Fires a notification once. Give either `when` (an absolute time
        like "7pm", "tomorrow at 8:30am", "19:00") or `minutes` (an
        offset from now). `when` wins if both arrive, being the more
        specific of the two.

        Persists — survives a restart, firing late if FRED was off past
        the due time rather than silently dropping it.
        """

        now = datetime.now()
        run_at = None

        if when is not None and str(when).strip():
            run_at = parse_when(str(when), now=now)
            if run_at is None:
                return (
                    f"I couldn't read \"{when}\" as a time. Try something like "
                    f"\"7pm\", \"tomorrow at 8:30am\", or \"in 20 minutes\"."
                )

        if run_at is None:
            if minutes is None:
                return (
                    "I need a time for that — either an absolute one like "
                    "\"7pm\" or an offset like \"in 20 minutes\"."
                )
            try:
                run_at = now + timedelta(minutes=float(minutes))
            except (TypeError, ValueError):
                return f"\"{minutes}\" isn't a number of minutes I can use."

        if run_at <= now:
            return "That time has already passed."

        self._scheduler.add_job(
            notify,
            args=[message],
            trigger="date",
            run_date=run_at,
            id=self._next_job_id("reminder"),
            jobstore="default",
            misfire_grace_time=None,
        )

        return f"Reminder set for {describe_when(run_at, now)}: \"{message}\""

    # =========================================================
    # FILE WATCH (in-memory only — see class docstring)
    # =========================================================

    def schedule_file_watch(
        self,
        path: str,
        message: str = "",
        check_interval_seconds: int = 30,
    ) -> str:
        """
        Polls for a file/folder to appear, notifying once it does
        (then stops). Gives up after _MAX_FILE_WATCH_HOURS so a typo
        doesn't poll forever unnoticed. Does NOT survive a restart.
        """

        job_id = self._next_job_id("filewatch")
        deadline = datetime.now() + timedelta(hours=_MAX_FILE_WATCH_HOURS)
        notify_message = message or f"{path} showed up."

        self._scheduler.add_job(
            self._check_file,
            args=[path, notify_message, job_id, deadline],
            trigger="interval",
            seconds=check_interval_seconds,
            id=job_id,
            jobstore="memory",
        )

        return f"Watching for {path} — I'll let you know when it shows up."

    def _check_file(self, path: str, message: str, job_id: str, deadline: datetime):

        if Path(path).exists():
            notify(message)
            self._remove_job(job_id, "memory")
            return

        if datetime.now() >= deadline:
            notify(f"Gave up waiting for {path} — it never showed up.")
            self._remove_job(job_id, "memory")

    # =========================================================
    # LIST / CANCEL
    # =========================================================

    def list_scheduled(self) -> str:
        """
        Human-readable summary of every pending reminder and watch.
        """

        jobs = self._scheduler.get_jobs()

        if not jobs:
            return "Nothing scheduled right now."

        lines = []

        for job in jobs:
            kind = "reminder" if job.id.startswith("reminder_") else "file watch"
            when = (
                job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                if job.next_run_time else "pending"
            )
            detail = job.args[0] if job.args else ""
            lines.append(f"- [{job.id}] {kind}: \"{detail}\" — {when}")

        return "\n".join(lines)

    def cancel_scheduled(self, identifier: str) -> str:
        """
        Cancels a reminder or file watch by job id (from
        list_scheduled) or by a substring of its message/path. Pass
        "all" to clear everything pending.
        """

        identifier = identifier.strip()
        jobs = self._scheduler.get_jobs()

        if not jobs:
            return "Nothing scheduled to cancel."

        if identifier.lower() == "all":
            for job in jobs:
                self._scheduler.remove_job(job.id, job._jobstore_alias)
            return f"Cancelled all {len(jobs)} pending reminder(s)/watch(es)."

        matches = [
            job for job in jobs
            if identifier == job.id
            or identifier.lower() in str(job.args[0] if job.args else "").lower()
        ]

        if not matches:
            return f"Nothing scheduled matching '{identifier}'."

        for job in matches:
            self._scheduler.remove_job(job.id, job._jobstore_alias)

        described = ", ".join(str(j.args[0]) for j in matches if j.args)
        return f"Cancelled: {described}"

    def _remove_job(self, job_id: str, jobstore: str):

        try:
            self._scheduler.remove_job(job_id, jobstore)
        except Exception:
            pass

    # =========================================================
    # INTERNAL
    # =========================================================

    def _next_job_id(self, prefix: str) -> str:

        self._job_counter += 1
        return f"{prefix}_{self._job_counter}"
