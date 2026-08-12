# Core/tests/test_vault_relative_paths.py
#
# The model refers to vault files by their vault-relative path, because
# that is how MAP.md and the retrieval labels present them. Those paths
# were anchored to Documents/FRED instead: read_file said "not found" for
# a file that existed, and append_to_file created a phantom copy and
# reported success.

import os
from pathlib import Path

import tools.assist_tools as assist_tools
from tools.assist_tools import append_to_file, resolve_user_path
from tools.machine_tools import read_file

REL = "daily/2026-08/2026-08-04.md"


def _fake_vault(tmp_path, monkeypatch):
    """A vault with one real note, and Documents/FRED pointed somewhere safe."""
    vault, docs = tmp_path / "vault", tmp_path / "docs"
    note = vault / "daily" / "2026-08" / "2026-08-04.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Tuesday\n", encoding="utf-8")
    docs.mkdir()
    monkeypatch.setattr(assist_tools, "VAULT_DIR", vault)
    monkeypatch.setattr(assist_tools, "DEFAULT_DOCS", docs)
    return vault, docs, note


def test_an_existing_vault_path_resolves_into_the_vault(tmp_path, monkeypatch):
    vault, docs, note = _fake_vault(tmp_path, monkeypatch)
    assert resolve_user_path(REL) == note


def test_read_file_finds_it(tmp_path, monkeypatch):
    _fake_vault(tmp_path, monkeypatch)
    assert "Tuesday" in read_file(REL)


def test_append_writes_to_the_real_note_not_a_phantom(tmp_path, monkeypatch):
    vault, docs, note = _fake_vault(tmp_path, monkeypatch)

    append_to_file(REL, "a dictated line")

    assert "a dictated line" in note.read_text(encoding="utf-8")
    assert not (docs / "daily").exists(), "wrote a phantom copy under Documents/FRED"


def test_the_default_root_is_the_vault():
    """One root, not two: a bare name and an empty listing must land in
    the same place a vault-relative path does."""
    from config.settings import VAULT_DIR

    assert assist_tools.DEFAULT_DOCS == VAULT_DIR
    assert resolve_user_path("").resolve() == VAULT_DIR.resolve()
    assert resolve_user_path("notes.txt").parent.resolve() == VAULT_DIR.resolve()
    # "FRED/daily" must not nest the vault inside itself.
    assert resolve_user_path("FRED/daily").resolve() == (VAULT_DIR / "daily").resolve()


def test_a_new_file_still_goes_to_documents(tmp_path, monkeypatch):
    """The vault check must not capture files that don't exist there —
    a fresh shopping list is not a vault file."""
    vault, docs, _ = _fake_vault(tmp_path, monkeypatch)

    append_to_file("shopping-list.txt", "milk")

    assert (docs / "shopping-list.txt").exists()
    assert not (vault / "shopping-list.txt").exists()


def test_vatsaldapro_resolves_under_home():
    """"VatsalDaPro/x" is a home subfolder like Downloads or Documents,
    not anchored under the vault/Documents/FRED default."""
    home = Path(os.path.expanduser("~"))
    assert resolve_user_path("VatsalDaPro/Projects").resolve() == (home / "VatsalDaPro" / "Projects").resolve()
    # case-insensitive match, real on-disk casing preserved in the result
    assert resolve_user_path("vatsaldapro/Projects").resolve() == (home / "VatsalDaPro" / "Projects").resolve()
