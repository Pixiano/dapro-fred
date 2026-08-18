# Core/tests/test_scheduler_missed_reminders.py
#
# misfire_grace_time bounded a one-shot reminder/timer to
# _REMINDER_MISFIRE_GRACE_SECONDS (3h) so a stale one doesn't fire days
# late and unprompted — but that left APScheduler silently skipping it
# on restart: no error, no announcement, just gone. _catch_up_missed_
# reminders() (called from start(), before the live scheduler starts)
# is what makes that skip explicit: scan the persisted jobstore, remove
# anything more than the grace window overdue, and speak everything it
# found in one batched notify().

from datetime import datetime, timedelta

from orchestrator import scheduler
from orchestrator.scheduler import ReminderScheduler, _REMINDER_MISFIRE_GRACE_SECONDS


class FakeJob:
    """Stands in for the apscheduler.job.Job objects
    SQLAlchemyJobStore.get_all_jobs() returns — only the attributes
    _catch_up_missed_reminders actually reads."""

    def __init__(self, id, args, next_run_time):
        self.id = id
        self.args = args
        self.next_run_time = next_run_time


class FakeJobStore:
    """Stands in for the SQLAlchemyJobStore instance kept as
    self._default_jobstore — records removals rather than touching a
    real SQLite file."""

    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.removed = []
        self.started = False

    def start(self, scheduler_, alias):
        self.started = True  # mirrors the real jobstore's checkfirst table-create

    def get_all_jobs(self):
        return list(self.jobs)

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self.jobs = [j for j in self.jobs if j.id != job_id]


def _bare_scheduler(jobs):
    """A ReminderScheduler with no __init__ run — see the identical
    helper in test_recurring_reminders.py for why."""
    obj = ReminderScheduler.__new__(ReminderScheduler)
    store = FakeJobStore(jobs)
    obj._default_jobstore = store
    obj._scheduler = None  # only ever passed through to store.start(), unused by the fake
    return obj, store


def test_a_reminder_well_past_the_grace_window_is_caught_removed_and_announced(monkeypatch):
    spoken = []
    monkeypatch.setattr(scheduler, "notify", lambda msg, **kw: spoken.append(msg))

    now = datetime.now()
    stale = now - timedelta(days=3)
    obj, store = _bare_scheduler([FakeJob("reminder_1", ["call banks"], stale)])

    obj._catch_up_missed_reminders()

    assert store.removed == ["reminder_1"]
    assert len(spoken) == 1
    assert "call banks" in spoken[0]
    assert "3 days ago" in spoken[0]


def test_a_reminder_only_slightly_overdue_is_left_alone(monkeypatch):
    # Within _REMINDER_MISFIRE_GRACE_SECONDS — APScheduler's own misfire
    # handling still fires this one late once the scheduler starts; this
    # code path must not touch it first.
    spoken = []
    monkeypatch.setattr(scheduler, "notify", lambda msg, **kw: spoken.append(msg))

    now = datetime.now()
    barely_late = now - timedelta(seconds=_REMINDER_MISFIRE_GRACE_SECONDS - 60)
    obj, store = _bare_scheduler([FakeJob("timer_1", ["pasta"], barely_late)])

    obj._catch_up_missed_reminders()

    assert store.removed == []
    assert spoken == []


def test_workout_cron_jobs_are_never_touched_no_matter_how_overdue(monkeypatch):
    spoken = []
    monkeypatch.setattr(scheduler, "notify", lambda msg, **kw: spoken.append(msg))

    now = datetime.now()
    ancient = now - timedelta(days=30)
    obj, store = _bare_scheduler([
        FakeJob("workout_mon", ["Workout - Legs"], ancient),
        FakeJob("recurring_1", ["Stretch"], ancient),
    ])

    obj._catch_up_missed_reminders()

    assert store.removed == []
    assert spoken == []


def test_nothing_overdue_stays_silent(monkeypatch):
    spoken = []
    monkeypatch.setattr(scheduler, "notify", lambda msg, **kw: spoken.append(msg))

    obj, store = _bare_scheduler([])
    obj._catch_up_missed_reminders()

    assert store.removed == []
    assert spoken == []


def test_multiple_missed_items_are_batched_into_one_notify_call(monkeypatch):
    spoken = []
    monkeypatch.setattr(scheduler, "notify", lambda msg, **kw: spoken.append(msg))

    now = datetime.now()
    obj, store = _bare_scheduler([
        FakeJob("reminder_1", ["call banks"], now - timedelta(days=3)),
        FakeJob("timer_1", ["pasta"], now - timedelta(days=1)),
    ])

    obj._catch_up_missed_reminders()

    assert sorted(store.removed) == ["reminder_1", "timer_1"]
    assert len(spoken) == 1  # one batched sentence, not one notify() per item
    assert "call banks" in spoken[0]
    assert "pasta" in spoken[0]
