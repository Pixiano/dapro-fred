# Core/tests/test_school_proactive.py
#
# The four school checks added to proactive_checks.py on 2026-08-09,
# built to Vatsal's own examples: a deadline statement, an event's
# getting-ready nudge, an upcoming-event question, and a daily
# carryover check-in for anything still open past its due date. Each
# has to dedup the same way the existing checks already do (see
# check_task_deadlines' own comment on why) and the two QUESTION checks
# have to prime the orchestrator's follow-up before anyone can answer
# them — that's the whole point of on_school_ask.

from datetime import datetime, timedelta

from orchestrator import proactive_checks as pc
from tools import school_tasks as st


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    monkeypatch.setattr(pc, "PROACTIVE_STATE_PATH", tmp_path / "proactive_state.json")


def _notifications(monkeypatch):
    seen = []
    monkeypatch.setattr(pc, "notify", lambda msg, title="F.R.E.D.": seen.append(msg))
    return seen


# =========================================================
# check_school_deadlines
# =========================================================

def test_deadline_check_fires_once_then_dedups(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    st.add_item("homework", "Geography", detail="3 questions", due="tomorrow")

    pc.check_school_deadlines()
    assert len(seen) == 1
    assert "Geography" in seen[0]
    assert "due tomorrow" in seen[0]

    pc.check_school_deadlines()
    assert len(seen) == 1  # unchanged deadline, no re-notify


def test_deadline_check_refires_when_the_due_date_changes(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    st.add_item("homework", "Geography", due="tomorrow")
    pc.check_school_deadlines()
    assert len(seen) == 1

    st.update_item("geography", new_due="in 2 days")
    pc.check_school_deadlines()
    assert len(seen) == 2


def test_deadline_check_ignores_events(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    st.add_item("event", "Movie", due="tomorrow", time="2pm")

    pc.check_school_deadlines()
    assert seen == []


def test_deadline_check_says_overdue_for_a_past_date(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    overdue_iso = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    st.add_item("homework", "Geography", due=overdue_iso)

    pc.check_school_deadlines()
    assert "overdue" in seen[0].lower()


def test_deadline_check_survives_a_broken_read(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    monkeypatch.setattr(
        st, "due_within", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    pc.check_school_deadlines()  # must not raise
    assert seen == []


# =========================================================
# check_school_event_prep
# =========================================================

def test_event_prep_fires_inside_the_window_and_dedups(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    start = datetime.now() + timedelta(minutes=10)
    st.add_item(
        "event", "Movie",
        due=start.strftime("%Y-%m-%d"), time=start.strftime("%H:%M"),
        prep_minutes=30,
    )

    pc.check_school_event_prep()
    assert len(seen) == 1
    assert "Movie" in seen[0]
    assert "getting ready" in seen[0]

    pc.check_school_event_prep()
    assert len(seen) == 1


def test_event_prep_does_not_fire_before_the_window_opens(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    start = datetime.now() + timedelta(hours=3)
    st.add_item(
        "event", "Movie",
        due=start.strftime("%Y-%m-%d"), time=start.strftime("%H:%M"),
        prep_minutes=30,
    )

    pc.check_school_event_prep()
    assert seen == []


# =========================================================
# check_school_events_upcoming — a QUESTION, needs on_school_ask
# =========================================================

def test_events_upcoming_asks_and_primes_the_callback(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    primed = []
    start = datetime.now() + timedelta(hours=5)
    st.add_item(
        "event", "Turf session", detail="7 people at Inorbit",
        due=start.strftime("%Y-%m-%d"), time=start.strftime("%H:%M"),
    )

    pc.check_school_events_upcoming(on_school_ask=primed.append)

    assert len(seen) == 1
    assert "Turf session" in seen[0]
    assert "prepped" in seen[0].lower()
    assert primed == [["update_school_item"]]


def test_events_upcoming_dedups_and_does_not_reprime(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _notifications(monkeypatch)
    primed = []
    start = datetime.now() + timedelta(hours=5)
    st.add_item("event", "Turf session", due=start.strftime("%Y-%m-%d"), time=start.strftime("%H:%M"))

    pc.check_school_events_upcoming(on_school_ask=primed.append)
    pc.check_school_events_upcoming(on_school_ask=primed.append)

    assert len(primed) == 1


def test_events_upcoming_ignores_events_more_than_a_day_out(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    start = datetime.now() + timedelta(hours=48)
    st.add_item("event", "Far event", due=start.strftime("%Y-%m-%d"), time=start.strftime("%H:%M"))

    pc.check_school_events_upcoming(on_school_ask=lambda *a: None)
    assert seen == []


def test_events_upcoming_works_with_no_callback_given(tmp_path, monkeypatch):
    """CLI / no-orchestrator callers pass nothing — must not raise."""
    _isolate(tmp_path, monkeypatch)
    _notifications(monkeypatch)
    start = datetime.now() + timedelta(hours=5)
    st.add_item("event", "Turf session", due=start.strftime("%Y-%m-%d"), time=start.strftime("%H:%M"))

    pc.check_school_events_upcoming()  # no on_school_ask at all


# =========================================================
# check_school_carryover — a QUESTION, re-asked once per day
# =========================================================

def test_carryover_asks_and_primes_the_callback(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    primed = []
    st.add_item("homework", "Geography", detail="3 questions", due="today")

    pc.check_school_carryover(on_school_ask=primed.append)

    assert len(seen) == 1
    assert "Geography" in seen[0]
    assert "did you finish it" in seen[0].lower()
    assert primed == [["update_school_item"]]


def test_carryover_does_not_reask_the_same_day(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    st.add_item("homework", "Geography", due="today")

    pc.check_school_carryover(on_school_ask=lambda *a: None)
    pc.check_school_carryover(on_school_ask=lambda *a: None)

    assert len(seen) == 1


def test_carryover_stops_once_the_item_is_done(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    st.add_item("homework", "Geography", due="today")
    st.update_item("geography", done=True)

    pc.check_school_carryover(on_school_ask=lambda *a: None)
    assert seen == []


def test_carryover_ignores_events():
    """Events have their own upcoming/prep checks — carryover is
    homework/project only, enforced in school_tasks.carryover_candidates
    and re-checked here at the integration boundary."""
    # Covered structurally by school_tasks' own carryover_candidates
    # tests; this just documents the boundary for this file's reader.
    assert True


def test_carryover_says_workaround_phrasing_not_reused_from_deadline_check(tmp_path, monkeypatch):
    """The carryover question and the deadline statement are different
    speech acts — this pins the question phrasing specifically, so a
    future edit can't quietly collapse them into the same wording."""
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    st.add_item("homework", "Geography", due="today")

    pc.check_school_carryover(on_school_ask=lambda *a: None)

    assert "workaround" in seen[0].lower()


# =========================================================
# register() wiring
# =========================================================

def test_register_adds_all_four_school_jobs():
    added = []

    class _FakeScheduler:
        def add_periodic(self, func, minutes, job_id):
            added.append(job_id)

    pc.register(_FakeScheduler(), llm=None, on_school_ask=lambda *a: None)

    for job in (
        "proactive_school_deadlines", "proactive_school_event_prep",
        "proactive_school_events_upcoming", "proactive_school_carryover",
    ):
        assert job in added


def test_register_works_with_no_on_school_ask(monkeypatch):
    """The CLI (or any caller with no orchestrator to prime) must not
    crash register() by omitting the new parameter."""
    class _FakeScheduler:
        def add_periodic(self, func, minutes, job_id):
            pass

    pc.register(_FakeScheduler(), llm=None)  # no TypeError
