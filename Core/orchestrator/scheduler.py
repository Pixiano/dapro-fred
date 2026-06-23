# Core/orchestrator/scheduler.py
#
# Phase 15 — "He Speaks First." Background scheduler for reminders
# and periodic checks, so FRED can interrupt with something that
# matters instead of only ever responding when spoken to.

from pathlib import Path
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

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
    """

    def __init__(self):

        self._scheduler = BackgroundScheduler()
        self._scheduler.start()
        self._job_counter = 0

    def shutdown(self):

        self._scheduler.shutdown(wait=False)

    # =========================================================
    # ONE-OFF REMINDERS
    # =========================================================

    def schedule_reminder(self, message: str, minutes: float) -> str:
        """
        Fires a notification once, after the given number of minutes.
        """

        run_at = datetime.now() + timedelta(minutes=float(minutes))

        self._scheduler.add_job(
            notify,
            args=[message],
            trigger="date",
            run_date=run_at,
            id=self._next_job_id("reminder"),
        )

        return f"Reminder set for {minutes} minute(s) from now: \"{message}\""

    # =========================================================
    # FILE WATCH
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
        doesn't poll forever unnoticed.
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
        )

        return f"Watching for {path} — I'll let you know when it shows up."

    def _check_file(self, path: str, message: str, job_id: str, deadline: datetime):

        if Path(path).exists():
            notify(message)
            self._remove_job(job_id)
            return

        if datetime.now() >= deadline:
            notify(f"Gave up waiting for {path} — it never showed up.")
            self._remove_job(job_id)

    def _remove_job(self, job_id: str):

        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

    # =========================================================
    # INTERNAL
    # =========================================================

    def _next_job_id(self, prefix: str) -> str:

        self._job_counter += 1
        return f"{prefix}_{self._job_counter}"
