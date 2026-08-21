# Core/tests/test_vault_map.py
#
# scan_missing()'s exclusion logic (daily/ pattern, MAP.md itself) and
# append_missing()'s section-create-then-append behaviour, against a
# small fake vault under tmp_path — same VAULT_DIR-swap approach
# session_summary.py's own self-check uses for SESSIONS_DIR.

from datetime import datetime

from tools import vault_map


def _build_fake_vault(tmp_path):
    (tmp_path / "MAP.md").write_text(
        "# Map\n\n| File | Holds | Read when |\n|---|---|---|\n"
        "| [known.md](known.md) | Known stuff | Always |\n",
        encoding="utf-8",
    )
    (tmp_path / "known.md").write_text("# Known\nstuff\n", encoding="utf-8")
    (tmp_path / "orphan.md").write_text("# Orphan File\nnotes\n", encoding="utf-8")

    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "README.md").write_text("# Daily README\n", encoding="utf-8")
    (daily / "_TEMPLATE.md").write_text("# Template\n", encoding="utf-8")

    current_month = tmp_path / "daily" / datetime.now().strftime("%Y-%m")
    current_month.mkdir()
    (current_month / "2026-08-20.md").write_text("# Aug 20\n", encoding="utf-8")

    old_month = tmp_path / "daily" / "2020-01"
    old_month.mkdir()
    (old_month / "2020-01-01.md").write_text("# Old day\n", encoding="utf-8")

    return tmp_path


def test_scan_missing_excludes_map_and_old_daily_notes(monkeypatch, tmp_path):
    vault = _build_fake_vault(tmp_path)
    monkeypatch.setattr(vault_map, "VAULT_DIR", vault)

    missing = vault_map.scan_missing()

    # MAP.md never flags itself, known.md is in the table already, and
    # daily/README.md + daily/_TEMPLATE.md are always kept individually.
    assert "MAP.md" not in missing
    assert "known.md" not in missing
    assert "daily/README.md" in missing
    assert "daily/_TEMPLATE.md" in missing
    # Current month's daily note is flagged (not yet in MAP.md text)...
    current_rel = f"daily/{datetime.now().strftime('%Y-%m')}/2026-08-20.md"
    assert current_rel in missing
    # ...but an old month's daily note is covered by the pattern note in
    # MAP.md and must NOT be individually flagged.
    assert "daily/2020-01/2020-01-01.md" not in missing
    assert "orphan.md" in missing


def test_preview_missing_empty_when_nothing_missing(monkeypatch, tmp_path):
    (tmp_path / "MAP.md").write_text(
        "# Map\n\n| [known.md](known.md) | x | y |\n", encoding="utf-8"
    )
    (tmp_path / "known.md").write_text("# Known\n", encoding="utf-8")
    monkeypatch.setattr(vault_map, "VAULT_DIR", tmp_path)

    assert vault_map.scan_missing() == []
    assert vault_map.preview_missing() == ""


def test_append_missing_creates_then_appends_under_same_section(monkeypatch, tmp_path):
    vault = _build_fake_vault(tmp_path)
    monkeypatch.setattr(vault_map, "VAULT_DIR", vault)

    result = vault_map.append_missing()
    assert "Added" in result

    after_first = (vault / "MAP.md").read_text(encoding="utf-8")
    assert vault_map._UNFILED_HEADING in after_first
    assert "orphan.md" in after_first
    assert "Orphan File" in after_first  # H1-guess used as the Holds column
    assert after_first.count(vault_map._UNFILED_HEADING) == 1
    assert vault_map.scan_missing() == []  # nothing left to add

    # A brand-new orphan appears later — append_missing must add it
    # UNDER the existing section, not create a second one.
    (vault / "second_orphan.md").write_text("# Second Orphan\n", encoding="utf-8")
    result2 = vault_map.append_missing()
    assert "Added 1" in result2

    after_second = (vault / "MAP.md").read_text(encoding="utf-8")
    assert after_second.count(vault_map._UNFILED_HEADING) == 1
    assert "second_orphan.md" in after_second


def test_append_missing_noop_when_nothing_to_add(monkeypatch, tmp_path):
    (tmp_path / "MAP.md").write_text(
        "# Map\n\n| [known.md](known.md) | x | y |\n", encoding="utf-8"
    )
    (tmp_path / "known.md").write_text("# Known\n", encoding="utf-8")
    monkeypatch.setattr(vault_map, "VAULT_DIR", tmp_path)

    result = vault_map.append_missing()
    assert "Nothing to add" in result


if __name__ == "__main__":
    print("run via pytest: pytest Core/tests/test_vault_map.py")
