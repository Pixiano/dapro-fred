# parse_when() regressions. Real bug (session_2026-08-03.jsonl,
# tool_call_log.jsonl): the model correctly resolved "Wednesday at
# 5:55pm" itself and called schedule_reminder(when="2026-08-05 17:55"),
# but _CLOCK_RE is unanchored and greedily matched "20" out of "2026"
# as the hour, producing "tomorrow at 8:00 PM" — nowhere near what was
# asked, from an input that was itself perfectly well-formed.

from datetime import datetime

from orchestrator.scheduler import parse_when

# A fixed Monday, so weekday arithmetic in these tests is deterministic
# regardless of when they're actually run.
_NOW = datetime(2026, 8, 3, 22, 0)  # Monday, 10pm


def test_iso_date_with_time_is_not_mistaken_for_a_bare_clock_time():
    result = parse_when("2026-08-05 17:55", now=_NOW)
    assert result == datetime(2026, 8, 5, 17, 55)


def test_iso_date_in_the_past_is_rejected_not_rolled_forward():
    # An explicit calendar date is unambiguous — unlike a bare clock
    # time, there's nothing sensible to roll it forward to.
    assert parse_when("2026-08-01 09:00", now=_NOW) is None


def test_weekday_name_resolves_to_the_next_occurrence():
    # _NOW is Monday; Wednesday is 2 days out, Friday is 4.
    assert parse_when("wednesday at 5:55pm", now=_NOW) == datetime(2026, 8, 5, 17, 55)
    assert parse_when("friday at 5:55pm", now=_NOW) == datetime(2026, 8, 7, 17, 55)


def test_weekday_name_today_still_rolls_forward_if_time_has_passed():
    # _NOW is Monday 10pm; "monday at 9am" has already passed today, so
    # it rolls forward one day same as any other already-passed time.
    result = parse_when("monday at 9am", now=_NOW)
    assert result == datetime(2026, 8, 4, 9, 0)


def test_plain_clock_time_unaffected_by_the_new_branches():
    # _NOW is Monday 10pm — 7pm today has already passed, so this rolls
    # forward one day same as before the ISO/weekday branches existed.
    assert parse_when("7pm", now=_NOW) == datetime(2026, 8, 4, 19, 0)
    assert parse_when("tomorrow at 8:30am", now=_NOW) == datetime(2026, 8, 4, 8, 30)
