# Core/tools/presence_tools.py
#
# Aggregates Core/input/presence_log.py's per-poll history into a
# "typical active hours" answer. Reads the log fresh on every call
# (small file, infrequent ask) rather than caching — same reasoning as
# other low-frequency tool-facing reads in this codebase.

from collections import defaultdict
from datetime import datetime, timedelta

import json as _json

from input.presence_log import LOG_PATH


def active_hours_summary(days: int = 7) -> dict:
    """Per-hour-of-day presence ratio (0.0-1.0) over the last `days` days.

    Shape: {"days": days, "hours": {0: 0.0, ..., 23: 0.0}, "total_polls": N}
    Hours with zero polls in the window report 0.0, not missing/None —
    simplest useful shape for a histogram-style caller.
    """
    cutoff = datetime.now() - timedelta(days=days)
    present_counts = defaultdict(int)
    total_counts = defaultdict(int)
    total_polls = 0

    try:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    for line in lines:
        try:
            rec = _json.loads(line)
            ts = datetime.fromisoformat(rec["ts"])
        except (ValueError, KeyError):
            continue
        if ts < cutoff:
            continue
        hour = ts.hour
        total_counts[hour] += 1
        total_polls += 1
        if rec.get("present"):
            present_counts[hour] += 1

    hours = {
        h: round(present_counts[h] / total_counts[h], 3) if total_counts[h] else 0.0
        for h in range(24)
    }
    return {"days": days, "hours": hours, "total_polls": total_polls}


def describe_active_hours(days: int = 7) -> str:
    """Tool-facing entry point: turns active_hours_summary() into a short
    spoken-friendly sentence naming the hours most often present."""
    summary = active_hours_summary(days)
    if summary["total_polls"] == 0:
        return f"I don't have enough presence data from the last {days} days yet, sir."

    active = [h for h, ratio in summary["hours"].items() if ratio >= 0.5]
    if not active:
        return f"No hour in the last {days} days had you present at least half the time, sir."

    active.sort()
    spans = []
    start = prev = active[0]
    for h in active[1:]:
        if h == prev + 1:
            prev = h
            continue
        spans.append((start, prev))
        start = prev = h
    spans.append((start, prev))

    def fmt(h):
        return datetime(2000, 1, 1, h).strftime("%I %p").lstrip("0")

    span_strs = [fmt(a) if a == b else f"{fmt(a)}-{fmt(b)}" for a, b in spans]
    return f"Based on the last {days} days, you're typically active around {', '.join(span_strs)}, sir."
