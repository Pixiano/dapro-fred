# workout_plan reads the training split out of the vault PDF instead of
# hardcoding it, and that choice is the thing worth testing. The PDF is
# named workout_split_June.pdf — the month in the name says it gets
# revised — so a hardcoded "Monday is legs" would be right today and
# quietly wrong the first time it's rewritten, with nothing to catch the
# drift. test_a_different_split_parses_correctly below is the one that
# actually proves the file is being read rather than remembered.
#
# The other easy way to get this wrong is the extraction shape. pypdf
# emits the schedule table one CELL per line, not one row per line — the
# "MON TUE WED..." arrangement is the visual layout and matches nothing
# in the extracted text. The synthetic fixtures here are in the real
# extracted shape for that reason.
#
# Rest days are deliberately ABSENT from the dict rather than stored as
# "REST": a 4:55pm "Workout - REST" notification every Thursday is noise,
# and the split is explicit that Thursday and Sunday are walk/mobility
# only.

from datetime import datetime

import pytest

from tools import workout_plan

# The real pypdf output for the current split, one cell per line.
REAL_SHAPE = (
    "MON\nTUE\nWED\nTHU\nFRI\nSAT\nSUN\n"
    "Legs\nChest\nBack\nREST\nShoulders\nArms + Core\nREST\n"
)

CURRENT_SPLIT = {
    "mon": "Legs",
    "tue": "Chest",
    "wed": "Back",
    "fri": "Shoulders",
    "sat": "Arms + Core",
}

_VAULT_PDF = workout_plan.VAULT_DIR / workout_plan.DEFAULT_PDF

# The vault lives outside the repo, so it isn't there on a fresh clone or
# a CI box. Everything that touches the real file is skipped rather than
# failed — the parser tests below still run and still cover the logic.
needs_vault = pytest.mark.skipif(
    not _VAULT_PDF.is_file(),
    reason=f"vault PDF not present at {workout_plan.DEFAULT_PDF}",
)


class FakeScheduler:
    """
    Records what schedule_workouts would register. Standing in for the
    real ReminderScheduler keeps this test off the persistent SQLite
    jobstore — a test that actually scheduled would leave five 4:55pm
    reminders behind in the developer's own FRED.
    """

    def __init__(self):
        self.recurring = []
        self.cancelled = []

    def schedule_recurring(self, message=None, when=None, days=None,
                           hour=None, minute=None, job_id=None):
        self.recurring.append(
            {"message": message, "days": days, "hour": hour,
             "minute": minute, "job_id": job_id}
        )
        return "ok"

    def cancel_job_id(self, job_id):
        self.cancelled.append(job_id)
        return True


# =========================================================
# parse_split — no vault required
# =========================================================

def test_parses_the_real_extraction_shape():
    assert workout_plan.parse_split(REAL_SHAPE) == CURRENT_SPLIT


def test_rest_days_are_dropped_not_recorded():
    """No key at all for Thursday/Sunday — schedule_workouts iterates the
    dict, so a "REST" value would become a reminder to train."""
    split = workout_plan.parse_split(REAL_SHAPE)
    assert "thu" not in split
    assert "sun" not in split


def test_a_different_split_parses_correctly():
    """
    The point of parsing the PDF at all: when Vatsal rewrites the split,
    re-running picks the change up. If this ever fails while the test
    above passes, the parser has been "simplified" into something that
    only recognises the current routine.
    """
    revised = (
        "MON\nTUE\nWED\nTHU\nFRI\nSAT\nSUN\n"
        "REST\nPush\nPull\nLegs\nREST\nUpper + Core\nConditioning\n"
    )
    assert workout_plan.parse_split(revised) == {
        "tue": "Push",
        "wed": "Pull",
        "thu": "Legs",
        "sat": "Upper + Core",
        "sun": "Conditioning",
    }


def test_no_table_returns_empty_rather_than_guessing():
    # A partial guess would schedule five confidently mislabelled
    # reminders, which is worse than saying nothing was found.
    assert workout_plan.parse_split("nonsense with no table") == {}


def test_a_cell_containing_spaces_survives():
    # "Arms + Core" is one cell. One-cell-per-line handles it for free;
    # any whitespace-splitting parse would shred it into three days.
    assert workout_plan.parse_split(REAL_SHAPE)["sat"] == "Arms + Core"


# =========================================================
# The real vault PDF — end to end through pypdf
# =========================================================

@needs_vault
def test_real_pdf_extracts_the_current_split():
    pytest.importorskip("pypdf")
    assert workout_plan.get_split() == CURRENT_SPLIT


@needs_vault
def test_describe_split_names_the_training_days_and_the_rest_days():
    pytest.importorskip("pypdf")
    described = workout_plan.describe_split()
    assert "Monday: Legs" in described
    assert "Rest on Thursday and Sunday" in described


@needs_vault
def test_today_workout_matches_whatever_day_it_actually_is():
    pytest.importorskip("pypdf")
    split = workout_plan.get_split()
    today = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[
        datetime.now().weekday()
    ]
    answer = workout_plan.today_workout()

    if today in split:
        assert answer == f"Today is {split[today]}."
    else:
        assert "rest day" in answer.lower()


# =========================================================
# schedule_workouts — fed a synthetic PDF so it runs without
# the vault, since the wiring is what's under test here
# =========================================================

def _scheduled(monkeypatch, text=REAL_SHAPE, **kwargs):
    monkeypatch.setattr(workout_plan, "_pdf_text", lambda rel_path=None: text)
    fake = FakeScheduler()
    result = workout_plan.schedule_workouts(fake, **kwargs)
    return fake, result


def test_one_reminder_per_training_day_labelled_with_its_focus(monkeypatch):
    """The label is the whole point — "Workout" alone doesn't tell you
    what to pack for, "Workout - Legs" does."""
    fake, _ = _scheduled(monkeypatch)

    assert len(fake.recurring) == 5
    by_day = {call["days"]: call for call in fake.recurring}
    assert set(by_day) == set(CURRENT_SPLIT)
    for day, focus in CURRENT_SPLIT.items():
        assert by_day[day]["message"] == f"Workout - {focus}"


def test_job_ids_are_stable_per_weekday(monkeypatch):
    # Stable ids are what make a re-run replace rather than stack — see
    # replace_existing in test_recurring_reminders.py.
    fake, _ = _scheduled(monkeypatch)
    assert sorted(call["job_id"] for call in fake.recurring) == [
        "workout_fri", "workout_mon", "workout_sat", "workout_tue", "workout_wed",
    ]


def test_rest_days_have_their_old_reminder_retired(monkeypatch):
    """A day that becomes a rest day must lose its reminder, or the old
    one keeps firing forever with a stale label."""
    fake, _ = _scheduled(monkeypatch)
    assert "workout_thu" in fake.cancelled
    assert "workout_sun" in fake.cancelled


def test_default_time_is_five_minutes_before_the_session(monkeypatch):
    # 16:55, not 17:00 — a reminder arriving as the session starts is too
    # late to act on.
    fake, _ = _scheduled(monkeypatch)
    for call in fake.recurring:
        assert (call["hour"], call["minute"]) == (16, 55)


def test_an_unreadable_plan_schedules_nothing(monkeypatch):
    fake, result = _scheduled(monkeypatch, text="nonsense with no table")
    assert fake.recurring == []
    assert "couldn't find the schedule table" in result.lower()
