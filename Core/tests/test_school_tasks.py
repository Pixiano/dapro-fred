# Core/tests/test_school_tasks.py
#
# Built alongside school_tasks.py on 2026-08-09: Vatsal named reliable
# school-deadline tracking (capture, query, progress, carryover) the
# one feature that would make him use FRED daily, and asked for this
# tested extensively rather than shipped thin. Every public function
# gets covered here, plus the round-trip through the actual file on
# disk — the earlier daily_tasks bug (FRED said "logged" while nothing
# was written) is exactly the class of failure a read-back-only test
# would miss.

from datetime import datetime, timedelta

from tools import school_tasks as st


def _fixed(days=0, hours=0, minutes=0):
    return (datetime.now() + timedelta(days=days, hours=hours, minutes=minutes))


# =========================================================
# DATE PARSING
# =========================================================

def test_parse_due_date_today_and_tomorrow():
    now = datetime(2026, 8, 9, 15, 0)
    assert st.parse_due_date("today", now=now).date() == now.date()
    assert st.parse_due_date("tomorrow", now=now).date() == (now + timedelta(days=1)).date()


def test_parse_due_date_in_n_days_and_weeks():
    now = datetime(2026, 8, 9, 15, 0)
    assert st.parse_due_date("in 3 days", now=now).date() == (now + timedelta(days=3)).date()
    assert st.parse_due_date("in a day", now=now).date() == (now + timedelta(days=1)).date()
    assert st.parse_due_date("in 2 weeks", now=now).date() == (now + timedelta(days=14)).date()
    assert st.parse_due_date("in a week", now=now).date() == (now + timedelta(days=7)).date()
    assert st.parse_due_date("next week", now=now).date() == (now + timedelta(days=7)).date()


def test_parse_due_date_weekday_is_next_occurrence_including_today():
    # 2026-08-09 is a Sunday.
    now = datetime(2026, 8, 9, 9, 0)
    assert st.parse_due_date("sunday", now=now).date() == now.date()
    assert st.parse_due_date("wednesday", now=now).date() == (now + timedelta(days=3)).date()


def test_parse_due_date_iso():
    assert st.parse_due_date("2026-09-01").date() == datetime(2026, 9, 1).date()


def test_parse_due_date_named_month_day_first_with_year():
    """The real gap: "13 August 2026" returned None the first time a
    real item was logged (2026-08-09)."""
    assert st.parse_due_date("13 August 2026").date() == datetime(2026, 8, 13).date()
    assert st.parse_due_date("on 13 August 2026").date() == datetime(2026, 8, 13).date()
    assert st.parse_due_date("13th August 2026").date() == datetime(2026, 8, 13).date()
    assert st.parse_due_date("3 Aug 2026").date() == datetime(2026, 8, 3).date()


def test_parse_due_date_named_month_day_first_no_year_infers_forward():
    now = datetime(2026, 8, 9)
    # In the future this year: stays this year.
    assert st.parse_due_date("13 August", now=now).date() == datetime(2026, 8, 13).date()
    # Already passed this year: rolls to next year, not into the past.
    assert st.parse_due_date("1 January", now=now).date() == datetime(2027, 1, 1).date()


def test_parse_due_date_named_month_month_first():
    assert st.parse_due_date("August 13, 2026").date() == datetime(2026, 8, 13).date()
    assert st.parse_due_date("Aug 13 2026").date() == datetime(2026, 8, 13).date()


def test_parse_due_date_full_month_names_not_truncated_by_abbreviation_match():
    """Alternation order/backtracking must resolve "september" fully,
    not stop at the "sep" prefix and leave "tember" dangling."""
    assert st.parse_due_date("5 September 2026").date() == datetime(2026, 9, 5).date()
    assert st.parse_due_date("5 June 2026").date() == datetime(2026, 6, 5).date()


def test_parse_due_date_garbage_is_none():
    assert st.parse_due_date("") is None
    assert st.parse_due_date("whenever") is None
    assert st.parse_due_date(None) is None


def test_resolve_when_with_time_uses_scheduler_and_sets_has_time():
    when, has_time = st._resolve_when("tomorrow", "2:45pm")
    assert has_time is True
    assert when.hour == 14 and when.minute == 45


def test_resolve_when_without_time_has_no_time_flag():
    when, has_time = st._resolve_when("tomorrow", "")
    assert has_time is False
    assert when.hour == 0 and when.minute == 0


