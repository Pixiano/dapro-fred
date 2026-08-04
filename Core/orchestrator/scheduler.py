# Core/orchestrator/scheduler.py
#
# Phase 15 — "He Speaks First." Background scheduler for reminders
# and periodic checks, so FRED can interrupt with something that
# matters instead of only ever responding when spoken to.

import re
import time
from pathlib import Path
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from config.settings import SCHEDULER_DB_PATH
from tools.assist_tools import format_duration
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

# "2026-08-05 17:55" / "2026-08-05" — the form the model itself reaches
# for once IT has already resolved a relative/named date to a specific
# calendar day (see schedule_reminder's tool description). Confirmed
# bug (session_2026-08-03.jsonl): the model correctly computed
# when="2026-08-05 17:55" for "Wednesday at 5:55pm", but _CLOCK_RE is
# unanchored and greedily matched "20" out of "2026" as the hour before
# this branch existed — produced "tomorrow at 8:00 PM" (20:00, rolled
# forward because 8pm today had already passed), nowhere close to what
# was asked. Checked before _CLOCK_RE below so an ISO date's digits are
# never reachable by that regex at all.
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[ t](\d{1,2}):(\d{2}))?\b")

# Named weekdays — "remind me Wednesday at 6", "next Friday". Resolved
# to the closest occurrence that isn't in the past; same "roll forward
# if already gone by" rule the rest of this function uses for bare
# clock times, applied via day_offset below rather than a special case.
_WEEKDAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

def _fire_reminder(message: str):
    """
    The reminder job's actual target — never notify() directly, so the
    "Here's your reminder:" framing lives only here, at fire time, and
    never gets stored as the job's args (see schedule_reminder).
    """
    notify(f"Here's your reminder: {message}", title="Reminder")


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

    iso = _ISO_DATE_RE.search(text)
    if iso:
        year, month, day, hour, minute = iso.groups()
        try:
            candidate = datetime(
                int(year), int(month), int(day), int(hour or 0), int(minute or 0)
            )
        except ValueError:
            return None
        # Unlike a bare clock time, an explicit calendar date in the
        # past is unambiguous — nothing to roll forward to.
        return candidate if candidate > now else None

    day_offset = 1 if "tomorrow" in text else 0

    weekday = _WEEKDAY_RE.search(text)
    if weekday:
        target = _WEEKDAY_INDEX[weekday.group(1).lower()]
        day_offset = (target - now.weekday()) % 7

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


# APScheduler's cron day_of_week vocabulary. Its own abbreviations, so
# the parsed value can be handed straight to add_job without a second
# translation step.
_CRON_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_DAY_WORDS = {
    "monday": "mon", "mon": "mon",
    "tuesday": "tue", "tue": "tue", "tues": "tue",
    "wednesday": "wed", "wed": "wed",
    "thursday": "thu", "thu": "thu", "thur": "thu", "thurs": "thu",
    "friday": "fri", "fri": "fri",
    "saturday": "sat", "sat": "sat",
    "sunday": "sun", "sun": "sun",
}

# Phrases naming a whole set of days at once.
_DAY_SETS = {
    "every day": "mon,tue,wed,thu,fri,sat,sun",
    "everyday": "mon,tue,wed,thu,fri,sat,sun",
    "daily": "mon,tue,wed,thu,fri,sat,sun",
    "weekday": "mon,tue,wed,thu,fri",
    "weekdays": "mon,tue,wed,thu,fri",
    "weekend": "sat,sun",
    "weekends": "sat,sun",
}

# "every"/"each" is what separates a repeating request from a one-off.
# parse_when() deliberately has no notion of it — it returns a single
# datetime by contract (pinned by test_scheduler_parse_when.py), so
# recurrence is parsed here instead of complicating that function.
#
# The plural day-set words carry recurrence on their own: "weekends at
# noon" names no "every" but is unambiguously repeating, and without
# them it fell through to the one-shot path. Singular "weekday" is
# deliberately absent — "remind me on a weekday" is not a schedule.
_RECURRING_RE = re.compile(
    r"\b(every|each|daily|weekly|weekends?|weekdays)\b", re.IGNORECASE
)


def looks_recurring(text: str) -> bool:
    """True if the phrasing asks for a repeating reminder rather than a
    single one. Used by the tool layer to pick between schedule_reminder
    and schedule_recurring."""
    return bool(text) and bool(_RECURRING_RE.search(str(text)))


