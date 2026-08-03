# Confirmed 2026-08-03: "add to today's tasks" had no tool behind it at
# all — active-priorities.md untouched since 2026-08-01, no daily/2026-08
# notes existed, despite FRED repeatedly saying tasks were "logged".
# daily_tasks.py is the fix; this checks its read/write round trip
# actually persists, since that's the exact thing that was silently not
# happening before.

from tools import daily_tasks


def test_add_creates_note_and_task_line(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)

    result = daily_tasks.add_task("Visit the docs", day="2026-08-03")

    assert "Visit the docs" in result
    path = tmp_path / "daily" / "2026-08" / "2026-08-03.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "## Tasks" in text
    assert "- [ ] Visit the docs" in text
    assert "Monday, August 03, 2026" in text


def test_add_twice_appends_both_without_losing_the_first(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)

    daily_tasks.add_task("First link", day="2026-08-03")
    daily_tasks.add_task("Second link", day="2026-08-03")

    listed = daily_tasks.list_tasks(day="2026-08-03")
    assert "[open] First link" in listed
    assert "[open] Second link" in listed


def test_complete_task_toggles_the_matching_line(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)

    daily_tasks.add_task("Maths activity", day="2026-08-03")
    daily_tasks.add_task("SS studying", day="2026-08-03")

    result = daily_tasks.complete_task("maths", day="2026-08-03")
    assert "complete" in result.lower()

    listed = daily_tasks.list_tasks(day="2026-08-03")
    assert "[done] Maths activity" in listed
    assert "[open] SS studying" in listed

    # Reversible — matches the earlier real conversation where Vatsal
    # corrected FRED from complete back to incomplete.
    daily_tasks.complete_task("maths", done=False, day="2026-08-03")
    listed = daily_tasks.list_tasks(day="2026-08-03")
    assert "[open] Maths activity" in listed


def test_complete_task_reports_no_match_instead_of_guessing(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)

    daily_tasks.add_task("Something else", day="2026-08-03")
    result = daily_tasks.complete_task("nonexistent thing", day="2026-08-03")

    assert "no task matching" in result.lower()


def test_list_tasks_with_nothing_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)

    assert daily_tasks.list_tasks(day="2026-08-03") == "No tasks logged for today."


def test_add_task_preserves_content_outside_the_tasks_section(tmp_path, monkeypatch):
    """A note that already has other content (e.g. a session_summary.py
    recap block) must survive add_task touching an unrelated section."""
    monkeypatch.setattr(daily_tasks, "VAULT_DIR", tmp_path)

    path = tmp_path / "daily" / "2026-08" / "2026-08-03.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntype: log\n---\n\n# Monday, August 03, 2026\n\n"
        "## FRED session recap — 13:00\n\nDid some stuff.\n",
        encoding="utf-8",
    )

    daily_tasks.add_task("A new task", day="2026-08-03")

    text = path.read_text(encoding="utf-8")
    assert "Did some stuff." in text
    assert "- [ ] A new task" in text
