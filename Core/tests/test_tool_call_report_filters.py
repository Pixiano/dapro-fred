# Core/tests/test_tool_call_report_filters.py
#
# The eval set for the fine-tune is built from this reader's output, so a
# filter that silently keeps the wrong rows trains on them. Two rules
# worth pinning:
#
#   - a known-bug artefact is excluded only up to the date it was fixed,
#     never after, or the exclusion would keep hiding real regressions;
#   - --since must not drop feedback rows, which carry no timestamp of
#     their own and are joined to tool rows by turn_id.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from tool_call_report import is_excluded, split_and_join


def _row(**kw):
    base = {"turn_id": "t1", "tool_called": "call_phone",
            "timestamp": "2026-08-15T18:42:10", "result_preview": "ok"}
    base.update(kw)
    return base


def test_confirmation_bug_rows_excluded_up_to_the_fix_date():
    bug = _row(result_preview="Cancelled by user")
    assert is_excluded(bug), "the bug's own rows must be excluded"


def test_same_result_after_the_fix_is_kept():
    # A genuine "no, don't call them" on a later date is real signal and
    # must survive, or the exclusion outlives the bug it describes.
    later = _row(result_preview="Cancelled by user",
                 timestamp="2026-08-20T10:00:00")
    assert not is_excluded(later)


def test_unrelated_rows_are_never_excluded():
    assert not is_excluded(_row(result_preview="Calling Mom now."))
    assert not is_excluded(_row(tool_called="get_current_time",
                                result_preview="It's 6:37 PM."))


def test_lockdown_exclusion_is_scoped_to_its_own_tool():
    needle = "Error running tool: (0, 'SetForegroundWindow', 'No e"
    assert is_excluded(_row(tool_called="lockdown_engage",
                            result_preview=needle,
                            timestamp="2026-08-14T16:22:00"))
    # Same string from a different tool is somebody else's bug.
    assert not is_excluded(_row(tool_called="take_screenshot",
                                result_preview=needle,
                                timestamp="2026-08-14T16:22:00"))


def test_feedback_rows_still_join_after_filtering():
    rows = [_row(turn_id="t9"), {"turn_id": "t9", "feedback": True,
                                 "interrupted": True}]
    joined = split_and_join(rows)
    assert len(joined) == 1
    _, fb = joined[0]
    assert fb and fb["interrupted"] is True
