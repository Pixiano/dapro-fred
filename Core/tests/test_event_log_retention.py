# Session logs are one new file per launch with no cleanup — 45 files
# in a handful of days by 2026-08-02, on something meant to run all
# day. _prune_old_sessions is the fix; these confirm it keeps recent
# files and only removes what's actually old.

import time

from utils import event_log


def _touch(path, age_days):
    path.write_text('{"ts": "x"}\n', encoding="utf-8")
    old_time = time.time() - (age_days * 86400)
    import os
    os.utime(path, (old_time, old_time))


def test_old_sessions_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(event_log, "SESSION_DIR", tmp_path)

    old = tmp_path / "session_2020-01-01_00-00-00.jsonl"
    recent = tmp_path / "session_2026-08-01_00-00-00.jsonl"
    _touch(old, age_days=90)
    _touch(recent, age_days=1)

    event_log._prune_old_sessions()

    assert not old.exists()
    assert recent.exists()


def test_prune_on_missing_dir_does_not_raise(tmp_path):
    from utils import event_log as el
    import types

    fake_module = types.SimpleNamespace(SESSION_DIR=tmp_path / "does_not_exist")
    # Just exercise the real function against a non-existent dir directly.
    original = el.SESSION_DIR
    try:
        el.SESSION_DIR = fake_module.SESSION_DIR
        el._prune_old_sessions()  # must not raise
    finally:
        el.SESSION_DIR = original


def test_non_session_files_are_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(event_log, "SESSION_DIR", tmp_path)

    unrelated = tmp_path / "notes.txt"
    _touch(unrelated, age_days=90)

    event_log._prune_old_sessions()

    assert unrelated.exists()