def parse_recurrence(text: str):
    """
    Resolve a repeating time expression to (day_of_week, hour, minute)
    for an APScheduler cron trigger, or None if it isn't recurring.

    Handles "every day at 7am", "every weekday at 6:30pm", "every monday
    and wednesday at 5", "weekends at noon".

    Reuses parse_when() for the clock half rather than duplicating its
    am/pm and evening-word rules — that logic is subtle (see its
    docstring) and having two copies drift apart would produce a
    recurring reminder that fires an hour off from the one-shot version
    of the same phrasing.
    """
    if not looks_recurring(text):
        return None

    lowered = str(text).strip().lower()

    days = None
    for phrase, value in _DAY_SETS.items():
        if phrase in lowered:
            days = value
            break

    if days is None:
        named = [
            _DAY_WORDS[word]
            for word in re.findall(r"[a-z]+", lowered)
            if word in _DAY_WORDS
        ]
        if named:
            # dict.fromkeys rather than set(): preserves the order spoken
            # so "monday and wednesday" doesn't come back as "wed,mon".
            days = ",".join(dict.fromkeys(named))

    # "every day" is the sensible default for a bare "remind me every
    # day"-shaped request that named no days at all.
    if days is None:
        days = _DAY_SETS["every day"]

    # Strip the day words before handing the rest to parse_when — left
    # in, "every monday at 5" would send it down its own _WEEKDAY_RE
    # branch and shift the date, which is meaningless here since cron
    # supplies the day and only the time-of-day is wanted.
    time_text = _WEEKDAY_RE.sub(" ", lowered)
    when = parse_when(time_text)
    if when is None:
        return None

    return days, when.hour, when.minute


