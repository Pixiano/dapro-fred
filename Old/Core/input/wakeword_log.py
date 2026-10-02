# Core/input/wakeword_log.py
#
# Dedicated log for wake-word activity, separate from the general
# session log (utils/event_log.py) — see that file's own module
# docstring on the same split for orchestrator/tool_call_log.py.
# Scores come in every ~80ms while listening; mixed into the shared
# log that would drown out everything else there almost instantly.
#
# Only scores above _LOG_FLOOR are written — true silence/room tone
# scores ~0.000-0.002 in practice (confirmed live 2026-08-10), so
# logging every single chunk unconditionally would mean thousands of
# "nothing happened" lines per idle hour for no benefit. Anything
# above the floor is a real attempt worth seeing, fired or not — a
# near-miss score just under threshold is exactly the signal needed
# to tell whether a tuning change (AGC, threshold) is helping.

import json
import threading
from datetime import datetime

from config.settings import DATA_DIR

LOG_PATH = DATA_DIR / "wakeword_log.jsonl"
_LOG_FLOOR = 0.03

_lock = threading.Lock()


def _write(record: dict):
    record["ts"] = datetime.now().isoformat()
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                # default=str: a second line of defense (the real fix is
                # casting at the call site — see wakeword.py's `bool()`
                # comment) so a future stray numpy/non-native type here
                # degrades to its string form instead of raising. This
                # runs inside the live audio callback thread on every
                # call — confirmed live 2026-08-10 that an unhandled
                # TypeError here surfaces as a visible error dialog, not
                # a quiet log line, so it must never be the thing that
                # crashes.
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except (OSError, TypeError) as e:
        print(f"[wakeword_log] write failed: {e}")


def log_score(score: float, gain: float, threshold: float, fired: bool):
    if score < _LOG_FLOOR and not fired:
        return
    _write({
        "type": "score",
        "score": round(float(score), 3),
        "gain": round(float(gain), 2),
        "threshold": threshold,
        "fired": fired,
    })


def log_event(kind: str, **fields):
    """kind: 'resumed' | 'paused' | 'resume_failed' | 'predict_error' | 'on_wake_error' | ..."""
    _write({"type": kind, **fields})
