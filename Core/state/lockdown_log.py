# Core/state/lockdown_log.py
#
# Append-only event log for lockdown mode — engage/disengage and
# anything blocked while locked. Same shape as input/wakeword_log.py:
# one jsonl line per event, a write failure prints and moves on rather
# than raising, because a logging problem must never break the caller.

import json
from datetime import datetime

from config.settings import DATA_DIR

LOG_PATH = DATA_DIR / "lockdown_log.jsonl"


def log_event(kind: str, detail: str = "") -> None:
    record = {
        "ts": datetime.now().isoformat(),
        "kind": kind,
        "detail": detail,
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        print(f"[lockdown_log] write failed: {e}")


if __name__ == "__main__":
    # Self-check, not in Core/tests/ — that folder's README restricts it
    # to regression tests pinning a bug that actually happened; this is
    # new logic, not a pinned bug.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        LOG_PATH = Path(tmp) / "nested" / "does" / "not" / "exist" / "lockdown_log.jsonl"
        log_event("engaged")
        log_event("blocked", detail="get_weather")
        lines = LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2, lines
        rec0, rec1 = json.loads(lines[0]), json.loads(lines[1])
        assert rec0["kind"] == "engaged" and rec0["detail"] == "", rec0
        assert rec1["kind"] == "blocked" and rec1["detail"] == "get_weather", rec1
        assert "ts" in rec0

        # Never raises even if the parent can't be created (path through
        # an existing file instead of a directory).
        blocked_path = Path(tmp) / "blocker_file"
        blocked_path.write_text("x", encoding="utf-8")
        LOG_PATH = blocked_path / "lockdown_log.jsonl"
        log_event("engaged")  # would raise NotADirectoryError if unguarded

    print("lockdown_log self-check: all passed")
