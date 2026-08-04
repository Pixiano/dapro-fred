# Recurring reminders — the second half of the reminder system, added
# once "remind me every weekday at 7am" turned out to schedule a single
# one-off. parse_when() returns ONE datetime by contract (pinned by
# test_scheduler_parse_when.py), so a repeating phrase silently lost its
# repetition: it fired tomorrow morning and never again.
#
# Two things this file guards that aren't obvious from reading the code:
#
# - The plural day-set words ("weekends at noon", "weekdays at 7am")
#   carry recurrence with no "every" anywhere in them. They were the
#   phrasings that fell through to the one-shot path. Singular "weekday"
#   deliberately does NOT — "remind me on a weekday" is not a schedule,
#   and treating it as one would register seven cron jobs from an
#   offhand sentence.
# - job_id + replace_existing=True is the whole reason re-running the
#   workout setup is idempotent. Drop either and every re-run stacks
#   another copy of the same reminder, so a week of edits to the split
#   ends with five alarms firing at 4:55pm at once.

from orchestrator import scheduler
from orchestrator.scheduler import ReminderScheduler


class FakeAPScheduler:
    """
    Stands in for BackgroundScheduler so the validation branches can be
    tested without starting a thread or touching the real SQLite
    jobstore. Records rather than acts — the assertions here are about
    what schedule_recurring *asks* APScheduler to do.
    """

    def __init__(self):
        self.added = []      # kwargs of each add_job call
        self.removed = []
        self.jobs = {}

    def add_job(self, func, **kwargs):
        self.added.append(kwargs)
        self.jobs[kwargs.get("id")] = func
        return kwargs.get("id")

    def get_job(self, job_id, jobstore=None):
        return self.jobs.get(job_id)

    def remove_job(self, job_id, jobstore=None):
        self.removed.append(job_id)
        self.jobs.pop(job_id, None)


def _bare_scheduler():
    """
    A ReminderScheduler with no __init__ run — __init__ starts a real
    background thread and opens SCHEDULER_DB_PATH, neither of which any
    of these assertions need, and both of which leak between tests.
    """
    obj = ReminderScheduler.__new__(ReminderScheduler)
    fake = FakeAPScheduler()
    obj._scheduler = fake
    obj._job_counter = 0
    return obj, fake


# =========================================================
# parse_recurrence
# =========================================================

def test_every_day_covers_the_whole_week():
    assert scheduler.parse_recurrence("every day at 7am") == (
        "mon,tue,wed,thu,fri,sat,sun", 7, 0,
    )


def test_weekday_set_excludes_the_weekend():
    assert scheduler.parse_recurrence("every weekday at 6:30pm") == (
        "mon,tue,wed,thu,fri", 18, 30,
    )


def test_named_days_keep_the_order_they_were_spoken_in():
    # dict.fromkeys, not set(), in the parser — a set would come back
    # "thu,mon" and describe_recurrence would read it back in the wrong
    # order, which sounds like FRED misheard the request.
    assert scheduler.parse_recurrence("every monday and thursday at 6pm") == (
        "mon,thu", 18, 0,
    )


def test_plural_day_sets_are_recurring_without_the_word_every():
    # The exact phrasings that used to fall through to a single one-off
    # reminder: no "every" or "each" appears in either.
    assert scheduler.parse_recurrence("weekends at noon") == ("sat,sun", 12, 0)
    assert scheduler.parse_recurrence("weekdays at 7am") == (
        "mon,tue,wed,thu,fri", 7, 0,
    )


def test_a_one_off_is_not_dragged_into_the_recurring_path():
    # The other direction of the same bug — "remind me at 7pm" must stay
    # a single reminder, not become a daily 7pm alarm.
    assert scheduler.parse_recurrence("remind me at 7pm") is None


def test_singular_weekday_is_not_a_schedule():
    """"Remind me on a weekday" names no repetition — reading it as one
    would register five cron jobs from a vague sentence."""
    assert scheduler.parse_recurrence("remind me on a weekday") is None


# =========================================================
# looks_recurring — the tool layer's router between
# schedule_reminder and schedule_recurring
# =========================================================

def test_looks_recurring_covers_every_form_that_means_repeat():
    for text in (
        "every day at 7am",
        "each morning at 8",
        "daily at 9",
        "weekly review at 5pm",
        "weekend at noon",
        "weekends at noon",
        "weekdays at 7am",
    ):
        assert scheduler.looks_recurring(text), text


def test_looks_recurring_is_false_for_a_plain_one_off():
    assert not scheduler.looks_recurring("remind me at 7pm")


# =========================================================
# describe_recurrence — this is spoken aloud, so the phrasing
# is the feature, not decoration
# =========================================================

def test_a_whole_day_set_is_named_by_its_set_not_listed_out():
    # "every Monday, Tuesday, Wednesday, Thursday and Friday at 6:30 PM"
    # is a mouthful for something that has a one-word name.
    assert scheduler.describe_recurrence("mon,tue,wed,thu,fri", 18, 30) == (
        "every weekday at 6:30 PM"
    )


def test_a_single_day_reads_as_a_singular():
    assert scheduler.describe_recurrence("mon", 16, 55) == "every Monday at 4:55 PM"


def test_several_days_get_an_and_before_the_last_one():
    assert scheduler.describe_recurrence("mon,wed,fri", 16, 55) == (
        "every Monday, Wednesday and Friday at 4:55 PM"
    )


# =========================================================
# schedule_recurring — validation before anything is registered
# =========================================================

def test_unreadable_time_is_reported_and_nothing_is_scheduled():
    """A cron job built from a misparse fires forever at the wrong time
    and nobody connects it back to the sentence that created it. Bail
    out loudly instead."""
    obj, fake = _bare_scheduler()

    result = obj.schedule_recurring(message="Stretch", when="whenever-ish")

    assert "couldn't read" in result.lower()
    assert fake.added == []


def test_unknown_day_names_are_rejected_before_add_job():
    # APScheduler raises on an unknown day_of_week from inside its own
    # trigger construction — caught here instead so the user hears a
    # sentence rather than a traceback.
    obj, fake = _bare_scheduler()

    result = obj.schedule_recurring(message="Stretch", days="funday", hour=5)

    assert "recognise" in result.lower()
    assert "funday" in result
    assert fake.added == []


def test_out_of_range_hour_is_rejected():
    obj, fake = _bare_scheduler()

    result = obj.schedule_recurring(message="Stretch", days="mon", hour=25)

    assert "isn't a valid time of day" in result
    assert fake.added == []


def test_structured_call_registers_a_replaceable_cron_job():
    """
    The structured form is what tools/workout_plan.py uses — it already
    knows the schedule and shouldn't have to render it to English just
    to have it parsed back.

    The load-bearing pair is job_id + replace_existing=True: re-running
    the workout setup after an edit to the split must UPDATE Monday's
    reminder, not add a second one beside it. Without both, a few edits
    leave several copies all firing at 4:55pm.
    """
    obj, fake = _bare_scheduler()

    result = obj.schedule_recurring(
        message="Workout - Legs",
        days="mon,wed",
        hour=16,
        minute=55,
        job_id="workout_mon",
    )

    assert len(fake.added) == 1
    job = fake.added[0]
    assert job["trigger"] == "cron"
    assert job["day_of_week"] == "mon,wed"
    assert job["hour"] == 16
    assert job["minute"] == 55
    assert job["id"] == "workout_mon"
    assert job["replace_existing"] is True

    # The confirmation is spoken back, so it has to name the thing that
    # was actually scheduled.
    assert "Workout - Legs" in result
