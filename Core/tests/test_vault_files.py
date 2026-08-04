# resolve_vault_file's matching rules, tested against a fake index
# (monkeypatched directly) rather than the real vault on disk — isolates
# the matching logic from vault content, which changes over time.
#
# Root cause this guards against (session_2026-08-03.jsonl):
# read_file("active-priorities.md") and open_path both failed because
# neither knew the vault directory existed; resolve_vault_file is the
# fix, and its matching must actually handle the phrasings that failed
# — including "activepriorities.md" (no hyphen), which a naive
# space-preserving normalize still misses (see _normalize's docstring).

from pathlib import Path

import tools.vault_files as vault_files


def _fake_index(monkeypatch, entries: dict):
    monkeypatch.setattr(vault_files, "_index", dict(entries))


def test_exact_filename_and_stem_match(monkeypatch):
    path = Path("/vault/active-priorities.md")
    _fake_index(monkeypatch, {
        vault_files._normalize("active-priorities.md"): path,
        vault_files._normalize("active-priorities"): path,
    })
    assert vault_files.resolve_vault_file("active-priorities.md") == path
    assert vault_files.resolve_vault_file("active-priorities") == path


def test_no_hyphen_no_space_still_matches():
    # The actual failing transcript: "open up the file called
    # activepriorities.md" — no hyphen, no space between the words.
    assert vault_files._normalize("active-priorities.md") == vault_files._normalize(
        "activepriorities.md"
    )


def test_title_match(monkeypatch):
    path = Path("/vault/personal/goals.md")
    _fake_index(monkeypatch, {
        vault_files._normalize("goals"): path,
        vault_files._normalize("Priority order"): path,
    })
    assert vault_files.resolve_vault_file("priority order") == path


def test_unambiguous_substring_fallback(monkeypatch):
    path = Path("/vault/active-priorities.md")
    _fake_index(monkeypatch, {vault_files._normalize("active-priorities"): path})
    assert vault_files.resolve_vault_file("priorities") == path


def test_ambiguous_substring_declines(monkeypatch):
    _fake_index(monkeypatch, {
        vault_files._normalize("active-priorities"): Path("/vault/active-priorities.md"),
        vault_files._normalize("goals priority order"): Path("/vault/personal/goals.md"),
    })
    assert vault_files.resolve_vault_file("priorit") is None


def test_no_match_returns_none(monkeypatch):
    _fake_index(monkeypatch, {
        vault_files._normalize("active-priorities"): Path("/vault/active-priorities.md"),
    })
    assert vault_files.resolve_vault_file("spotify") is None


def test_blank_name_returns_none(monkeypatch):
    _fake_index(monkeypatch, {
        vault_files._normalize("active-priorities"): Path("/vault/active-priorities.md"),
    })
    assert vault_files.resolve_vault_file("   ") is None
