# Core/orchestrator/headphone_watch.py
#
# Detects via camera whether Vatsal is wearing his headphones and
# switches the default Windows playback device to match — Vatsal's own
# idea, 2026-08-23. Same two-image vision-model comparison mechanic
# input/presence.py's own ambiguous-match fallback already uses
# (_vision_fallback_is_match): one reference photo of him wearing the
# headphones (scripts/enroll_headphones.py), compared against the live
# frame, not a face-identity check.
#
# Deliberately its own module rather than folded into presence.py or
# proactive_checks.py — this reads presence.py's camera-index resolver
# and vision_server.py's pipeline, but its own concern (audio routing)
# is unrelated to either module's job.

import base64
import json as _json
import urllib.error
import urllib.request

import cv2

from config.settings import (
    HEADPHONE_CHECK_STREAK,
    HEADPHONE_OUTPUT_DEVICE_NAME,
    HEADPHONES_REFERENCE_PATH,
    SPEAKER_OUTPUT_DEVICE_NAME,
)
from input.presence import resolve_camera_index
from tools.machine_tools import set_audio_output
from utils import event_log

# None = never checked yet / unknown. True = headphones last confirmed
# on, False = confirmed off. In-memory only, same "a restart is a real
# event" reasoning sleep_mode.py's own streak state holds to — on
# restart this just re-detects and switches once on the first
# confirmed read rather than assuming yesterday's state.
_last_state = None
_streak = 0
_pending_state = None  # the state the current streak is confirming


def _capture_frame():
    """Same open-one-frame-release-immediately pattern used everywhere
    else in this codebase (presence.py's poll_once, vision_tools.py's
    look_through_camera). Returns None on any camera failure — this
    check just skips a cycle rather than raising."""
    cap = cv2.VideoCapture(resolve_camera_index())
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
    finally:
        cap.release()
    return frame if ok else None


def _wearing_headphones(frame) -> bool | None:
    """Ask the local vision model whether the person in `frame` is
    wearing the same headphones shown in the reference photo. True/False
    on a clear signal, None if the model/network genuinely couldn't
    produce one (caller keeps the last known state rather than
    guessing) — same fail-soft shape as presence.py's
    _vision_fallback_is_match, deliberately not shared code with it
    since the question being asked is different (headphones-on vs
    same-person)."""
    from llm import vision_server

    if not HEADPHONES_REFERENCE_PATH.exists():
        return None

    if not vision_server.ensure_running():
        event_log.log("headphone_watch", note="vision server unavailable")
        return None

    ok, ref_bytes = cv2.imencode(".jpg", cv2.imread(str(HEADPHONES_REFERENCE_PATH)))
    ok2, cur_bytes = cv2.imencode(".jpg", frame)
    if not ok or not ok2:
        return None

    ref_uri = "data:image/jpeg;base64," + base64.b64encode(ref_bytes).decode("ascii")
    cur_uri = "data:image/jpeg;base64," + base64.b64encode(cur_bytes).decode("ascii")

    prompt = (
        "The first image shows a person wearing over-ear headphones. "
        "In the second image, is the same person visibly wearing "
        "headphones or earbuds right now? Answer with just YES or NO."
    )
    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": ref_uri}},
                {"type": "image_url", "image_url": {"url": cur_uri}},
            ],
        }],
        "max_tokens": 600,
        "temperature": 0.2,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{vision_server._BASE_URL}/v1/chat/completions",
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            data = _json.loads(resp.read())
        reply = (data["choices"][0]["message"]["content"] or "").strip().lower()
    except (urllib.error.URLError, KeyError, IndexError, ValueError) as e:
        event_log.log_error("headphone_watch", e)
        return None

    has_yes, has_no = "yes" in reply, "no" in reply
    if has_yes and not has_no:
        return True
    if has_no and not has_yes:
        return False
    return None


def check_and_switch():
    """One poll: capture, classify, debounce, switch on a real change.
    Never raises — a failure here must not affect anything else FRED is
    doing (same convention proactive_checks.py's own checks hold to)."""
    global _last_state, _streak, _pending_state

    if not HEADPHONES_REFERENCE_PATH.exists():
        return  # not enrolled yet — see scripts/enroll_headphones.py

    try:
        frame = _capture_frame()
        if frame is None:
            return

        result = _wearing_headphones(frame)
        if result is None:
            return  # ambiguous/unavailable — keep the last known state

        if result != _pending_state:
            _pending_state = result
            _streak = 1
        else:
            _streak += 1

        if _streak < HEADPHONE_CHECK_STREAK:
            return
        if result == _last_state:
            return  # confirmed, but nothing actually changed

        device_name = HEADPHONE_OUTPUT_DEVICE_NAME if result else SPEAKER_OUTPUT_DEVICE_NAME
        set_audio_output(device_name)
        event_log.log("headphone_switch", wearing=result, device=device_name)
        _last_state = result
    except Exception as e:
        event_log.log_error("headphone_watch", e)
