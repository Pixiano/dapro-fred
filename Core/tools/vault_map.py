# Core/tools/vault_map.py
#
# MAP.md gap detection: which vault .md files aren't listed in MAP.md
# yet. Propose/write split exactly like session_summary.py — rules.md
# requires MAP.md edits to be proposed and confirmed, never written
# unattended (including from sleep-mode consolidation).
#
# Exclusion rules mirror check-vault.ps1's "map parity" check (section
# 1 of that script) — same vault, same intent, just needed from Python
# too (orchestrator/consolidation.py runs at sleep-mode boundaries, not
# from a manually-run PowerShell script).

from datetime import datetime
from pathlib import Path

from config.settings import VAULT_DIR
from utils.vault_md import strip_frontmatter, extract_h1_title

# check-vault.ps1 keeps these two daily/ files individually, and treats
# every other daily/* file as covered by the "current month" pattern
# note in MAP.md rather than needing its own row.
_DAILY_KEEP = {"daily/README.md", "daily/_TEMPLATE.md"}


def _current_month_prefix() -> str:
    return f"daily/{datetime.now().strftime('%Y-%m')}/"


def scan_missing() -> list:
    """Vault-relative paths (forward-slash) of every .md file not
    mentioned anywhere in MAP.md's own text — same rule set as
    check-vault.ps1's map-parity check. Empty list if the vault or
    MAP.md itself is missing."""
    map_path = VAULT_DIR / "MAP.md"
    if not VAULT_DIR.exists() or not map_path.exists():
        return []

    map_text = map_path.read_text(encoding="utf-8")
    current_month = _current_month_prefix()
    missing = []

    for path in sorted(VAULT_DIR.rglob("*.md")):
        rel = str(path.relative_to(VAULT_DIR)).replace("\\", "/")
        if rel == "MAP.md":
            continue
        if rel.startswith("daily/") and rel not in _DAILY_KEEP and not rel.startswith(current_month):
            continue
        if rel not in map_text:
            missing.append(rel)

    return missing


def preview_missing() -> str:
    """One short spoken-length line naming the gap, without writing
    anything. Empty string if nothing's missing — callers treat that as
    'no gap to mention'."""
    missing = scan_missing()
    if not missing:
        return ""

    names = ", ".join(missing[:8])
    more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
    return (
        f"{len(missing)} vault file(s) aren't listed in MAP.md yet: "
        f"{names}{more}. Say add them to log placeholder entries."
    )


_UNFILED_HEADING = "## Unfiled — pending categorization"


def _guess_holds(path: Path) -> str:
    """Best-effort 'Holds' column from the file's own H1 title — a
    guess, not real categorization, but better than a blank TBD."""
    try:
        title = extract_h1_title(strip_frontmatter(path.read_text(encoding="utf-8")))
    except OSError:
        title = ""
    return title or "TBD"


def append_missing(auto: bool = False) -> str:
    """
    Appends one placeholder row per currently-missing file under a flat
    'Unfiled — pending categorization' section (created once, appended
    under thereafter) — no attempt at correct-table placement, that's a
    human/FRED-in-conversation job, not this function's.

    The only function in this module that writes. Reads MAP.md's own
    current text first (`text` below) both to find/create the Unfiled
    section and, via scan_missing(), to skip files already listed — so
    a second auto-write the same day never re-adds the same row.

    auto: True when called unattended from consolidation.on_sleep_enter()
    (no spoken "add them" confirmation first, per Vatsal's 2026-08-22
    request) — tags each new row's "Read when" cell so it's clear later
    which entries were unattended vs. added via the manual
    _add_missing_map_entries tool (auto=False, its default).

    # ponytail: always appends at end-of-file once the Unfiled section
    # exists, on the assumption nothing else follows it. Fine while this
    # is the last section in MAP.md; if that stops being true, insert
    # after the section's own table instead of at EOF.
    """
    missing = scan_missing()
    if not missing:
        return "Nothing to add, sir — MAP.md is already current."

    map_path = VAULT_DIR / "MAP.md"
    text = map_path.read_text(encoding="utf-8")

    read_when = "TBD — auto-logged" if auto else "TBD"
    rows = "\n".join(
        f"| [{rel}]({rel}) | {_guess_holds(VAULT_DIR / rel)} | {read_when} |"
        for rel in missing
    )

    if _UNFILED_HEADING in text:
        text = text.rstrip("\n") + f"\n{rows}\n"
    else:
        text = (
            text.rstrip("\n")
            + f"\n\n---\n\n{_UNFILED_HEADING}\n\n"
            + f"| File | Holds | Read when |\n|---|---|---|\n{rows}\n"
        )

    map_path.write_text(text, encoding="utf-8")
    plural = "y" if len(missing) == 1 else "ies"
    return f"Added {len(missing)} placeholder entr{plural} to MAP.md, sir."


if __name__ == "__main__":
    # Self-check, not Core/tests/ — same VAULT_DIR-swap approach
    # test_vault_map.py uses. Not a regression test, so it stays here
    # per Core/tests/README.md's own split.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / "MAP.md").write_text(
            "# Map\n\n| File | Holds | Read when |\n|---|---|---|\n"
            "| [known.md](known.md) | Known stuff | Always |\n",
            encoding="utf-8",
        )
        (vault / "known.md").write_text("# Known\nstuff\n", encoding="utf-8")
        (vault / "orphan.md").write_text("# Orphan File\nnotes\n", encoding="utf-8")
        (vault / "daily").mkdir()
        (vault / "daily" / "README.md").write_text("# Daily README\n", encoding="utf-8")

        globals()["VAULT_DIR"] = vault
        missing = scan_missing()
        assert missing == ["daily/README.md", "orphan.md"], missing

        preview = preview_missing()
        assert "orphan.md" in preview and "daily/README.md" in preview, preview

        result = append_missing()
        assert "Added 2" in result, result
        after = (vault / "MAP.md").read_text(encoding="utf-8")
        assert "Orphan File" in after and "orphan.md" in after, after
        assert scan_missing() == [], scan_missing()

    print("vault_map self-check: all passed")
