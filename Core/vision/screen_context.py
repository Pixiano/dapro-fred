# Core/vision/screen_context.py
#
# Read/write for the screen-watcher's cached description. Deliberately
# tiny and dependency-light (json + pathlib only) — this is imported by
# the on-demand "what's on my screen" tool in the MAIN process, which
# must never need to pull in mss/llama_cpp just to read a cached string.
# The heavy imports (screenshot capture, the Vision model) live only in
# screen_watcher.py, the child-process side.

import json
import time

from config.settings import SCREEN_CONTEXT_PATH, SCREEN_CONTEXT_MAX_AGE_SECONDS


def write(description: str):
    """Atomic write-then-rename, same pattern as every other small JSON
    state file in this codebase (found_cache, proactive_state) — a
    reader must never see a half-written file."""
    SCREEN_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCREEN_CONTEXT_PATH.with_suffix(".json.tmp")
    payload = {"description": description, "ts": time.time()}
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(SCREEN_CONTEXT_PATH)


def read():
    """
    Returns (description, age_seconds) or (None, None) if nothing has
    ever been captured. Age is returned rather than a pre-computed
    "is it stale" bool so the caller can phrase staleness in its own
    words rather than getting a bare True/False.
    """
    if not SCREEN_CONTEXT_PATH.exists():
        return None, None
    try:
        data = json.loads(SCREEN_CONTEXT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, None

    age = time.time() - data.get("ts", 0)
    return data.get("description"), age


def is_fresh(age_seconds) -> bool:
    return age_seconds is not None and age_seconds <= SCREEN_CONTEXT_MAX_AGE_SECONDS
