# Core/input/presence.py
#
# MVP scope only, per fred-presence-sleep-mode-plan_2026-08-18.md and
# Vatsal's own scoping call 2026-08-21: presence detection alone
# (is_present()/last_seen()/last_checked()/poll_once()), nothing
# downstream yet — no sleep-mode, no reminder-gating, no cancel phrases,
# no background poller/scheduler. poll_once() is a plain function some
# external caller invokes every PRESENCE_POLL_SECONDS; that wiring is
# later, separate work.
#
# Snapshot, not continuous feed — same shape as tools/vision_tools.py's
# look_through_camera(): open the camera, grab one frame, release
# immediately. Never keep the capture open between polls.
#
# Privacy: face embeddings are personal biometric data, same treatment
# this codebase gives personal/ vault content (see SENSITIVE_LOCAL_ONLY /
# SENSITIVE_TOOLS in orchestrator/orchestrator.py). Everything here stays
# on-device: insightface runs locally, and the ambiguous-match fallback
# below calls this repo's own local llama-server.exe vision pipeline
# (llm/vision_server.py), never a cloud model. No raw frames are ever
# persisted by this module — a polled frame is matched and discarded.
# (enroll_face.py keeps exactly one reference photo on disk, but that's
# a deliberate, narrow, explicitly-commented exception in that script,
# not something this module does.)

import base64
import json
from datetime import datetime
from pathlib import Path

import cv2

from config.settings import (
    DATA_DIR,
    PRESENCE_CAMERA_INDEX,
    PRESENCE_MATCH_THRESHOLD_HIGH,
    PRESENCE_MATCH_THRESHOLD_LOW,
)
from utils import event_log

STATE_PATH = DATA_DIR / "presence_state.json"
ENROLLMENT_PATH = DATA_DIR / "face_enrollment.json"
REFERENCE_PHOTO_PATH = DATA_DIR / "face_reference.jpg"

_analyzer = None  # lazy, load-once-keep-warm — same pattern as llm_client._get_model
_enrollment_embeddings = None  # lazy-loaded list[np.ndarray], cached after first read

_state_cache = None  # in-memory mirror of STATE_PATH, simplest correct approach per phone_tools' CALL_SEEN_PATH pattern


def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        from insightface.app import FaceAnalysis
        _analyzer = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _analyzer.prepare(ctx_id=0)
    return _analyzer


def _get_enrollment_embeddings():
    """List of stored embeddings (5 separate shots, not averaged — per
    the enrollment design decision, kept for robustness across lighting/
    angle). Cached after first successful load; re-read never happens
    automatically, matching every other lazy-load-once pattern here —
    rerun enroll_face.py and restart the process to pick up a re-
    enrollment."""
    global _enrollment_embeddings
    if _enrollment_embeddings is None:
        import numpy as np
        data = json.loads(ENROLLMENT_PATH.read_text(encoding="utf-8"))
        _enrollment_embeddings = [np.array(e) for e in data["embeddings"]]
    return _enrollment_embeddings


def _load_state() -> dict:
    global _state_cache
    if _state_cache is not None:
        return _state_cache
    try:
        _state_cache = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _state_cache = {"present": False, "last_seen": None, "last_checked": None}
    return _state_cache


def _save_state(state: dict):
    global _state_cache
    _state_cache = state
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE_PATH)


def is_present() -> bool:
    return _load_state().get("present", False)


def last_seen():
    ts = _load_state().get("last_seen")
    return datetime.fromisoformat(ts) if ts else None


def last_checked():
    ts = _load_state().get("last_checked")
    return datetime.fromisoformat(ts) if ts else None


def _cosine_similarity(a, b) -> float:
    import numpy as np
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _best_similarity(face_embedding, enrollment_embeddings) -> float:
    return max(_cosine_similarity(face_embedding, e) for e in enrollment_embeddings)


