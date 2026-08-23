# Core/orchestrator/headphone_watch.py
#
# Detects via camera whether Vatsal is wearing his headphones and
# switches FRED's own audio output to match — Vatsal's own idea,
# 2026-08-23.
#
# Switches via audio.device_info.set_output_device (sd.default.device —
# PortAudio, process-local), the SAME mechanism the HUD's speaker
# dropdown already uses. Deliberately NOT the Windows-wide system
# default (pycaw's SetDefaultDevice) — first attempt here used that and
# confirmed live 2026-08-23 it visibly did nothing Vatsal could hear:
# FRED's own TTS output stream reads sd.default.device at creation
# time, independent of the OS system default, so it kept talking
# through whatever sd.default.device already was (pinned by
# apply_saved_devices()/the HUD dropdown) regardless of the Windows-wide
# default changing underneath it. Vatsal's explicit call: only touch
# what the HUD already touches, not system-wide settings.
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
#
# Gated on presence.is_present() (Vatsal's own call 2026-08-23): runs
# on presence.py's own 15s poll cadence and skips entirely — no camera
# open, no face detection — the instant presence's own last-known state
# says nobody's there. Originally this had its own independent 30s
# schedule with its own separate camera capture + face-detection call,
# duplicating presence.py's work on a different cadence and only
# "cancelling" implicitly when its own detection happened to find no
# face. Reading presence's already-computed state instead of re-running
# detection is strictly cheaper and matches what was actually asked for.

import random

import cv2

from config.settings import (
    HEADPHONE_CHECK_STREAK,
    HEADPHONE_OUTPUT_DEVICE_NAME,
    HEADPHONES_OFF_PATHS,
    HEADPHONES_ON_PATHS,
    SPEAKER_OUTPUT_DEVICE_NAME,
)
from audio import device_info
from input import presence
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

# Short, varied heads-up on an actual switch — same "sir-suffixed
# short-phrase-pool" style as proactive_checks.py's own
# _PRESENCE_GREETINGS/_CAMERA_OBSTRUCTION_PHRASES, so this doesn't say
# the exact same line every single time. Vatsal's call 2026-08-23.
_TO_HEADPHONES_PHRASES = (
    "Switched to your headphones, sir.",
    "On headphones now, sir.",
    "Headphones it is, sir.",
    "You're on headphones, sir.",
)
_TO_SPEAKERS_PHRASES = (
    "Switched to speakers, sir.",
    "On speakers now, sir.",
    "Speakers it is, sir.",
    "You're on speakers, sir.",
)


def _capture_frame():
    """Same open-one-frame-release-immediately pattern used everywhere
    else in this codebase (presence.py's poll_once, vision_tools.py's
    look_through_camera). Returns None on any camera failure — this
    check just skips a cycle rather than raising."""
    cap = cv2.VideoCapture(presence.resolve_camera_index())
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
    faces = presence._get_analyzer().get(frame)
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


def _average_histogram(paths):
    """Mean histogram across every path in `paths` that exists on disk
    and has a detectable face — not just the first. More reference
    shots (Vatsal's own call 2026-08-23, 4 per state now instead of 2)
    genuinely improves the comparison this way instead of the extras
    sitting on disk unused as spares."""
    hists = []
    for path in paths:
        if not path.exists():
            continue
        crop = _head_region(cv2.imread(str(path)))
        if crop is not None:
            hists.append(_histogram(crop))
    if not hists:
        return None
    avg = sum(hists) / len(hists)
    cv2.normalize(avg, avg)
    return avg


def _reference_histograms():
    """Cached after first successful computation — same lazy-load-once
    convention as presence.py's own _get_enrollment_embeddings. Rerun
    enroll_headphones.py and restart the process to pick up new
    reference photos, same as re-enrollment for faces."""
    global _ref_histograms
    if _ref_histograms is not None:
        return _ref_histograms

    on_hist = _average_histogram(HEADPHONES_ON_PATHS)
    off_hist = _average_histogram(HEADPHONES_OFF_PATHS)
    if on_hist is None or off_hist is None:
        return None

    _ref_histograms = (on_hist, off_hist)
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


def check_and_switch(notify=None):
    """One poll: capture, classify, debounce, switch on a real change.
    Never raises — a failure here must not affect anything else FRED is
    doing (same convention proactive_checks.py's own checks hold to).

    notify: proactive_checks.py's own gated notify(), passed in rather
    than imported — same reason focus_checkin.check(notify) takes it as
    a parameter instead of importing proactive_checks back: that module
    already imports this one, so a top-level import the other way would
    cycle. Vatsal's own ask 2026-08-23: a manual output-device switch in
    Windows shows a heads-up, so an automatic one should say something
    too rather than silently swapping under him."""
    global _last_state, _streak, _pending_state

    if not (HEADPHONES_ON_PATHS[0].exists() and HEADPHONES_OFF_PATHS[0].exists()):
        return  # not enrolled yet — see scripts/enroll_headphones.py

    if not presence.is_present():
        return  # presence.py's own poll already says nobody's there — no camera capture needed

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
        matches = [d for d in device_info.list_output_devices() if d["name"] == device_name]
        if not matches:
            event_log.log_error(
                "headphone_watch", OSError(f"no output device named {device_name!r} present")
            )
            return
        device_info.set_output_device(matches[0]["index"])
        event_log.log("headphone_switch", wearing=result, device=device_name)
        _last_state = result

        if notify is not None:
            phrases = _TO_HEADPHONES_PHRASES if result else _TO_SPEAKERS_PHRASES
            notify(random.choice(phrases), title="Audio")
    except Exception as e:
        event_log.log_error("headphone_watch", e)
