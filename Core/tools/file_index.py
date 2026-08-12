# Core/tools/file_index.py
#
# A maintained SQLite index of the filesystem, standing in for
# machine_tools.search_files' live os.walk on every call. reindex_drive
# does the (slow) walk once and writes it to disk; search_index answers
# from that snapshot with a plain LIKE query — instant, but only as
# fresh as the last reindex. Deliberately never auto-refreshes (not on
# FRED startup, not on a schedule) — that would just be the live walk
# again with extra steps; it only rebuilds when explicitly asked
# ("reindex my drive").
#
# Reuses machine_tools._walk_pruned for the walk itself rather than
# reimplementing the heavy-directory skip list (AppData, node_modules,
# .git, venvs, ...) a second time — same rationale documented there.

import sqlite3
from pathlib import Path

from config.settings import DATA_DIR
from tools.machine_tools import _walk_pruned

DB_PATH = DATA_DIR / "file_index.db"


def _connect(db_path: Path = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS files "
        "(path TEXT PRIMARY KEY, name TEXT, mtime REAL, size INTEGER)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name)")
    return conn


def reindex_drive(directory: str = "", db_path: Path = None) -> str:
    """
    Walk `directory` (default: the user's home folder, same default
    search_files uses — not the raw C:\\ root, which is mostly Windows/
    Program Files noise nobody means by "my files") and rebuild the
    index from scratch.
    """

    base = Path(directory).expanduser() if directory else Path.home()
    if not base.exists():
        return f"No such folder: {base}"

    rows = []
    for path in _walk_pruned(base):
        try:
            stat = path.stat()
        except OSError:
            continue  # gone, or unreadable, between listing and stat
        rows.append((str(path), path.name, stat.st_mtime, stat.st_size))

    conn = _connect(db_path)
    conn.execute("DELETE FROM files")
    conn.executemany(
        "INSERT OR REPLACE INTO files (path, name, mtime, size) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    return f"Indexed {len(rows)} file(s) under {base}."


def search_index(query: str, limit: int = 10, db_path: Path = None) -> str:
    """
    Search the maintained index by filename substring. Fast — no
    filesystem walk — but reflects whatever the index looked like at
    the last reindex_drive() call, not the live disk.
    """

    query = str(query or "").strip()
    if not query:
        return "Give me something to search for."

    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        return "The file index hasn't been built yet — say 'reindex my drive' first."

    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT path, name FROM files WHERE name LIKE ? ORDER BY name LIMIT ?",
        (f"%{query}%", int(limit)),
    ).fetchall()
    conn.close()

    if not rows:
        return f"No indexed files matching '{query}'."

    # Same speech-safe shape as search_files: names only, folder for
    # context, no full paths read aloud.
    names = ", ".join(f"{name} (in {Path(p).parent.name})" for p, name in rows)
    return f"Found {len(rows)} indexed file(s) matching '{query}': {names}"
