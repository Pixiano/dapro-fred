# Synthetic fixture covering both row kinds tool_call_report.py joins:
# a tool-call row (has "tool_called") and a feedback row ("feedback":
# true, joined back by turn_id). No real session log is quoted here.

import json

from scripts import tool_call_report as report

ROWS = [
    # turn 1: clean success, no feedback row at all
    {"turn_id": "t1", "tool_called": "get_weather", "utterance": "weather?",
     "arguments": {}, "result_preview": "Sunny, 72F", "result_error": False},
    # turn 2: tool errored
    {"turn_id": "t2", "tool_called": "open_path", "utterance": "open x",
     "arguments": {"path": "x"}, "result_preview": "Couldn't find x", "result_error": True},
    # turn 3: tool succeeded but the user interrupted the reply
    {"turn_id": "t3", "tool_called": "web_search", "utterance": "search y",
     "arguments": {"query": "y"}, "result_preview": "top result...", "result_error": False},
    {"turn_id": "t3", "feedback": True, "interrupted": True, "note": ""},
    # turn 4: second get_weather call, also errored, to make the rate 50%
    {"turn_id": "t4", "tool_called": "get_weather", "utterance": "weather??",
     "arguments": {}, "result_preview": "Error: no location set", "result_error": True},
]


def _write_log(tmp_path):
    path = tmp_path / "tool_call_log.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for row in ROWS:
            f.write(json.dumps(row) + "\n")
    return path


def test_missing_file_reports_no_data(tmp_path, capsys):
    import sys
    missing = tmp_path / "nope.jsonl"
    sys.argv = ["tool_call_report.py", "--log-path", str(missing)]
    report.main()
    assert "no data yet" in capsys.readouterr().out


def test_join_matches_feedback_to_its_tool_row(tmp_path):
    path = _write_log(tmp_path)
    joined = report.split_and_join(report.load_rows(path))
    by_turn = {row["turn_id"]: fb for row, fb in joined}
    assert by_turn["t1"] is None
    assert by_turn["t3"]["interrupted"] is True
    assert by_turn["t2"] is None


def test_aggregate_counts_calls_errors_and_interrupted(tmp_path):
    path = _write_log(tmp_path)
    joined = report.split_and_join(report.load_rows(path))
    stats = report.aggregate(joined)

    assert stats["get_weather"]["calls"] == 2
    assert stats["get_weather"]["errors"] == 1
    assert stats["open_path"]["errors"] == 1
    assert stats["web_search"]["interrupted"] == 1
    assert stats["web_search"]["errors"] == 0


def test_errors_only_filter_includes_interrupted_non_errors(tmp_path):
    path = _write_log(tmp_path)
    joined = report.split_and_join(report.load_rows(path))
    matched = [
        row["turn_id"] for row, fb in joined
        if row.get("result_error") or bool(fb and fb.get("interrupted"))
    ]
    assert set(matched) == {"t2", "t3", "t4"}
