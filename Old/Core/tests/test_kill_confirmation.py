# kill_process substring-matches by design ("code" matches Code.exe
# and every helper process with "code" anywhere in its name), and the
# confirmation prompt used to only echo the raw argument back — "about
# to run kill_process (name_or_pid=code)" — giving no way to notice
# how many processes that covers before saying yes.

from unittest.mock import MagicMock, patch

from tools import machine_tools


def _fake_process(name, pid):
    proc = MagicMock()
    proc.info = {"name": name, "pid": pid}
    return proc


def test_matching_processes_previews_without_killing():
    procs = [
        _fake_process("Code.exe", 100),
        _fake_process("Code - Insiders.exe", 101),
        _fake_process("notepad.exe", 200),
    ]
    with patch("tools.machine_tools.psutil.process_iter", return_value=procs):
        matches = machine_tools.matching_processes("code")

    names = {m[0] for m in matches}
    assert names == {"Code.exe", "Code - Insiders.exe"}
    for proc in procs:
        proc.kill.assert_not_called()


def test_matching_processes_by_exact_pid():
    procs = [_fake_process("notepad.exe", 200), _fake_process("chrome.exe", 300)]
    with patch("tools.machine_tools.psutil.process_iter", return_value=procs):
        matches = machine_tools.matching_processes("300")

    assert matches == [("chrome.exe", 300)]


def test_kill_process_only_kills_what_was_previewed():
    procs = [_fake_process("Code.exe", 100), _fake_process("notepad.exe", 200)]
    with patch("tools.machine_tools.psutil.process_iter", return_value=procs), \
         patch("tools.machine_tools.psutil.Process", return_value=procs[0]):
        result = machine_tools.kill_process("code")

    assert "Code.exe" in result
    procs[0].kill.assert_called_once()
    procs[1].kill.assert_not_called()


def test_no_match_does_not_error():
    with patch("tools.machine_tools.psutil.process_iter", return_value=[]):
        assert machine_tools.matching_processes("nonexistent") == []
        assert "No process found" in machine_tools.kill_process("nonexistent")
