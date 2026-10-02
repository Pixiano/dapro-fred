"""single_instance — PID lock liveness logic: fresh start, live-FRED-PID
refusal, stale-PID takeover, PID-recycled-into-something-else takeover
(the 2026-08-28 bug), and release() only removing its own PID."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.single_instance as si

_tmp_dir = tempfile.TemporaryDirectory()
si.LOCK_PATH = Path(_tmp_dir.name) / "fred.lock"
_real_is_fred_process = si._is_fred_process


# --- no lock file yet: acquires cleanly, writes our own PID ---
assert not si.LOCK_PATH.exists()
si.acquire_or_exit()
assert si.LOCK_PATH.read_text().strip() == str(os.getpid())


# --- stale lock (PID no longer alive at all): treated as free, takes over ---
si.LOCK_PATH.write_text("999999999")
si._is_fred_process = lambda pid: False
try:
    si.acquire_or_exit()
    assert si.LOCK_PATH.read_text().strip() == str(os.getpid())
finally:
    si._is_fred_process = _real_is_fred_process


# --- PID alive but recycled into an unrelated process (the actual
# 2026-08-28 bug: psutil.pid_exists() alone would say "live", but it's
# not FRED) -- treated as free, takes over, does NOT refuse ---
si.LOCK_PATH.write_text("424242")
si._is_fred_process = lambda pid: False  # alive, but not fred_popup.py
try:
    si.acquire_or_exit()
    assert si.LOCK_PATH.read_text().strip() == str(os.getpid())
finally:
    si._is_fred_process = _real_is_fred_process


# --- live lock naming another PID that genuinely IS fred_popup.py:
# refuses, does not overwrite ---
si.LOCK_PATH.write_text("424242")
si._is_fred_process = lambda pid: pid == 424242
try:
    try:
        si.acquire_or_exit()
        raise AssertionError("expected SystemExit for a live competing FRED PID")
    except SystemExit as e:
        assert "424242" in str(e)
    assert si.LOCK_PATH.read_text().strip() == "424242"
finally:
    si._is_fred_process = _real_is_fred_process


# --- release() only removes a lock that names our own PID ---
si.LOCK_PATH.write_text(str(os.getpid()))
si.release()
assert not si.LOCK_PATH.exists()

si.LOCK_PATH.write_text("13")
si.release()
assert si.LOCK_PATH.read_text().strip() == "13"  # untouched, not ours

_tmp_dir.cleanup()
print("test_single_instance_guard: ok")
