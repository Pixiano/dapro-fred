# Core/utils/single_instance.py
#
# One FRED at a time. Nothing stopped a second `fred_popup.py` from
# starting a whole second stack alongside a live one — and Windows'
# SO_REUSEADDR lets the second process's HUD/phone-API servers bind an
# already-bound port without erroring (see pill_app.py's `_start_phone_api`
# comment), so two live FREDs can end up splitting incoming requests
# nondeterministically. A PID lock file refuses the second launch outright,
# before it gets anywhere near that.
#
# psutil.pid_exists() alone only checks the PID slot is occupied, not that
# it's still FRED — Windows can recycle a PID fast enough that a stale lock
# from a process that died could now match a totally unrelated process.
# Confirmed live 2026-08-28: a hard-killed fred_popup.py left the lock
# pointing at a PID that got reused by an unrelated process, silently
# blocking every relaunch (FRED.bat, the Startup-folder shortcut, manual
# runs) with no visible error at all, since pythonw has no console to show
# the "already running" message on. _is_fred_process() below closes that
# gap by checking the PID's own command line actually names this script,
# not just that something is alive at that number.

import os
from pathlib import Path

import psutil

LOCK_PATH = Path(__file__).resolve().parents[1] / "data" / "fred.lock"


def _is_fred_process(pid: int) -> bool:
    """True only if `pid` is genuinely running fred_popup.py."""
    try:
        return "fred_popup.py" in " ".join(psutil.Process(pid).cmdline())
    except psutil.Error:
        return False


def acquire_or_exit():
    """
    Call once at startup, before anything spawns a child. Writes this
    process's PID to the lock file. If the lock names another PID that's
    still alive AND still actually FRED, prints why and raises SystemExit
    without starting anything — the existing instance is left running
    untouched. A lock pointing at a dead or PID-recycled-into-something-
    else process is treated as stale and overwritten.
    """
    if LOCK_PATH.exists():
        try:
            pid = int(LOCK_PATH.read_text().strip())
        except (ValueError, OSError):
            pid = None
        if pid is not None and _is_fred_process(pid):
            raise SystemExit(
                f"[fred_popup] FRED is already running (pid {pid}) — "
                "not starting a second instance."
            )
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()))


def release():
    """
    Best-effort cleanup on clean exit. Not load-bearing: a lock left
    behind by a hard kill is already handled by the pid_exists() check
    above finding the PID gone.
    """
    try:
        if LOCK_PATH.exists() and int(LOCK_PATH.read_text().strip()) == os.getpid():
            LOCK_PATH.unlink()
    except (OSError, ValueError):
        pass