def _vision_fallback_is_match(current_frame) -> bool | None:
    """Ambiguous-band fallback: ask FRED's own local vision model
    (llm/vision_server.py) whether the current frame and the stored
    enrollment reference photo show the same person. Returns True/False
    on a clear signal, or None if the model/network genuinely couldn't
    produce one (caller falls back to last-known state, not a guess).

    Not a describe_image() call — that only takes one image. This POSTs
    directly to vision_server's own endpoint with two image_url content
    parts in one message, the same request shape confirmed working
    tonight (2026-08-21) elsewhere: chat_template_kwargs.enable_thinking
    must be explicitly False or the model burns its whole token budget
    on a <think> block and returns empty (see vision_server.describe_image's
    own docstring on the same failure mode).
    """
    import json as _json
    import urllib.error
    import urllib.request

    from llm import vision_server

    if not REFERENCE_PHOTO_PATH.exists():
        event_log.log("presence_vision_fallback", note="no reference photo on disk")
        return None

    if not vision_server.ensure_running():
        event_log.log("presence_vision_fallback", note="vision server unavailable")
        return None

    ok, ref_bytes = cv2.imencode(".jpg", cv2.imread(str(REFERENCE_PHOTO_PATH)))
    ok2, cur_bytes = cv2.imencode(".jpg", current_frame)
    if not ok or not ok2:
        event_log.log("presence_vision_fallback", note="jpg encode failed")
        return None

    ref_uri = "data:image/jpeg;base64," + base64.b64encode(ref_bytes).decode("ascii")
    cur_uri = "data:image/jpeg;base64," + base64.b64encode(cur_bytes).decode("ascii")

    prompt = (
        "The first image is a reference photo of one specific person. "
        "The second image is a just-captured photo. Is the same person "
        "clearly visible in the second image? Answer with just YES or NO."
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
        event_log.log_error("presence_vision_fallback", e)
        return None

    has_yes = "yes" in reply
    has_no = "no" in reply
    if has_yes and not has_no:
        return True
    if has_no and not has_yes:
        return False
    event_log.log("presence_vision_fallback", note="ambiguous reply", reply=reply[:200])
    return None


def _frame_matches_enrollment(frame) -> bool:
    """Runtime multi-face handling, DELIBERATELY different from
    enroll_face.py's single-largest-face heuristic: present (True) if
    ANY detected face matches, regardless of how many other people are
    also in frame. Family visiting must never cause a false absent —
    that asymmetry with enrollment (which picks just the largest face)
    is intentional, not an inconsistency."""
    faces = _get_analyzer().get(frame)
    if not faces:
        return False

    enrollment_embeddings = _get_enrollment_embeddings()
    last_state = is_present()

    for face in faces:
        similarity = _best_similarity(face.normed_embedding, enrollment_embeddings)
        if similarity >= PRESENCE_MATCH_THRESHOLD_HIGH:
            return True
        if similarity < PRESENCE_MATCH_THRESHOLD_LOW:
            continue  # confident non-match for this face, check the next one

        # Ambiguous band: fall back to the vision model. A clear match on
        # any single face is enough to report present.
        verdict = _vision_fallback_is_match(frame)
        if verdict is True:
            return True
        if verdict is False:
            continue
        # Vision fallback also couldn't produce a clear signal: fail
        # safe toward the last known state rather than flip unpredictably.
        event_log.log("presence_ambiguous_fallback_failed", similarity=round(similarity, 3),
                       fallback_to_last_state=last_state)
        if last_state:
            return True

    return False


def poll_once() -> bool:
    """Grab one frame, match, update state, return current presence.

    Camera-in-use failure mode (e.g. the iBall claimed by a video call):
    fails soft, returns the last-known persisted state, never raises."""
    now = datetime.now()
    state = _load_state()

    cap = cv2.VideoCapture(PRESENCE_CAMERA_INDEX)
    try:
        if not cap.isOpened():
            event_log.log("presence_poll_failed", note="camera did not open")
            state["last_checked"] = now.isoformat()
            _save_state(state)
            return state.get("present", False)

        ok, frame = cap.read()
    finally:
        cap.release()

    if not ok:
        event_log.log("presence_poll_failed", note="camera read failed")
        state["last_checked"] = now.isoformat()
        _save_state(state)
        return state.get("present", False)

    present = _frame_matches_enrollment(frame)

    state["present"] = present
    state["last_checked"] = now.isoformat()
    if present:
        state["last_seen"] = now.isoformat()
    _save_state(state)
    return present
