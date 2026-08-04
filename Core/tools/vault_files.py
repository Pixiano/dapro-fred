# Core/tools/vault_files.py
#
# Name -> path lookup for every real file in the vault, so "open active
# priorities" (or "open activepriorities.md", or the file's own H1
# title) resolves instantly and correctly.
#
# "Hardcoded" here means resolved once from the real vault on disk into
# an in-memory table, not ~65 paths typed out by hand — that would go
# stale the moment a file is renamed or a new one is added, which is
# exactly the kind of drift rules.md warns against. Reuses
# vault_router.py's own file walk and utils/vault_md.py's H1 reader
# rather than re-implementing either.
#
# Root cause this exists for (confirmed 2026-08-03,
# session_2026-08-03.jsonl): read_file("active-priorities.md") failed
# with "File not found" because it resolved against the working
# directory, and launch_application/open_path's own bare-filename
# fallback (assist_tools._find_bare_filename) only checks
# Desktop/Downloads/Documents/Pictures — never the vault, which lives
# in a separate directory tree entirely (see VAULT_DIR in settings.py).

import os
from pathlib import Path

from config.settings import VAULT_DIR, VAULT_HARDCODED_FILES
from orchestrator.vault_router import _iter_vault_files
from utils.vault_md import strip_frontmatter, extract_h1_title

_index = None  # {normalized_key: Path} — built lazily, once per process


def _normalize(text: str) -> str:
    """
    Squash to lowercase alphanumerics only — no separators survive at
    all, so "active-priorities.md", "ActivePriorities", "active
    priorities", and "activepriorities.md" (no hyphen, as actually said
    in the confirmed bug — session_2026-08-03.jsonl) all normalize to
    the identical key regardless of which punctuation/spacing a person
    used. A space-preserving normalize was tried first and missed
    exactly that real transcript, since "activepriorities" (no space)
    never matched a key stored as "active priorities" (space) either by
    equality or substring.

    Ceiling: dropping word boundaries entirely means two unrelated
    short words can coincidentally concatenate into a substring of a
    longer key. resolve_vault_file's single-unambiguous-match rule is
    the guard for that — an over-eager match just becomes a decline
    (multiple hits) rather than picking the wrong file.
    """
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _add(index: dict, key: str, path: Path):
    norm = _normalize(key)
    if norm:
        index.setdefault(norm, path)


def _build_index() -> dict:
    index = {}

    for rel_path, path in _iter_vault_files():
        _add(index, path.name, path)
        _add(index, path.stem, path)
        _add(index, rel_path, path)
        try:
            title = extract_h1_title(strip_frontmatter(path.read_text(encoding="utf-8")))
        except OSError:
            title = ""
        if title:
            _add(index, title, path)

    for name in VAULT_HARDCODED_FILES:
        path = VAULT_DIR / name
        if path.exists():
            _add(index, name, path)
            _add(index, Path(name).stem, path)

    return index


def _get_index() -> dict:
    global _index
    if _index is None:
        _index = _build_index()
    return _index


def refresh_vault_index():
    """Force a rebuild on next lookup — call after adding/renaming a vault file."""
    global _index
    _index = None


def resolve_vault_file(name: str):
    """
    Look up a vault file by filename, stem, relative path, or H1
    title — all name forms above are normalized the same way, so
    punctuation/case never matters. Falls back to a substring match
    (single unambiguous hit only) for a shortened or "semantic" name
    that isn't an exact key. Returns a Path, or None if nothing matches.
    """
    if not name or not name.strip():
        return None

    index = _get_index()
    key = _normalize(name)

    if key in index:
        return index[key]

    matches = {path for norm, path in index.items() if key in norm}
    if len(matches) == 1:
        return matches.pop()

    return None


def open_vault_file(name: str) -> str:
    """Open a vault file with its default program, by name or title — no path needed."""
    path = resolve_vault_file(name)
    if path is None:
        return f"Couldn't find a vault file matching '{name}'."

    try:
        os.startfile(str(path))
    except Exception as e:
        return f"Couldn't open {path.name}: {e}"

    return f"Opened {path.name}."
