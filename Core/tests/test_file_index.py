# Core/tests/test_file_index.py
#
# New tool (2026-08-12): a maintained SQLite index in place of
# search_files' live walk. Pins the two things that matter — search
# before any reindex says so instead of returning nothing silently,
# and reindex_drive prunes the same heavy directories search_files
# does (reused from machine_tools._walk_pruned, not reimplemented) —
# and both run against tmp_path, never the real disk or the real
# Core/data/file_index.db.

from tools import file_index


def _db(tmp_path):
    return tmp_path / "index.db"


def test_search_before_any_reindex_says_so(tmp_path):
    result = file_index.search_index("report", db_path=_db(tmp_path))
    assert "hasn't been built yet" in result


def test_reindex_then_search_finds_the_file(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "quarterly_report.txt").write_text("x")
    (tree / "photo.png").write_text("x")

    db = _db(tmp_path)
    summary = file_index.reindex_drive(str(tree), db_path=db)
    assert "Indexed 2 file(s)" in summary

    result = file_index.search_index("report", db_path=db)
    assert "quarterly_report.txt" in result
    assert "photo.png" not in result


def test_reindex_prunes_heavy_directories(tmp_path):
    """Same skip list search_files uses (machine_tools._SKIP_DIRS) —
    an indexed node_modules would make the index as slow to build and
    as noisy to search as the live walk it's replacing."""
    tree = tmp_path / "tree"
    (tree / "node_modules").mkdir(parents=True)
    (tree / "node_modules" / "left_pad.js").write_text("x")
    (tree / "real_file.txt").write_text("x")

    db = _db(tmp_path)
    file_index.reindex_drive(str(tree), db_path=db)

    result = file_index.search_index("left_pad", db_path=db)
    assert "No indexed files matching" in result


def test_reindex_replaces_the_previous_snapshot(tmp_path):
    """A maintained index, not an ever-growing one — reindexing an
    emptied folder must make the old entries unfindable."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "old.txt").write_text("x")

    db = _db(tmp_path)
    file_index.reindex_drive(str(tree), db_path=db)
    assert "old.txt" in file_index.search_index("old", db_path=db)

    (tree / "old.txt").unlink()
    file_index.reindex_drive(str(tree), db_path=db)
    assert "No indexed files matching" in file_index.search_index("old", db_path=db)


def test_search_with_no_query_asks_for_one(tmp_path):
    db = _db(tmp_path)
    file_index.reindex_drive(str(tmp_path), db_path=db)
    assert "Give me something to search for" in file_index.search_index("", db_path=db)
