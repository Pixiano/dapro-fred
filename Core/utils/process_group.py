# Core/utils/process_group.py
#
# Confirmed live 2026-08-19: force-killing a stuck/duplicate FRED process
# (Stop-Process -Force / TerminateProcess) does NOT take its children with
# it on Windows. screen_watcher.py's multiprocessing.Process workers
# (daemon=True) only get cleaned up on a CLEAN interpreter exit — daemon
# status means nothing to an external hard kill, since there's no Python
# code left running to act on it. Two such orphans were found still
# running, one on stale pre-throttle settings, holding 15GB VRAM and 86%
# GPU utilization with nothing left alive to ever stop them.
#
# A Windows Job Object fixes this at the OS level rather than per-child:
# assign this process to a job with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
# and Windows kills every process still in the job — this one and every
# child it ever spawned (subprocess.Popen, multiprocessing.Process, both
# ultimately CreateProcess under the hood) — the instant the job's last
# handle closes. That closure happens automatically when this process
# dies, by ANY means, including an external hard kill with no chance to
# run cleanup code. Covers screen_watcher's workers, hud/server.py, and
# phone_api.py in one place instead of three separate fixes.

import win32api
import win32con
import win32job

_job = None


def contain_children():
    """
    Call once, as early as possible in the main process's startup —
    before anything spawns a child, so every descendant (present and
    future) ends up in the job. A no-op on any failure: this is a
    safety net for the orphan-on-hard-kill case, not something that
    should ever be allowed to stop FRED from starting.
    """
    global _job
    try:
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
        win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
        _job = job  # kept alive for the process lifetime — see module docstring
    except Exception as e:
        print(f"[process_group] couldn't set up job object, orphan-on-kill risk remains: {e}")
