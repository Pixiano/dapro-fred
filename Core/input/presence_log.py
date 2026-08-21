# Core/input/presence_log.py
#
# Dedicated log for presence poll history, same split rationale as
# wakeword_log.py (see that file's own module docstring) — a per-poll
# append that would drown out the general session log. One line per
# poll_once() call, written from presence.poll_once() itself so there is
# exactly one call site, whichever caller (proactive_checks.check_presence
# or a direct call) triggered the poll.
#
# No rotation, matching wakeword_log.jsonl's own precedent — one poll
# every PRESENCE_POLL_SECONDS (15s) is ~5760 lines/day, small enough that
# an ever-growing file is not a real problem at this scale.

import json
import threading
from datetime import datetime

from config.settings import DATA_DIR

LOG_PATH = DATA_DIR / "presence_log.jsonl"

_lock = threading.Lock()


def log_poll(present: bool):
    record = {"ts": datetime.now().isoformat(), "present": bool(present)}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[presence_log] write failed: {e}")
