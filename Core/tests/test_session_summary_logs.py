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


def test_llm_prose_prompt_does_not_leak_the_tool_tally(tmp_path, monkeypatch):
    """Confirmed 2026-08-21: handing the raw "Tools used: X (n), Y (n)"
    tally to a local model alongside the asks list invited it to just
    echo the tally back as its "summary" instead of writing real prose —
    exactly the "just lists tool names" complaint. The tool tally must
    only reach the no-llm fallback text, never the LLM prompt."""
    monkeypatch.setattr(session_summary, "SESSIONS_DIR", tmp_path)
    _write(tmp_path, "session_2026-08-04.jsonl", EVENTS)

    seen = {}

    class _FakeLLM:
        def generate(self, messages, local_only=False):
            seen["messages"] = messages
            return "Worked through today's tasks."

    result = session_summary.summarise_today("2026-08-04", llm=_FakeLLM())

    assert result == "Worked through today's tasks."
    user_content = seen["messages"][1]["content"]
    assert "Tools used" not in user_content
    assert "list_tasks" not in user_content


def test_start_daily_session_is_once_per_day_not_per_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(session_summary, "VAULT_DIR", tmp_path)

    first = session_summary.start_daily_session("2026-08-16")
    assert first  # something to announce on the first launch of the day
    note = session_summary._daily_note_path("2026-08-16").read_text(encoding="utf-8")
    assert note.count(session_summary._auto_session_marker("2026-08-16")) == 1
    assert note.count(session_summary._AUTO_SESSION_HEADING) == 1

    # A relaunch later the same day should resume, not fork a new block.
    second = session_summary.start_daily_session("2026-08-16")
    assert second == ""
    note_after = session_summary._daily_note_path("2026-08-16").read_text(encoding="utf-8")
    assert note_after.count(session_summary._AUTO_SESSION_HEADING) == 1


def test_save_session_summary_logs_into_the_days_session_block(tmp_path, monkeypatch):
    monkeypatch.setattr(session_summary, "VAULT_DIR", tmp_path)

    session_summary.start_daily_session("2026-08-16")
    result = session_summary.save_session_summary("2026-08-16", summary="did a thing")

    note = session_summary._daily_note_path("2026-08-16").read_text(encoding="utf-8")
    assert "did a thing" in note
    # Logged inside the existing session block, not as a second top-level heading.
    assert note.count(session_summary._AUTO_SESSION_HEADING) == 1
    assert "session block" in result.lower()

    # The recap must land after the block's heading, not before it.
    heading_pos = note.find(session_summary._AUTO_SESSION_HEADING)
    recap_pos = note.find("did a thing")
    assert heading_pos < recap_pos
