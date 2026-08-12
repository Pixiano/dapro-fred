# Core/tests/test_print_file.py
#
# New tool (2026-08-12): print_file uses os.startfile's "print" verb
# (no win32print dependency). os.startfile is faked throughout — it
# has no real effect under pytest anyway, but faking it lets the test
# assert the "print" verb was actually passed, not just "open".

from tools import system_tools


def test_prints_via_the_print_verb(tmp_path, monkeypatch):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"fake pdf")

    calls = []
    monkeypatch.setattr(system_tools.os, "startfile", lambda path, verb=None: calls.append((path, verb)))

    result = system_tools.print_file(str(target))

    assert calls == [(str(target), "print")]
    assert "Sent report.pdf to the printer" in result


def test_missing_file_is_reported(tmp_path):
    result = system_tools.print_file(str(tmp_path / "nope.pdf"))
    assert "Couldn't find" in result


def test_folder_is_rejected(tmp_path):
    result = system_tools.print_file(str(tmp_path))
    assert "folder" in result


def test_startfile_failure_is_reported_not_raised(tmp_path, monkeypatch):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"fake pdf")

    def raise_oserror(path, verb=None):
        raise OSError("no application is associated")

    monkeypatch.setattr(system_tools.os, "startfile", raise_oserror)

    result = system_tools.print_file(str(target))
    assert "Couldn't print" in result
