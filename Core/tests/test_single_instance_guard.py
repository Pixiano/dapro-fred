"""single_instance — PID lock liveness logic: fresh start, live-PID
refusal, stale-PID takeover, and release() only removing its own PID."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.single_instance as si

_tmp_dir = tempfile.TemporaryDirectory()
si.LOCK_PATH = Path(_tmp_dir.name) / "fred.lock"


# --- no lock file yet: acquires cleanly, writes our own PID ---
assert not si.LOCK_PATH.exists()
si.acquire_or_exit()
assert si.LOCK_PATH.read_text().strip() == str(os.getpid())


# --- stale lock (PID no longer alive): treated as free, takes over ---
si.LOCK_PATH.write_text("999999999")
_real_pid_exists = si.psutil.pid_exists
si.psutil.pid_exists = lambda pid: False
try:
    si.acquire_or_exit()
    assert si.LOCK_PATH.read_text().strip() == str(os.getpid())
finally:
    si.psutil.pid_exists = _real_pid_exists


# --- live lock naming another PID: refuses, does not overwrite ---
si.LOCK_PATH.write_text("424242")
si.psutil.pid_exists = lambda pid: pid == 424242
try:
    try:
        si.acquire_or_exit()
        raise AssertionError("expected SystemExit for a live competing PID")
    except SystemExit as e:
        assert "424242" in str(e)
    assert si.LOCK_PATH.read_text().strip() == "424242"
finally:
    si.psutil.pid_exists = _real_pid_exists


# --- release() only removes a lock that names our own PID ---
si.LOCK_PATH.write_text(str(os.getpid()))
si.release()
assert not si.LOCK_PATH.exists()

si.LOCK_PATH.write_text("13")
si.release()
assert si.LOCK_PATH.read_text().strip() == "13"  # untouched, not ours

_tmp_dir.cleanup()
print("test_single_instance_guard: ok")
