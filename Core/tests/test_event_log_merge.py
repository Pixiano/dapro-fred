# Session logs used to be one file per launch (session_2026-08-02_11-
# 46-12.jsonl), unbounded and never cleaned up -- 45 files in a
# handful of days. Explicit decision: keep it unbounded (no retention,
# no deletion -- the logs are meant to be a complete record), but stop
# multiplying files. One file per DATE now; same-day launches append to
# it, and pre-existing per-launch files get merged into per-date files
# once, automatically.

from utils import event_log


def test_same_day_launches_share_one_file(tmp_path, monkeypatch):
    monkeypatch.setattr(event_log, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(event_log, "_path", None)

    path1 = event_log.start_session()
    event_log.log("user_speech", text="first launch today")

    monkeypatch.setattr(event_log, "_path", None)
    path2 = event_log.start_session()
    event_log.log("user_speech", text="second launch today")

    assert path1 == path2
    lines = path1.read_text(encoding="utf-8").splitlines()
    assert sum(1 for l in lines if "first launch today" in l) == 1
    assert sum(1 for l in lines if "second launch today" in l) == 1


def test_legacy_per_launch_files_are_merged_into_one_per_date(tmp_path, monkeypatch):
    monkeypatch.setattr(event_log, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(event_log, "_path", None)

    (tmp_path / "session_2026-07-31_09-00-00.jsonl").write_text(
        '{"ts": "2026-07-31T09:00:00", "type": "user_speech", "text": "morning"}\n',
        encoding="utf-8",
    )
    (tmp_path / "session_2026-07-31_18-30-00.jsonl").write_text(
        '{"ts": "2026-07-31T18:30:00", "type": "user_speech", "text": "evening"}\n',
        encoding="utf-8",
    )
    # A different date must end up in its own file, not merged in.
    (tmp_path / "session_2026-08-01_10-00-00.jsonl").write_text(
        '{"ts": "2026-08-01T10:00:00", "type": "user_speech", "text": "next day"}\n',
        encoding="utf-8",
    )

    event_log._merge_legacy_sessions()

    assert not list(tmp_path.glob("session_*_*-*-*.jsonl")), "legacy files should be gone"

    merged_31 = (tmp_path / "session_2026-07-31.jsonl").read_text(encoding="utf-8")
    assert "morning" in merged_31
    assert "evening" in merged_31
    assert merged_31.index("morning") < merged_31.index("evening"), "must stay chronological"

    merged_01 = (tmp_path / "session_2026-08-01.jsonl").read_text(encoding="utf-8")
    assert "next day" in merged_01
    assert "morning" not in merged_01


def test_merge_is_a_safe_noop_once_nothing_legacy_remains(tmp_path, monkeypatch):
    monkeypatch.setattr(event_log, "SESSION_DIR", tmp_path)
    (tmp_path / "session_2026-08-02.jsonl").write_text(
        '{"ts": "x", "type": "system", "note": "session start"}\n', encoding="utf-8"
    )

    event_log._merge_legacy_sessions()  # must not touch or duplicate anything

    files = list(tmp_path.glob("session_*.jsonl"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").count("session start") == 1


def test_no_retention_old_files_survive(tmp_path, monkeypatch):
    """Explicit: nothing in this module deletes anything by age any more."""
    import os
    import time

    monkeypatch.setattr(event_log, "SESSION_DIR", tmp_path)
    ancient = tmp_path / "session_2020-01-01.jsonl"
    ancient.write_text('{"ts": "x"}\n', encoding="utf-8")
    old_time = time.time() - (400 * 86400)
    os.utime(ancient, (old_time, old_time))

    monkeypatch.setattr(event_log, "_path", None)
    event_log.start_session()

    assert ancient.exists()
