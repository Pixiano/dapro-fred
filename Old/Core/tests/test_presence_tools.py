# Core/tests/test_presence_tools.py
#
# Pure aggregation-logic test against tools/presence_tools.py — synthetic
# JSONL lines written straight to a temp file, no camera/polling involved.

import json
from datetime import datetime, timedelta

from tools import presence_tools


def _write_log(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def test_active_hours_summary_ratios(tmp_path, monkeypatch):
    log_path = tmp_path / "presence_log.jsonl"
    monkeypatch.setattr(presence_tools, "LOG_PATH", log_path)

    now = datetime.now()
    records = []
    # Hour 10: 3 polls, 2 present -> 0.667
    for present in (True, True, False):
        records.append({"ts": now.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(), "present": present})
    # Hour 20: 1 poll, absent -> 0.0
    records.append({"ts": now.replace(hour=20, minute=0, second=0, microsecond=0).isoformat(), "present": False})
    _write_log(log_path, records)

    summary = presence_tools.active_hours_summary(days=7)

    assert summary["total_polls"] == 4
    assert summary["hours"][10] == round(2 / 3, 3)
    assert summary["hours"][20] == 0.0
    assert summary["hours"][3] == 0.0  # no data for this hour -> 0.0, not missing
    assert set(summary["hours"].keys()) == set(range(24))


def test_active_hours_summary_ignores_old_records(tmp_path, monkeypatch):
    log_path = tmp_path / "presence_log.jsonl"
    monkeypatch.setattr(presence_tools, "LOG_PATH", log_path)

    old_ts = (datetime.now() - timedelta(days=30)).isoformat()
    _write_log(log_path, [{"ts": old_ts, "present": True}])

    summary = presence_tools.active_hours_summary(days=7)

    assert summary["total_polls"] == 0


def test_active_hours_summary_missing_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(presence_tools, "LOG_PATH", tmp_path / "does_not_exist.jsonl")

    summary = presence_tools.active_hours_summary(days=7)

    assert summary["total_polls"] == 0
    assert summary["hours"][0] == 0.0


def test_describe_active_hours_reports_span(tmp_path, monkeypatch):
    log_path = tmp_path / "presence_log.jsonl"
    monkeypatch.setattr(presence_tools, "LOG_PATH", log_path)

    now = datetime.now()
    records = [
        {"ts": now.replace(hour=h).isoformat(), "present": True}
        for h in (9, 10, 11)
    ]
    _write_log(log_path, records)

    text = presence_tools.describe_active_hours(days=7)

    assert "sir" in text
    assert any(str(h) in text or f"{h:02d}" in text for h in (9, 10, 11)) or "AM" in text


def test_describe_active_hours_no_data(tmp_path, monkeypatch):
    monkeypatch.setattr(presence_tools, "LOG_PATH", tmp_path / "does_not_exist.jsonl")

    text = presence_tools.describe_active_hours(days=7)

    assert "enough presence data" in text
