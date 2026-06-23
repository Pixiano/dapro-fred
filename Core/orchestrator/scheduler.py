# Core/orchestrator/scheduler.py
#
# Phase 15 — "He Speaks First." Background scheduler for reminders
# and periodic checks, so FRED can interrupt with something that
# matters instead of only ever responding when spoken to.

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

    def schedule_reminder(self, message: str, minutes: float) -> str:
        """
        Fires a notification once, after the given number of minutes.
        Persists — survives a restart, firing late if FRED was off
        past the due time rather than silently dropping it.
        """

        run_at = datetime.now() + timedelta(minutes=float(minutes))

        self._scheduler.add_job(
            notify,
            args=[message],
            trigger="date",
            run_date=run_at,
            id=self._next_job_id("reminder"),
            jobstore="default",
            misfire_grace_time=None,
        )

        return f"Reminder set for {minutes} minute(s) from now: \"{message}\""

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
