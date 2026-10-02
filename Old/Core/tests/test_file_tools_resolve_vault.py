# The vault-relative path is how the model names files, because that is
# how MAP.md presents them. read_file was anchored to the working
# directory and fixed on 2026-08-04; delete_file, move_file and
# rename_file had the same bug and were missed, so "delete
# personal/<name>.md" answered "Path not found" for a file that existed.
# For delete_file that was the benign half of the failure: the dangerous
# half is that a relative path pointing at something real under the
# LAUNCH directory would have been deleted instead.

import tools.assist_tools as assist_tools
import tools.machine_tools as machine_tools
from tools.machine_tools import delete_file, move_file, rename_file

REL = "personal/notes.md"


def _vault(tmp_path, monkeypatch):
    """A vault with one note, plus a decoy at the same relative path in
    the working directory — the thing a bare Path() would have hit."""
    vault, cwd = tmp_path / "vault", tmp_path / "cwd"
    note = vault / "personal" / "notes.md"
    note.parent.mkdir(parents=True)
    note.write_text("real\n", encoding="utf-8")

    decoy = cwd / "personal" / "notes.md"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("decoy\n", encoding="utf-8")

    monkeypatch.setattr(assist_tools, "VAULT_DIR", vault)
    monkeypatch.setattr(assist_tools, "DEFAULT_DOCS", vault)
    monkeypatch.chdir(cwd)
    return note, decoy


def test_delete_hits_the_vault_file(tmp_path, monkeypatch):
    note, decoy = _vault(tmp_path, monkeypatch)

    result = delete_file(REL)

    assert not note.exists(), result
    assert decoy.exists(), "deleted the working-directory file instead"


def test_delete_reports_a_genuine_miss(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    assert "not found" in delete_file("personal/nope.md").lower()


def test_rename_hits_the_vault_file(tmp_path, monkeypatch):
    note, decoy = _vault(tmp_path, monkeypatch)

    rename_file(REL, "renamed.md")

    assert (note.parent / "renamed.md").exists()
    assert decoy.exists() and decoy.read_text(encoding="utf-8") == "decoy\n"


def test_move_resolves_both_ends(tmp_path, monkeypatch):
    note, decoy = _vault(tmp_path, monkeypatch)

    move_file(REL, "archive/notes.md")

    assert (note.parent.parent / "archive" / "notes.md").exists()
    assert not note.exists()
    assert decoy.exists()