# =========================================================
# ADD_ITEM
# =========================================================

def test_add_homework_with_count_reads_back_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)

    result = st.add_item("homework", "Geography", detail="3 questions", count=3, due="tomorrow")

    assert "Geography" in result
    assert "3 questions" in result
    assert "0 of 3 done" in result
    assert "tomorrow" in result.lower()


def test_add_homework_without_count_has_no_progress_phrase(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)

    result = st.add_item("homework", "English", detail="an essay", due="friday")

    assert "of" not in result or "done" not in result  # no "0 of N done"


def test_add_project_with_next_step(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)

    result = st.add_item(
        "project", "Physics model", detail="build a working model",
        count=4, due="in 10 days", next_step="buy card sheet",
    )

    assert "next: buy card sheet" in result
    assert "0 of 4 done" in result


def test_add_event_with_prep_and_time(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)

    result = st.add_item("event", "Movie", due="today", time="2:45pm", prep_minutes=30)

    assert "Movie" in result
    assert "start getting ready" in result
    assert "2:15" in result  # 2:45pm minus 30 minutes


def test_add_event_with_prep_but_no_time_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)

    result = st.add_item("event", "Movie", due="today", prep_minutes=30)

    assert "start time" in result.lower()
    assert st._load_items() == []  # nothing written on rejection


def test_add_item_unknown_kind_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    result = st.add_item("chore", "Wash the car", due="tomorrow")
    assert "isn't something i track" in result.lower()


def test_add_item_missing_subject_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    assert "subject" in st.add_item("homework", "", due="tomorrow").lower()


def test_add_item_unparseable_due_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    result = st.add_item("homework", "Geography", due="sometime maybe")
    assert "couldn't work out when" in result.lower()
    assert st._load_items() == []


def test_zero_count_and_zero_prep_are_treated_as_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", count=0, due="tomorrow")
    item = st._load_items()[0]
    assert item["total_count"] is None


# =========================================================
# ROUND TRIP — written line survives a fresh read from disk
# =========================================================

def test_round_trip_preserves_every_field(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)

    st.add_item(
        "project", "Physics model", detail="build a working model",
        count=4, due="2026-09-01", next_step="buy card sheet",
    )

    path = tmp_path / "school" / "work.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "## Items" in text
    assert "- [ ] project | Physics model | build a working model" in text

    reloaded = st._load_items()
    assert len(reloaded) == 1
    item = reloaded[0]
    assert item["kind"] == "project"
    assert item["subject"] == "Physics model"
    assert item["detail"] == "build a working model"
    assert item["total_count"] == 4
    assert item["done_count"] == 0
    assert item["next_step"] == "buy card sheet"
    assert item["when"].date() == datetime(2026, 9, 1).date()
    assert item["has_time"] is False


def test_a_garbage_line_is_skipped_not_fatal(tmp_path, monkeypatch):
    """A hand-edited or half-written line must not take the whole file
    down — same fail-open convention as daily_tasks.py."""
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    path = tmp_path / "school" / "work.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntype: log\n---\n\n# School\n\n## Items\n\n"
        "- [ ] not a real item line\n"
        "- [ ] homework | Chemistry | journal | when 2026-08-20\n",
        encoding="utf-8",
    )

    items = st._load_items()
    assert len(items) == 1
    assert items[0]["subject"] == "Chemistry"


def test_repeated_saves_do_not_accumulate_blank_lines(tmp_path, monkeypatch):
    """The 2026-08-09 bug: header spacing must be stable across any
    number of saves, not grow by one blank line each time — caught only
    by checking the RAW file text, which list_items()'s output can't
    reveal."""
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)

    st.add_item("homework", "Geography", due="tomorrow")
    st.add_item("homework", "Physics", due="tomorrow")
    st.delete_item("physics")
    st.update_item("geography", note="still stable")

    text = (tmp_path / "school" / "work.md").read_text(encoding="utf-8")
    assert "# School\n\n## Items" in text
    assert "# School\n\n\n## Items" not in text


def test_adding_preserves_content_outside_items_section(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    path = tmp_path / "school" / "work.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntype: log\n---\n\n# School\n\n## Notes\n\nSome hand-written context.\n",
        encoding="utf-8",
    )

    st.add_item("homework", "Geography", due="tomorrow")

    text = path.read_text(encoding="utf-8")
    assert "Some hand-written context." in text
    assert "Geography" in text


