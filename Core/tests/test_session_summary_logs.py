# Core/tests/test_session_summary_logs.py
#
# The glob in _today_logs matched only the pre-consolidation per-launch
# filenames, so summarise_today reported an empty day every day.

import json

from tools import session_summary


def _write(dir_, name, events):
    (dir_ / name).write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )


EVENTS = [
    {"type": "system", "note": "session start"},
    {"type": "user_speech", "text": "What did we do today??"},
    {"type": "tool_call", "tool": "list_tasks"},
    {"type": "system", "note": "session start"},
    {"type": "user_speech", "text": "And now?"},
]


def test_the_consolidated_one_file_per_day_log_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(session_summary, "SESSIONS_DIR", tmp_path)
    _write(tmp_path, "session_2026-08-04.jsonl", EVENTS)

    data = session_summary.collect_today("2026-08-04")
    assert data["asks"] == ["What did we do today??", "And now?"]
    assert data["tools"] == {"list_tasks": 1}
    # Restarts, not files — one file per day would otherwise always be 1.
    assert data["sessions"] == 2


def test_legacy_per_launch_files_still_count(tmp_path, monkeypatch):
    monkeypatch.setattr(session_summary, "SESSIONS_DIR", tmp_path)
    _write(tmp_path, "session_2026-08-04_11-46-12.jsonl", EVENTS[:3])

    assert session_summary.collect_today("2026-08-04")["asks"] == ["What did we do today??"]


def test_a_genuinely_empty_day_still_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(session_summary, "SESSIONS_DIR", tmp_path)
    assert session_summary.summarise_today("2026-08-04") == "Nothing logged today yet, sir."
