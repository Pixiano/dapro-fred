# Due dates written into daily-note task lines.
#
# Confirmed 2026-08-04: asked "Check for todays tasks please", FRED
# answered "Open: Study SS (History)" and, when asked "Nothing else?",
# said "No other pending tasks, sir." Two journal deadlines were sitting
# in the previous day's note and only surfaced after Vatsal pushed back
# twice naming them himself.
#
# Two separate defects produced that, and both are covered here:
#   1. list_tasks only ever read TODAY's note, so anything logged
#      yesterday and still open was invisible (fixed by roll-forward).
#   2. Nothing parsed "due Thursday" out of a task line at all, so
#      nothing could warn before the deadline arrived. The existing
#      deadline check read a `deadline:` frontmatter field that no vault
#      file has ever used.

from datetime import datetime

from tools import daily_tasks


# Tuesday. Chosen so "due Thursday" is two days out and the weekday
# arithmetic is actually exercised rather than landing on today.
_TUESDAY = datetime(2026, 8, 4)


# ---------------------------------------------------------------
# parse_due
# ---------------------------------------------------------------

def test_parses_the_real_chemistry_journal_line():
    """The exact line from daily/2026-08/2026-08-03.md that went
    unnoticed. Thursday, from a Tuesday, is 2026-08-06."""
    due = daily_tasks.parse_due(
        "Chemistry journal completion — due Thursday in school", today=_TUESDAY
    )
    assert due == datetime(2026, 8, 6)


def test_parses_the_spoken_and_written_date_forms():
    cases = {
        "Physics journal — due tomorrow": datetime(2026, 8, 5),
        "hand in the essay due today": datetime(2026, 8, 4),
        "portfolio due 2026-08-10": datetime(2026, 8, 10),
        "due on Friday": datetime(2026, 8, 7),
        "report due by Monday": datetime(2026, 8, 10),
    }
    for text, expected in cases.items():
        assert daily_tasks.parse_due(text, today=_TUESDAY) == expected, text


def test_a_weekday_naming_today_means_today():
    """
    "due Thursday" said ON Thursday is due now, not in seven days.
    Rolling forward a full week would silence the warning on exactly
    the day it matters most.
    """
    thursday = datetime(2026, 8, 6)
    assert daily_tasks.parse_due("due Thursday", today=thursday) == thursday


def test_tasks_without_a_due_date_return_none():
    for text in ("Study SS (History)", "", "due diligence on the contract"):
        assert daily_tasks.parse_due(text, today=_TUESDAY) is None, text


# ---------------------------------------------------------------
# open_due_tasks — what the proactive check actually consumes
# ---------------------------------------------------------------

def _write_note(root, day, lines):
    path = root / "daily" / day[:7] / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: log\n---\n\n# Note\n\n## Tasks\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def test_open_due_task_from_an_earlier_day_is_still_surfaced(tmp_path, monkeypatch):
    """The whole point: the deadline was logged yesterday, and yesterday's
    note is not the one being read today."""
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)
    _write_note(tmp_path, "2026-08-03", [
        "- [ ] Chemistry journal completion — due Thursday in school",
        "- [x] Allen JEE live class",
    ])
    _write_note(tmp_path, "2026-08-04", ["- [ ] Study SS (History)"])

    due = daily_tasks.open_due_tasks(day="2026-08-04", within_days=2)

    assert [text for _date, text in due] == [
        "Chemistry journal completion — due Thursday in school"
    ]


def test_completed_tasks_are_never_warned_about(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)
    _write_note(tmp_path, "2026-08-03", [
        "- [x] Chemistry journal completion — due Thursday in school",
    ])
    assert daily_tasks.open_due_tasks(day="2026-08-04", within_days=2) == []


def test_a_task_completed_later_beats_the_earlier_open_entry(tmp_path, monkeypatch):
    """
    The same task appears open on Monday and done on Tuesday. The later
    day wins, so it must not be warned about — this is the same
    later-days-win rule list_tasks uses, and the two must agree or the
    spoken list and the proactive warning would contradict each other.
    """
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)
    line = "Physics journal — due tomorrow"
    _write_note(tmp_path, "2026-08-03", [f"- [ ] {line}"])
    _write_note(tmp_path, "2026-08-04", [f"- [x] {line}"])

    assert daily_tasks.open_due_tasks(day="2026-08-04", within_days=7) == []


def test_overdue_tasks_are_included_not_dropped(tmp_path, monkeypatch):
    """A missed deadline is more worth raising than an upcoming one."""
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)
    _write_note(tmp_path, "2026-08-03", ["- [ ] essay due 2026-08-01"])

    due = daily_tasks.open_due_tasks(day="2026-08-04", within_days=2)

    assert len(due) == 1
    assert due[0][0] == datetime(2026, 8, 1)


def test_distant_deadlines_are_not_raised_yet(tmp_path, monkeypatch):
    """Warning a week out on a same-week errand trains him to ignore it."""
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)
    _write_note(tmp_path, "2026-08-04", ["- [ ] portfolio due 2026-08-20"])

    assert daily_tasks.open_due_tasks(day="2026-08-04", within_days=2) == []


def test_results_are_sorted_soonest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)
    _write_note(tmp_path, "2026-08-04", [
        "- [ ] later thing due 2026-08-06",
        "- [ ] sooner thing due today",
    ])

    due = daily_tasks.open_due_tasks(day="2026-08-04", within_days=7)

    assert [text for _d, text in due] == ["sooner thing due today", "later thing due 2026-08-06"]


def test_list_tasks_still_rolls_forward(tmp_path, monkeypatch):
    """
    The roll-forward fix and the deadline parsing share _all_tasks; this
    pins the behaviour the shared helper was extracted from, so a change
    to one can't silently break the other.
    """
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)
    _write_note(tmp_path, "2026-08-03", ["- [ ] Chemistry journal completion"])
    _write_note(tmp_path, "2026-08-04", ["- [ ] Study SS (History)"])

    listed = daily_tasks.list_tasks(day="2026-08-04")

    assert "Open: Chemistry journal completion" in listed
    assert "Open: Study SS (History)" in listed
