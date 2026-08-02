# Path.rename() on Windows raises FileExistsError rather than
# overwriting (confirmed directly) — so this was never a silent
# data-loss risk, but it WAS an unhandled crash: a destination
# collision surfaced as a raw WinError string instead of a clear
# spoken answer.

from tools import machine_tools


def test_move_onto_existing_file_gives_a_clear_answer(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("source")
    dest = tmp_path / "b.txt"
    dest.write_text("already here")

    result = machine_tools.move_file(str(src), str(dest))

    assert "already exists" in result
    assert "WinError" not in result
    assert dest.read_text() == "already here"  # untouched
    assert src.exists()  # never moved


def test_rename_onto_existing_file_gives_a_clear_answer(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("source")
    (tmp_path / "b.txt").write_text("already here")

    result = machine_tools.rename_file(str(src), "b.txt")

    assert "already exists" in result
    assert "WinError" not in result
    assert src.exists()


def test_move_to_a_free_destination_still_works(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("source")
    dest = tmp_path / "sub" / "b.txt"

    result = machine_tools.move_file(str(src), str(dest))

    assert "Moved" in result
    assert dest.exists()
    assert not src.exists()
