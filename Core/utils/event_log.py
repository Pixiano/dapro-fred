# Core/utils/event_log.py
#
# One append-only JSONL file per FRED run, capturing what happened in
# that session: what you said, what FRED said, every tool call, every
# error. Distinct from orchestrator/tool_call_log.py, which accumulates
# forever in one file for a future router to learn from — this is
# scoped per session so a single run can be read back on its own,
# and it's the only channel FRED uses that doesn't depend on a console
# existing. GUI mode runs under pythonw with no console window, so
# print() output is not something you can go back and read after the
# fact; this always writes to disk regardless.
#
# Fail-open like every other logging path in this codebase (see
# tool_call_log.py's docstring on the same point): a write failure here
# must never break a turn, so any exception is swallowed after one
# print to whatever stdout happens to be.

import json
import threading
import time
from datetime import datetime

from config.settings import LOG_DIR

SESSION_DIR = LOG_DIR / "sessions"

_lock = threading.Lock()
_path = None

# A new file every launch, never cleaned up, was already at 45 files in
# a handful of days by 2026-08-02 — this is meant to "run all day" (see
# config/settings.py's idle-unload comments), so left alone it accumulates
# without bound for as long as the machine is used. 30 days is generous
# for "read back what happened recently" while keeping the total finite.
_RETENTION_DAYS = 30


def _prune_old_sessions():
    """
    Delete session logs older than _RETENTION_DAYS. Best-effort and
    silent: a cleanup failure must never block starting a new session,
    the same fail-open rule every write in this module already follows.
    """
    if not SESSION_DIR.exists():
        return

    cutoff = time.time() - (_RETENTION_DAYS * 86400)

    for old_file in SESSION_DIR.glob("session_*.jsonl"):
        try:
            if old_file.stat().st_mtime < cutoff:
                old_file.unlink()
        except OSError:
            continue


def start_session():
    """
    Call once per process, as early as possible (fred_popup.py's main()
    does this right after the crash-dump handler is installed). Creates
    a new timestamped file for this run and returns its path.

    Not required before the first log() call — log() lazily starts a
    session itself, since a missed explicit start (e.g. the CLI path
    someday) shouldn't mean silently losing events instead of just
    starting the file a little later.
    """
    global _path
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _prune_old_sessions()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    _path = SESSION_DIR / f"session_{stamp}.jsonl"
    log("system", note="session start")
    return _path


def log(kind: str, **fields):
    """
    Append one event. `kind` is a free-form label (user_speech,
    fred_speech, tool_call, tool_event, error, system, ...) rather than
    an enum — a new event type should never need a code change here to
    be recorded, only a call site.
    """
    global _path
    if _path is None:
        start_session()

    record = {"ts": datetime.now().isoformat(), "type": kind, **fields}
    try:
        with _lock:
            with open(_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[event_log] write failed: {e}")


def log_error(source: str, error) -> None:
    """Shorthand for the overwhelmingly common case: something in
    `source` raised `error`. Kept separate from log("error", ...) at
    each call site so the field names (source/message) are consistent
    everywhere instead of drifting per caller."""
    log("error", source=source, message=str(error))
