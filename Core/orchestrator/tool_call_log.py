# Core/orchestrator/tool_call_log.py
#
# Records what actually happened on tool-eligible turns, so a future
# router (embedding example bank, or whatever comes after it) can learn
# from real usage instead of a hand-written test table.
#
# Deliberately NOT a training pipeline. This only writes rows. Nothing
# reads this file yet — that's the next piece, once there's enough real
# data to make it worth building, per the "not yet" from the design
# discussion this came out of.
#
# Labels are free because FRED already knows the outcome after the fact:
#   - a tool ran and returned a real result, no error       -> positive
#   - a tool returned "Error: ..."                           -> negative
#   - the user interrupted FRED mid-reply                    -> weak negative
# No second model, no manual labelling pass, no cost beyond appending a
# line — this is data collection, not a feature with a UI.

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from config.settings import DATA_DIR

LOG_PATH = DATA_DIR / "tool_call_log.jsonl"

_lock = threading.Lock()


def _write(record: dict):
    record["timestamp"] = datetime.now().isoformat()
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[tool_call_log] write failed: {e}")


def new_turn_id() -> str:
    """One id per user turn, so a tool-call row and a later interruption
    signal (logged from the UI, after speech, on a different thread) can
    be joined back together."""
    return uuid.uuid4().hex[:12]


# Tool error phrasing across Core/tools/ isn't consistent — some raise
# "Error: ...", others "X not found", "Couldn't ...", "Failed to ...". A
# prefix match on "Error"/"Couldn't" alone missed real failures like
# "Path not found: nonexistent.txt", which is not a prefix case. This is
# a substring scan over the actual vocabulary in use, checked against
# grep across tools/*.py rather than guessed.
_ERROR_MARKERS = re.compile(
    r"\b(error|couldn'?t|can'?t\b|failed|not found|doesn'?t exist|"
    r"no such|unable|invalid|malformed)\b",
    re.IGNORECASE,
)


def log_tool_call(
    turn_id: str,
    utterance: str,
    tool_name: str,
    arguments: dict,
    result: str,
    path: str,
    tools_offered: list = None,
    reason: str = "",
):
    """
    One row per tool actually executed.

    `path` is "dispatcher" (deterministic fast path, no LLM tool choice
    involved — logged for completeness, not because the model chose
    anything there) or "tool_loop" (the LLM picked `tool_name` from
    `tools_offered`, which is the case an eventual router would learn
    from).
    """
    text = str(result or "")
    is_error = bool(_ERROR_MARKERS.search(text))

    _write({
        "turn_id": turn_id,
        "utterance": utterance,
        "path": path,
        "tools_offered": tools_offered or [],
        "routing_reason": reason,
        "tool_called": tool_name,
        "arguments": arguments,
        "result_preview": text[:160],
        "result_error": is_error,
    })


def log_turn_feedback(turn_id: str, interrupted: bool = False, note: str = ""):
    """
    Signal that arrives after the fact, from the UI layer — e.g. the user
    cut FRED off mid-reply. Joined to the tool-call row by `turn_id` when
    this file is eventually read, not merged into it now: the outcome
    isn't known until well after the row was written.
    """
    if not (interrupted or note):
        return
    _write({
        "turn_id": turn_id,
        "feedback": True,
        "interrupted": interrupted,
        "note": note,
    })
