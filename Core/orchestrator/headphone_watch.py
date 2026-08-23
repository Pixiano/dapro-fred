# Core/orchestrator/headphone_watch.py
#
# Detects via camera whether Vatsal is wearing his headphones and
# switches the default Windows playback device to match — Vatsal's own
# idea, 2026-08-23.
#
# Classical CV, not a vision-LLM call: presence.py's face detector
# (insightface, already loaded in-process for identity matching) locates
# the head, a region above/around the bbox — where headphone ear-cups
# sit — is cropped and colour-histogram-compared against the same crop
# from two reference photos (scripts/enroll_headphones.py, which takes
# two shots per state — one with glasses, one without — this only reads
# the first of each pair). Vatsal's own call: the vision-LLM round trip
# first tried here (presence.py's own ambiguous-match fallback pattern)
# turned out to be pointless for a binary head-region classification —
# confirmed live 2026-08-23 it was also silently failing outright,
# CONTEXT_WINDOW_BY_TIER["Vision"] (4096) being too small for even a
# 2-image request (~7200 tokens needed), a real separate bug fixed in
# settings.py but irrelevant to this feature now that it doesn't touch
# that pipeline at all. This path is faster, needs no GPU/LLM
# contention with the conversation model, and is exactly what the
# already-loaded face detector is suited for.
#
# Deliberately its own module rather than folded into presence.py or
# proactive_checks.py — reuses presence.py's face analyzer and
# camera-index resolver, but its own concern (audio routing) is
# unrelated to either module's job.

import cv2

from config.settings import (
    HEADPHONE_CHECK_STREAK,
    HEADPHONE_OUTPUT_DEVICE_NAME,
    HEADPHONES_OFF_PATHS,
    HEADPHONES_ON_PATHS,
    SPEAKER_OUTPUT_DEVICE_NAME,
)
from input.presence import _get_analyzer, resolve_camera_index
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

_ref_histograms = None  # (on_hist, off_hist), computed once and cached


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


def _head_region(frame):
    """Crop around the largest detected face, expanded upward and
    outward — where over-ear headphones and their band actually sit,
    which a plain face bbox doesn't cover. None if no face detected.
    Resized to a fixed size so the histogram comparison isn't sensitive
    to how close to the camera the face happens to be this frame."""
    faces = _get_analyzer().get(frame)
    if not faces:
        return None

    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    x1, y1, x2, y2 = face.bbox.astype(int)
    w, h = x2 - x1, y2 - y1

    top = max(0, y1 - int(h * 0.6))
    left = max(0, x1 - int(w * 0.3))
    right = min(frame.shape[1], x2 + int(w * 0.3))
    bottom = min(frame.shape[0], y2)

    crop = frame[top:bottom, left:right]
    if crop.size == 0:
        return None
    return cv2.resize(crop, (128, 128))


def _histogram(crop):
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def _reference_histograms():
    """Cached after first successful computation — same lazy-load-once
    convention as presence.py's own _get_enrollment_embeddings. Rerun
    enroll_headphones.py and restart the process to pick up new
    reference photos, same as re-enrollment for faces."""
    global _ref_histograms
    if _ref_histograms is not None:
        return _ref_histograms

    on_path, off_path = HEADPHONES_ON_PATHS[0], HEADPHONES_OFF_PATHS[0]
    if not (on_path.exists() and off_path.exists()):
        return None

    on_crop = _head_region(cv2.imread(str(on_path)))
    off_crop = _head_region(cv2.imread(str(off_path)))
    if on_crop is None or off_crop is None:
        return None

    _ref_histograms = (_histogram(on_crop), _histogram(off_crop))
    return _ref_histograms


def _wearing_headphones(frame) -> bool | None:
    """True/False on a clear signal (one reference correlates more
    strongly than the other), None if no face was detected, the
    reference photos aren't ready, or the two scores are exactly tied
    (caller keeps the last known state rather than guessing)."""
    refs = _reference_histograms()
    if refs is None:
        return None
    on_hist, off_hist = refs

    crop = _head_region(frame)
    if crop is None:
        return None
    hist = _histogram(crop)

    on_score = cv2.compareHist(hist, on_hist, cv2.HISTCMP_CORREL)
    off_score = cv2.compareHist(hist, off_hist, cv2.HISTCMP_CORREL)

    if on_score > off_score:
        return True
    if off_score > on_score:
        return False
    return None


def check_and_switch():
    """One poll: capture, classify, debounce, switch on a real change.
    Never raises — a failure here must not affect anything else FRED is
    doing (same convention proactive_checks.py's own checks hold to)."""
    global _last_state, _streak, _pending_state

    if not (HEADPHONES_ON_PATHS[0].exists() and HEADPHONES_OFF_PATHS[0].exists()):
        return  # not enrolled yet — see scripts/enroll_headphones.py

    try:
        frame = _capture_frame()
        if frame is None:
            return

        result = _wearing_headphones(frame)
        if result is None:
            return  # no face / ambiguous — keep the last known state

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