def describe_recurrence(days: str, hour: int, minute: int) -> str:
    """Human phrasing for a recurring confirmation, spoken aloud."""
    clock = datetime(2000, 1, 1, hour, minute).strftime("%I:%M %p").lstrip("0")

    for phrase, value in (("every day", _DAY_SETS["every day"]),
                          ("every weekday", _DAY_SETS["weekday"]),
                          ("every weekend", _DAY_SETS["weekend"])):
        if days == value:
            return f"{phrase} at {clock}"

    spoken = {
        "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
        "thu": "Thursday", "fri": "Friday", "sat": "Saturday",
        "sun": "Sunday",
    }
    names = [spoken.get(d.strip(), d.strip()) for d in days.split(",")]
    if len(names) == 1:
        return f"every {names[0]} at {clock}"
    return f"every {', '.join(names[:-1])} and {names[-1]} at {clock}"


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

    def add_periodic(self, func, minutes: float, job_id: str):
        """
        Runs `func` (no args) on a fixed interval, forever, in-memory
        only — unlike one-off reminders, a periodic check has nothing
        meaningful to persist across a restart, it just re-registers
        itself every launch (see orchestrator/proactive_checks.py).
        Skips re-adding if `job_id` already exists, so calling this
        twice in one process doesn't stack duplicate jobs.
        """
        if self._scheduler.get_job(job_id, jobstore="memory"):
            return
        self._scheduler.add_job(
            func,
            trigger="interval",
            minutes=minutes,
            id=job_id,
            jobstore="memory",
        )

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

        # Firing goes through _fire_reminder rather than notify directly,
        # so job.args stays the plain original message — used by
        # list_scheduled to show what the reminder is actually about — and
        # the "Here's your reminder:" framing is added only at the moment
        # it's spoken. Baking the framing into args instead produced a
        # visibly double-wrapped list entry: "Reminder: \"Here's your
        # reminder: buy milk\"".
        self._scheduler.add_job(
            _fire_reminder,
            args=[message],
            trigger="date",
            run_date=run_at,
            id=self._next_job_id("reminder"),
            jobstore="default",
            misfire_grace_time=None,
        )

        return f"Reminder set for {describe_when(run_at, now)}: \"{message}\""

    def set_timer(self, minutes: float, label: str = "") -> str:
        """
        A countdown, kept separate from schedule_reminder on purpose.

        "Set a timer for 10 minutes" and "remind me at 7pm" are different
        requests, and giving the model one tool for both meant it had to
        convert between forms. A timer is always a short relative
        countdown, so it takes only a duration and never a clock time.
        """
        try:
            duration = float(minutes)
        except (TypeError, ValueError):
            return f"\"{minutes}\" isn't a number of minutes I can use."

        if duration <= 0:
            return "A timer needs a positive number of minutes."
        if duration > 24 * 60:
            return "That's over a day — set a reminder instead."

        run_at = datetime.now() + timedelta(minutes=duration)
        duration_text = format_duration(duration)

        # A label already identifies the timer better than its duration
        # does — "Your pasta timer is up!" needs no number attached, and
        # cramming both in ("your 10 minutes pasta timer is up") reads as
        # broken grammar rather than more informative.
        message = f"Your {label} timer is up!" if label else f"Your {duration_text} timer is up!"

        self._scheduler.add_job(
            notify,
            args=[message],
            kwargs={"title": "Timer"},
            trigger="date",
            run_date=run_at,
            id=self._next_job_id("timer"),
            jobstore="default",
            misfire_grace_time=None,
        )

        suffix = f" for {label}" if label else ""
        return f"Timer set for {duration_text}{suffix}."

    def schedule_recurring(
        self,
        message: str,
        when: str = None,
        days: str = None,
        hour: int = None,
        minute: int = None,
        job_id: str = None,
    ) -> str:
        """
        A reminder that repeats, on a cron trigger.

        Give either `when` as a phrase ("every weekday at 6:30pm") or the
        structured form (`days="mon,wed,fri"`, `hour`, `minute`). The
        structured form exists for callers that already know the schedule
        and shouldn't have to render it to English just to have it parsed
        back — see tools/workout_plan.py.

        `job_id` makes a recurring reminder replaceable: re-registering
        the same id updates the existing job in place rather than
        stacking a duplicate. Without it, re-running the workout setup
        would leave two reminders firing at once.

        Persists like schedule_reminder. coalesce=True so a laptop that
        was off for three days fires ONE catch-up reminder rather than
        three at once — the misfire_grace_time=None used by the one-shot
        jobs means "no grace limit", which on a repeating trigger would
        otherwise mean every missed occurrence firing on boot.
        """
        if days is None or hour is None:
            parsed = parse_recurrence(when or message)
            if parsed is None:
                return (
                    f"I couldn't read \"{when or message}\" as a repeating time. "
                    "Try something like \"every weekday at 7am\" or "
                    "\"every monday and thursday at 6pm\"."
                )
            days, hour, minute = parsed

        minute = int(minute or 0)
        hour = int(hour)

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return "That isn't a valid time of day."

        wanted = [d.strip().lower() for d in str(days).split(",") if d.strip()]
        bad = [d for d in wanted if d not in _CRON_DAYS]
        if bad or not wanted:
            return f"I don't recognise those days: {', '.join(bad) or 'none given'}."
        days = ",".join(wanted)

        self._scheduler.add_job(
            _fire_reminder,
            args=[message],
            trigger="cron",
            day_of_week=days,
            hour=hour,
            minute=minute,
            id=job_id or self._next_job_id("recurring"),
            jobstore="default",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )

        return (
            f"Recurring reminder set for "
            f"{describe_recurrence(days, hour, minute)}: \"{message}\""
        )

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
            # The proactive checks (proactive_vault_staleness etc.) live
            # in the memory jobstore and get_jobs() returns both stores,
            # so they land here too — they aren't reminders and reading
            # them out as "File watch: ''" was pure noise.
            if job.id.startswith("proactive_"):
                continue

            if job.id.startswith("reminder_"):
                kind = "Reminder"
            elif job.id.startswith("timer_"):
                kind = "Timer"
            elif job.id.startswith("workout_"):
                kind = "Workout"
            elif job.id.startswith("recurring_"):
                kind = "Recurring"
            else:
                kind = "File watch"

            # A raw ISO timestamp read aloud is a string of digits —
            # "twenty twenty six dash oh seven dash thirty". describe_when
            # already exists for exactly this from the reminder-time work.
            when = (
                describe_when(job.next_run_time)
                if job.next_run_time else "pending"
            )
            detail = job.args[0] if job.args else ""
            lines.append(f"- [{job.id}] {kind}: \"{detail}\" — {when}")

        # The early "no jobs at all" return above doesn't cover this: the
        # proactive checks are always registered, so `jobs` is never
        # empty in a live process and a user with nothing of their own
        # scheduled would otherwise get an empty string read aloud as
        # silence.
        if not lines:
            return "Nothing scheduled right now."

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

    def cancel_job_id(self, job_id: str) -> bool:
        """
        Remove one job by its exact id, quietly. True if it existed.

        Distinct from cancel_scheduled(), which is the spoken-command
        path — it matches fuzzily on message text and reports back in a
        sentence. This is for callers that manage their own stable ids
        and need to retract one without narrating anything (see
        tools/workout_plan.py retiring a day that became a rest day).
        """
        job = self._scheduler.get_job(job_id, jobstore="default")
        if job is None:
            return False
        self._remove_job(job_id, "default")
        return True

    def _remove_job(self, job_id: str, jobstore: str):

        try:
            self._scheduler.remove_job(job_id, jobstore)
        except Exception:
            pass

    # =========================================================
    # INTERNAL
    # =========================================================

    def _next_job_id(self, prefix: str) -> str:
        """
        Unique across restarts, not just within a session.

        The counter alone collided: reminders persist to SQLite but
        _job_counter restarts at zero every launch, so the first reminder
        of a new session tried to reuse "reminder_1" and APScheduler
        rejected it — "Job identifier (reminder_1) conflicts with an
        existing job". Observed as a timer that silently refused to set
        while an old reminder was still pending. The timestamp makes the
        id unique per run; the counter keeps ids distinct within one.
        """
        self._job_counter += 1
        stamp = int(time.time())
        return f"{prefix}_{stamp}_{self._job_counter}"