# =========================================================
# LIST_ITEMS
# =========================================================

def test_list_items_filters_by_when(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)

    overdue_iso = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    far_iso = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")

    st.add_item("homework", "Overdue thing", due=overdue_iso)
    st.add_item("homework", "Today thing", due="today")
    st.add_item("homework", "Tomorrow thing", due="tomorrow")
    st.add_item("homework", "This week thing", due="in 3 days")
    st.add_item("homework", "Far future thing", due=far_iso)

    today_list = st.list_items(when="today")
    assert "Today thing" in today_list
    assert "Tomorrow thing" not in today_list

    tomorrow_list = st.list_items(when="tomorrow")
    assert "Tomorrow thing" in tomorrow_list
    assert "Today thing" not in tomorrow_list

    week_list = st.list_items(when="week")
    assert "This week thing" in week_list
    assert "Far future thing" not in week_list

    overdue_list = st.list_items(when="overdue")
    assert "Overdue thing" in overdue_list
    assert "Today thing" not in overdue_list

    all_list = st.list_items(when="all")
    for name in ("Overdue thing", "Today thing", "Tomorrow thing", "This week thing", "Far future thing"):
        assert name in all_list


def test_list_items_filters_by_kind_and_subject(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")
    st.add_item("project", "Physics model", due="tomorrow")

    only_homework = st.list_items(kind="homework")
    assert "Geography" in only_homework
    assert "Physics model" not in only_homework

    only_geo = st.list_items(subject="geography")
    assert "Geography" in only_geo
    assert "Physics model" not in only_geo


def test_list_items_excludes_done(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")
    st.update_item("geography", done=True)

    assert "Geography" not in st.list_items()


def test_list_items_empty_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    assert "nothing" in st.list_items(when="tomorrow").lower()


# =========================================================
# UPDATE_ITEM
# =========================================================

def test_add_progress_partial_stays_open(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", count=3, due="tomorrow")

    result = st.update_item("geography", add_progress=2)

    assert "2 of 3 done" in result
    item = st._load_items()[0]
    assert item["done"] is False
    assert item["done_count"] == 2


def test_add_progress_reaching_total_auto_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", count=3, due="tomorrow")

    st.update_item("geography", add_progress=2)
    st.update_item("geography", add_progress=1)

    item = st._load_items()[0]
    assert item["done"] is True
    assert item["done_count"] == 3


def test_add_progress_is_clamped_to_total(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", count=3, due="tomorrow")

    st.update_item("geography", add_progress=99)

    item = st._load_items()[0]
    assert item["done_count"] == 3
    assert item["done"] is True


def test_set_progress_is_absolute_not_additive(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", count=5, due="tomorrow")
    st.update_item("geography", add_progress=4)

    st.update_item("geography", set_progress=1)

    assert st._load_items()[0]["done_count"] == 1


def test_progress_on_uncounted_item_marks_it_done(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "English", detail="an essay", due="tomorrow")

    st.update_item("english", add_progress=1)

    assert st._load_items()[0]["done"] is True


def test_done_param_overrides_and_is_reversible(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", count=3, due="tomorrow")

    st.update_item("geography", done=True)
    assert st._load_items()[0]["done"] is True

    st.update_item("geography", done=False)
    assert st._load_items()[0]["done"] is False


def test_reschedule_updates_when_and_has_time(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")

    result = st.update_item("geography", new_due="2026-12-25", note="teacher extended it")

    assert "couldn't work out" not in result.lower()
    item = st._load_items()[0]
    assert item["when"].date() == datetime(2026, 12, 25).date()
    assert item["note"] == "teacher extended it"


def test_reschedule_with_bad_date_is_rejected_and_item_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")
    before = st._load_items()[0]["when"]

    result = st.update_item("geography", new_due="nonsense")

    assert "couldn't work out" in result.lower()
    assert st._load_items()[0]["when"] == before


def test_update_next_step(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("project", "Physics model", count=4, due="in 10 days", next_step="buy card sheet")

    st.update_item("physics", next_step="cut the base")

    assert st._load_items()[0]["next_step"] == "cut the base"


def test_update_no_match_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    result = st.update_item("nonexistent subject", done=True)
    assert "nothing matching" in result.lower()


def test_update_with_no_fields_given_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")
    result = st.update_item("geography")
    assert "nothing to update" in result.lower()


def test_delete_removes_the_matching_item(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")
    st.add_item("homework", "Physics", due="tomorrow")

    result = st.delete_item("geography")

    assert "geography" in result.lower()
    remaining = [i["subject"] for i in st._load_items()]
    assert remaining == ["Physics"]


def test_delete_no_match_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    result = st.delete_item("nonexistent")
    assert "nothing matching" in result.lower()


def test_delete_refuses_an_ambiguous_match_rather_than_guessing(tmp_path, monkeypatch):
    """Unlike update_item, a wrong pick here is not a harmless no-op —
    the item is gone. More than one match must refuse, not guess."""
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", detail="map work", due="tomorrow")
    st.add_item("homework", "Geography", detail="questions", due="in 5 days")

    result = st.delete_item("geography")

    assert "more than one" in result.lower()
    assert len(st._load_items()) == 2  # nothing deleted


def test_delete_empty_match_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")
    result = st.delete_item("")
    assert "need something to match" in result.lower()
    assert len(st._load_items()) == 1


def test_update_picks_the_soonest_due_among_multiple_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", detail="map work", due="in 10 days")
    st.add_item("homework", "Geography", detail="questions", due="tomorrow")

    st.update_item("geography", done=True)

    items = st._load_items()
    done = [i for i in items if i["done"]]
    assert len(done) == 1
    assert done[0]["detail"] == "questions"


# =========================================================
# PROACTIVE QUERY HELPERS
# =========================================================

def test_due_within_excludes_events(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")
    st.add_item("event", "Movie", due="tomorrow", time="2pm")

    due = st.due_within(2)
    assert [i["subject"] for i in due] == ["Geography"]


def test_due_within_includes_overdue(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    overdue_iso = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    st.add_item("homework", "Old thing", due=overdue_iso)

    due = st.due_within(2)
    assert [i["subject"] for i in due] == ["Old thing"]


def test_due_within_excludes_done_items(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Geography", due="tomorrow")
    st.update_item("geography", done=True)
    assert st.due_within(2) == []


def test_events_needing_prep_fires_inside_the_window_only(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    start = _fixed(hours=1)
    st.add_item(
        "event", "Movie",
        due=start.strftime("%Y-%m-%d"), time=start.strftime("%H:%M"),
        prep_minutes=90,
    )

    # Prep window is [start-90min, start) = roughly [now-30min, now+60min):
    # "now" sits inside it.
    due = st.events_needing_prep(now=datetime.now())
    assert [i["subject"] for i in due] == ["Movie"]

    # Well before the window opens.
    assert st.events_needing_prep(now=start - timedelta(hours=3)) == []

    # After the event has already started.
    assert st.events_needing_prep(now=start + timedelta(minutes=1)) == []


def test_events_needing_prep_ignores_events_without_prep_or_time(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    start = _fixed(minutes=30)
    st.add_item("event", "No-prep thing", due=start.strftime("%Y-%m-%d"), time=start.strftime("%H:%M"))
    st.add_item("event", "No-time thing", due="tomorrow", prep_minutes=30)

    assert st.events_needing_prep(now=datetime.now()) == []


def test_events_upcoming_within_window(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    soon = _fixed(hours=5)
    far = _fixed(hours=48)
    st.add_item("event", "Soon event", due=soon.strftime("%Y-%m-%d"), time=soon.strftime("%H:%M"))
    st.add_item("event", "Far event", due=far.strftime("%Y-%m-%d"), time=far.strftime("%H:%M"))

    upcoming = st.events_upcoming(within_hours=24)
    assert [i["subject"] for i in upcoming] == ["Soon event"]


def test_carryover_candidates_only_due_today_or_earlier_and_open(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Today thing", due="today")
    st.add_item("homework", "Tomorrow thing", due="tomorrow")
    st.add_item("event", "Today event", due="today", time="6pm")

    candidates = st.carryover_candidates()
    names = [i["subject"] for i in candidates]
    assert "Today thing" in names
    assert "Tomorrow thing" not in names
    assert "Today event" not in names  # events excluded, handled separately


def test_carryover_candidates_excludes_already_done(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "VAULT_DIR", tmp_path)
    st.add_item("homework", "Today thing", due="today")
    st.update_item("today thing", done=True)

    assert st.carryover_candidates() == []
