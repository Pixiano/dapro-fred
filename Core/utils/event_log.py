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
import re
import threading
from datetime import datetime

from config.settings import LOG_DIR

SESSION_DIR = LOG_DIR / "sessions"

_lock = threading.Lock()
_path = None

# One file per DAY, not per launch — every session on the same date
# appends to the same file instead of starting a new one. Kept
# deliberately unbounded: no retention, no pruning, grows for as long
# as FRED is used. That's an explicit choice, not an oversight — a
# prior version of this module deleted anything older than 30 days,
# and that was reverted because the logs are meant to be a complete
# record, not a rolling window.
_DATE_FMT = "%Y-%m-%d"

# Old naming, one file per launch: session_2026-08-02_11-46-12.jsonl.
# Matched (not the new session_2026-08-02.jsonl form) so the one-time
# merge below can tell "still needs merging" from "already merged"
# without a separate migrated-or-not flag anywhere.
_LEGACY_PATTERN = re.compile(r"^session_(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}\.jsonl$")


def _merge_legacy_sessions():
    """
    One-time migration from one-file-per-launch to one-file-per-date.

    Every legacy file for a given date is concatenated, in chronological
    order (filenames sort lexicographically the same way their
    timestamps do, so a plain sort is enough), into that date's single
    file, then the legacy files are deleted. Idempotent by construction:
    once no file matches _LEGACY_PATTERN, the glob below finds nothing
    and this is a no-op — so it's safe to call on every startup instead
    of needing a separate one-shot flag to say "already migrated".
    """
    if not SESSION_DIR.exists():
        return

    by_date = {}
    for path in sorted(SESSION_DIR.glob("session_*.jsonl")):
        match = _LEGACY_PATTERN.match(path.name)
        if match:
            by_date.setdefault(match.group(1), []).append(path)

    for date, legacy_files in by_date.items():
        merged_path = SESSION_DIR / f"session_{date}.jsonl"
        try:
            with open(merged_path, "a", encoding="utf-8") as out:
                for legacy in legacy_files:
                    content = legacy.read_text(encoding="utf-8")
                    if content and not content.endswith("\n"):
                        content += "\n"  # a missing trailing newline would
                        # otherwise glue the next file's first line onto
                        # this file's last one.
                    out.write(content)
            for legacy in legacy_files:
                legacy.unlink()
        except OSError as e:
            print(f"[event_log] merge failed for {date}: {e}")


def start_session():
    """
    Call once per process, as early as possible (fred_popup.py's main()
    does this right after the crash-dump handler is installed). Points
    logging at today's file — creating it if this is the first session
    of the day, appending to it if it already exists from an earlier
    launch today — and returns its path.

    Not required before the first log() call — log() lazily starts a
    session itself, since a missed explicit start (e.g. the CLI path
    someday) shouldn't mean silently losing events instead of just
    starting the file a little later.
    """
    global _path
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _merge_legacy_sessions()
    stamp = datetime.now().strftime(_DATE_FMT)
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
