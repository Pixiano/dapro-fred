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
    PRESENCE_DYNAMIC_EMBEDDINGS_CAP,
    PRESENCE_MATCH_THRESHOLD_HIGH,
    PRESENCE_PRESENT_DEBOUNCE,
    PRESENCE_YOLO_FAILSAFE_MAX_POLLS,
    PRESENCE_YOLO_PERSON_CONFIDENCE,
)
from input import presence_log
from utils import event_log

STATE_PATH = DATA_DIR / "presence_state.json"
ENROLLMENT_PATH = DATA_DIR / "face_enrollment.json"
REFERENCE_PHOTO_PATH = DATA_DIR / "face_reference.jpg"

# Family/friend enrollment, strictly additive to Vatsal's own presence
# tracking above — never read/written by is_present()'s own matching.
# Deliberately flat, NOT Vatsal's own base/hard/dynamic tiering: this
# only powers a binary known/unknown gate for
# orchestrator/security_watch.py, not a debounced presence signal, so
# that complexity isn't earning its keep here. Format:
#   {"people": {"Mom": {"greet": true, "embeddings": [{"embedding": [...], "ts": "..."}]}}}
# No settings UI for MVP — hand-edit "greet" to turn a person's
# wake-greeting off. Populated via scripts/enroll_face.py --person NAME.
FAMILY_ENROLLMENT_PATH = DATA_DIR / "family_enrollment.json"

_analyzer = None  # lazy, load-once-keep-warm — same pattern as llm_client._get_model
_yolo_model = None  # lazy, load-once-keep-warm — same pattern, see _get_yolo_model
_enrollment_embeddings = None  # lazy-loaded list[np.ndarray], cached after first read
_family_embeddings = None  # lazy-loaded {name: [np.ndarray, ...]}, cached after first read

_state_cache = None  # in-memory mirror of STATE_PATH, simplest correct approach per phone_tools' CALL_SEEN_PATH pattern

_camera_index_cache = None  # cv2 index, resolved once per process — see resolve_camera_index()

# Last poll's frame + confirmed-match face object, cached so a sibling
# checker on the same cadence (orchestrator/headphone_watch.py) can
# reuse them instead of opening its own camera connection and running
# its own insightface detection pass — added 2026-08-24, Vatsal's own
# report of the GPU/CPU cost of the two running redundantly every 15s.
# _last_matched_face is None whenever this poll's present result came
# from anything other than a confirmed direct/vision-fallback match
# (no face, or the "fail safe to last known state" ambiguous path) —
# callers must treat that the same as "nothing to reuse this cycle".
_last_frame = None
_last_matched_face = None

# Last per-poll family classification, updated by _frame_matches_enrollment
# every poll_once() — see last_classification() below.
_last_classification = {"known_people": [], "unrecognized": False}

# Consecutive present/match polls, in-memory only — mirrors
# orchestrator/sleep_mode.py's own absent/present streak counters (same
# "a restart is a real event" reasoning as that module's docstring).
# Gates _accumulate_embedding: a match on a single frame that hasn't
# cleared this debounce yet must not get written into the enrollment
# set, same false-positive window sleep_mode.py's own present-debounce
# closes for the greeting/sleep-exit path. See PRESENCE_PRESENT_DEBOUNCE's
# comment in settings.py.
_present_streak = 0

# Consecutive polls where presence was sustained ONLY via the no-face
# YOLO-person fail-safe below (never a real face match in between),
# in-memory only, same "restart is a real event" convention. A YOLO
# "person" detection doesn't confirm identity, so trusting it forever
# would mask Vatsal actually leaving (if someone else is now in frame)
# or suppress security_watch.py's stranger loop indefinitely — capped at
# PRESENCE_YOLO_FAILSAFE_MAX_POLLS, Vatsal's own call 2026-08-28 that
# the original unbounded version was too permissive. Reset to 0 by any
# real face match; incremented each poll the fail-safe alone is why
# presence stayed True.
_yolo_failsafe_streak = 0


def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        from insightface.app import FaceAnalysis
        _analyzer = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _analyzer.prepare(ctx_id=0)
    return _analyzer


def _get_yolo_model():
    """Lazy-loaded yolov8n singleton, mirrors _get_analyzer() above.
    Nano weights — fast, COCO-pretrained, "person" is one of its
    best-covered classes (unlike the earlier failed headphone-detection
    attempt with a niche class). `ultralytics` is already an installed
    dependency from that attempt."""
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO
        _yolo_model = YOLO("yolov8n.pt")
    return _yolo_model


def _frame_has_person(frame) -> tuple:
    """(has_person, confidence) — best "person" detection confidence in
    frame, or (False, 0.0) if none clears PRESENCE_YOLO_PERSON_CONFIDENCE.
    COCO class 0 is "person" in yolov8n's default weights."""
    results = _get_yolo_model()(frame, classes=[0], verbose=False)
    best = 0.0
    for r in results:
        for conf in r.boxes.conf.tolist():
            best = max(best, conf)
    return best >= PRESENCE_YOLO_PERSON_CONFIDENCE, best


def _get_enrollment_embeddings() -> dict:
    """{"base": [...], "hard": [...], "dynamic": [...]} of np.ndarray
    embeddings (5+ separate shots per tier, not averaged — per the
    enrollment design decision, kept for robustness across lighting/
    angle). Cached after first successful load; re-read never happens
    automatically, matching every other lazy-load-once pattern here —
    rerun enroll_face.py and restart the process to pick up a re-
    enrollment.

    Loads via scripts.enroll_face's own loader so old flat-format
    face_enrollment.json (pre-2026-08-22) is migrated to the tagged
    {"embedding", "kind", "ts"} format the same way, whichever module
    touches the file first."""
    global _enrollment_embeddings
    if _enrollment_embeddings is None:
        import numpy as np
        from scripts.enroll_face import _load_existing_embeddings

        by_tier = {"base": [], "hard": [], "dynamic": []}
        for entry in _load_existing_embeddings():
            by_tier.setdefault(entry["kind"], []).append(np.array(entry["embedding"]))
        _enrollment_embeddings = by_tier
    return _enrollment_embeddings


def _get_family_embeddings() -> dict:
    """{"Mom": [np.ndarray, ...], ...} — cached after first successful
    load, same lazy-load-once convention as _get_enrollment_embeddings
    above (rerun enroll_face.py --person NAME and restart to pick up a
    re-enrollment or a new person). Missing/corrupt file -> empty dict,
    same as face_enrollment.json's own missing-file handling."""
    global _family_embeddings
    if _family_embeddings is None:
        import numpy as np
        try:
            data = json.loads(FAMILY_ENROLLMENT_PATH.read_text(encoding="utf-8"))
            people = data.get("people", {})
        except (OSError, ValueError):
            people = {}
        _family_embeddings = {
            name: [np.array(e["embedding"]) for e in info.get("embeddings", [])]
            for name, info in people.items()
        }
    return _family_embeddings


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


def last_classification() -> dict:
    """{"known_people": [name, ...], "unrecognized": bool} from the most
    recent poll_once() — see _classify_family below. Never affects
    is_present()'s own Vatsal-only result; this is a sibling read for
    orchestrator/security_watch.py's stranger-detection loop."""
    return _last_classification


def _cosine_similarity(a, b) -> float:
    import numpy as np
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _best_similarity(face_embedding, embeddings_by_tier: dict) -> tuple[float, str | None]:
    """Best cosine similarity across ALL tiers (a match against any tier
    counts, same as the old flat-pool behaviour) plus which tier produced
    it — MVP keeps a single threshold for all tiers (no per-tier
    threshold yet, per Vatsal's 2026-08-21 precision-risk call), the tier
    is tracked only so a match is diagnosable later if false-accepts
    increase."""
    best_sim, best_tier = -1.0, None
    for tier, embeddings in embeddings_by_tier.items():
        for e in embeddings:
            sim = _cosine_similarity(face_embedding, e)
            if sim > best_sim:
                best_sim, best_tier = sim, tier
    return best_sim, best_tier


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


def _accumulate_embedding(face):
    """Ongoing accuracy improvement: append this face's embedding to
    face_enrollment.json's "dynamic" tier ONLY — base/hard are protected,
    populated exclusively by the deliberate enroll_face.py flows, never
    by this automatic path. ONLY ever called on a CONFIRMED positive
    match (see call sites below) — never on a non-match or an unresolved
    ambiguous result.

    FIFO eviction once PRESENCE_DYNAMIC_EMBEDDINGS_CAP is reached: the
    oldest dynamic entry (by file order, which is insertion order) is
    dropped before the new one is appended — Vatsal's 2026-08-21 tier
    redesign, replacing the old flat "stop once full" cap.

    Reuses enroll_face.py's own load/append helpers rather than
    reinventing the same JSON read-modify-write (scripts/ is already
    importable from here — see test_tool_call_report.py's identical
    `from scripts import ...` pattern). Cheap to read+parse on every poll
    (every PRESENCE_POLL_SECONDS) at this scale, no caching needed."""
    from scripts.enroll_face import EMBEDDINGS_PATH, _append_embeddings, _load_existing_embeddings

    all_entries = _load_existing_embeddings()
    dynamic_entries = [e for e in all_entries if e["kind"] == "dynamic"]
    new_entry = {
        "embedding": face.normed_embedding.tolist(),
        "kind": "dynamic",
        "ts": datetime.now().isoformat(),
    }

    if len(dynamic_entries) >= PRESENCE_DYNAMIC_EMBEDDINGS_CAP:
        # Evict the oldest dynamic entry (first one in file order) —
        # base/hard entries are never touched by this.
        oldest = dynamic_entries[0]
        kept = [e for e in all_entries if e is not oldest]
        kept.append(new_entry)
        EMBEDDINGS_PATH.write_text(json.dumps({"embeddings": kept}), encoding="utf-8")
    else:
        _append_embeddings([new_entry])


def _classify_family(faces) -> dict:
    """Sibling classification pass over the SAME already-detected faces
    from _frame_matches_enrollment's own analyzer.get(frame) call below
    — no second face-detection run. Binary known/unknown per face
    against family_enrollment.json (see FAMILY_ENROLLMENT_PATH's format
    comment above); completely separate from Vatsal's own base/hard/
    dynamic matching and never affects it.

    A face that matches VATSAL himself (checked against his own
    enrollment, same PRESENCE_MATCH_THRESHOLD_HIGH bar) is excluded
    entirely — he isn't in family_enrollment.json, so without this a
    frame containing only Vatsal would misreport as "unrecognized".

    unrecognized=True means at least one remaining face is neither
    Vatsal nor a known family member — the signal
    orchestrator/security_watch.py's stranger-detection loop consumes."""
    vatsal_tiers = _get_enrollment_embeddings()
    family = _get_family_embeddings()
    known = []
    unrecognized = False

    for face in faces:
        vatsal_sim, _ = _best_similarity(face.normed_embedding, vatsal_tiers)
        if vatsal_sim >= PRESENCE_MATCH_THRESHOLD_HIGH:
            continue  # this is Vatsal, not a family/stranger classification target

        best_name, best_sim = None, -1.0
        for name, embeddings in family.items():
            for e in embeddings:
                sim = _cosine_similarity(face.normed_embedding, e)
                if sim > best_sim:
                    best_sim, best_name = sim, name

        if best_name is not None and best_sim >= PRESENCE_MATCH_THRESHOLD_HIGH:
            if best_name not in known:
                known.append(best_name)
        else:
            unrecognized = True

    return {"known_people": known, "unrecognized": unrecognized}


def _frame_matches_enrollment(frame):
    """Runtime multi-face handling, DELIBERATELY different from
    enroll_face.py's single-largest-face heuristic: present (True) if
    ANY detected face matches, regardless of how many other people are
    also in frame. Family visiting must never cause a false absent —
    that asymmetry with enrollment (which picks just the largest face)
    is intentional, not an inconsistency.

    Returns (present, matched_face, matched_tier): matched_face is the
    face object behind a CONFIRMED positive match (high-confidence direct
    match, or the ambiguous-band vision fallback resolving to a match) —
    the caller accumulates its embedding, but only once the present-
    streak debounce has cleared (see poll_once). matched_tier is which
    tier ("base"/"hard"/"dynamic") produced that match's best similarity
    score, logged for diagnosability — no per-tier threshold yet, a
    match against ANY tier still counts the same (Vatsal's 2026-08-21
    precision-risk call, MVP keeps one threshold). matched_face/
    matched_tier are None when present is True via the ambiguous-
    fallback-failed/last-known-state path, since that's not a confirmed
    match and must never be accumulated regardless of debounce, or when
    present is False."""
    faces = _get_analyzer().get(frame)

    global _last_classification, _yolo_failsafe_streak
    _last_classification = _classify_family(faces)

    last_state = is_present()

    if not faces:
        # No face at all — before declaring absence, check for a
        # person-shaped blob (head down writing, turned away, partial
        # occlusion). Fails safe toward last known state exactly like the
        # ambiguous-vision-fallback-failed path below, never a confirmed
        # match (no embedding to accumulate). PRESENCE_ABSENT_DEBOUNCE
        # stays the backstop for genuine absence either way.
        has_person, confidence = _frame_has_person(frame)
        if has_person and last_state:
            if _yolo_failsafe_streak < PRESENCE_YOLO_FAILSAFE_MAX_POLLS:
                _yolo_failsafe_streak += 1
                event_log.log("presence_no_face_person_failsafe",
                               confidence=round(confidence, 3), fallback_to_last_state=last_state,
                               streak=_yolo_failsafe_streak)
                return True, None, None
            # Cap exceeded — a YOLO "person" doesn't confirm identity, so
            # trusting it indefinitely could mask Vatsal actually leaving
            # (someone else now in frame) or suppress security_watch.py's
            # stranger loop forever. Stop holding present; normal absence
            # handling (PRESENCE_ABSENT_DEBOUNCE) resumes from here.
            event_log.log("presence_no_face_person_failsafe_capped",
                           confidence=round(confidence, 3), streak=_yolo_failsafe_streak)
        return False, None, None

    embeddings_by_tier = _get_enrollment_embeddings()

    for face in faces:
        similarity, tier = _best_similarity(face.normed_embedding, embeddings_by_tier)
        if similarity >= PRESENCE_MATCH_THRESHOLD_HIGH:
            # Real confirmed match -- resets the YOLO fail-safe streak,
            # same reasoning as its own comment above.
            _yolo_failsafe_streak = 0
            # pose = [pitch, yaw, roll] degrees, already computed by
            # buffalo_l's 1k3d68 landmark model on every detected face —
            # logged only, no decision built on it yet (plan_perception_
            # features_2026-08-25.md's wave-1 scope: confirm real values
            # behave sensibly before using them for anything).
            pose = face.get("pose")
            event_log.log("presence_match", similarity=round(similarity, 3), tier=tier,
                           pose=pose.tolist() if pose is not None else None)
            return True, face, tier

        # Below HIGH always goes to the vision model now — Vatsal's own
        # 2026-08-23 report: turning away or looking down even a little
        # was enough to drop ArcFace similarity below the old LOW
        # threshold (0.30), which skipped the vision fallback entirely
        # and reported absent straight away. PRESENCE_MATCH_THRESHOLD_LOW
        # is no longer read here for that reason (still used elsewhere,
        # e.g. as a general "this isn't even close" reference point in
        # docs/settings). Cost: a stranger's face in frame (family
        # visiting) now also pays a vision-model round trip instead of
        # being skipped cheaply — accepted, not a reported problem yet.
        verdict = _vision_fallback_is_match(frame)
        if verdict is True:
            _yolo_failsafe_streak = 0  # real confirmed match, same as the HIGH-similarity branch above
            event_log.log("presence_match", similarity=round(similarity, 3), tier=tier,
                           via_vision_fallback=True)
            return True, face, tier
        if verdict is False:
            continue
        # Vision fallback also couldn't produce a clear signal: fail
        # safe toward the last known state rather than flip unpredictably.
        event_log.log("presence_ambiguous_fallback_failed", similarity=round(similarity, 3),
                       fallback_to_last_state=last_state)
        if last_state:
            return True, None, None

    return False, None, None


def _is_live_feed(index: int) -> bool:
    """Two frames, a beat apart: a real camera sensor has natural noise
    even on a static scene, a frozen virtual-cam placeholder (OBS Virtual
    Camera / Canon EOS Webcam Utility both show a static "no signal"
    bitmap when idle) is bit-identical between reads. Confirmed live
    2026-08-23: real iBall frame-diff mean ~45, OBS placeholder exactly
    0.0. Any failure (camera won't open, mocked/fake capture returning a
    non-array frame in tests) means "can't tell" -> not live, never
    raises."""
    import time

    try:
        cap = cv2.VideoCapture(index)
        try:
            if not cap.isOpened():
                return False
            ok1, f1 = cap.read()
            time.sleep(0.4)
            ok2, f2 = cap.read()
        finally:
            cap.release()
        if not (ok1 and ok2):
            return False
        return cv2.absdiff(f1, f2).mean() > 1.0
    except Exception:
        return False


def resolve_camera_index() -> int:
    """Which cv2 index is the real, live desk camera right now — auto
    probed rather than trusting the hardcoded PRESENCE_CAMERA_INDEX,
    because Windows does not guarantee stable camera index ordering
    across reboots when virtual-cam apps (OBS, Canon EOS Webcam Utility)
    are involved. Confirmed live 2026-08-23: a reboot silently swapped
    the real iBall from index 1 to index 0, and PRESENCE_CAMERA_INDEX
    (still 1) spent the better part of an hour reading OBS Virtual
    Camera's idle placeholder instead — presence detection never saw
    Vatsal, and nobody noticed until look_through_camera described the
    placeholder graphic instead of the desk.

    Cached for the process lifetime once resolved — each probe opens the
    camera and waits ~0.4s per candidate index, not something to redo on
    every 15s poll, and the mapping can't change without a reboot/device
    change anyway (restart the process to re-probe, same convention as
    presence.py's other lazy-cached loaders).

    Falls back to the configured PRESENCE_CAMERA_INDEX if nothing looks
    live (no camera connected, or a test/mock environment where the
    frame-diff probe itself can't run) — same index poll_once() always
    used before this existed.
    """
    global _camera_index_cache
    if _camera_index_cache is not None:
        return _camera_index_cache

    for index in range(4):
        if _is_live_feed(index):
            _camera_index_cache = index
            return index

    _camera_index_cache = PRESENCE_CAMERA_INDEX
    return _camera_index_cache


def last_poll_frame_and_face():
    """(frame, matched_face) from the most recent poll_once() — see
    _last_frame/_last_matched_face's own comment above. Either or both
    can be None (no camera frame this cycle, or a present result that
    didn't come from a confirmed match); callers must check."""
    return _last_frame, _last_matched_face


def poll_once() -> bool:
    """Grab one frame, match, update state, return current presence.

    Camera-in-use failure mode (e.g. the iBall claimed by a video call):
    fails soft, returns the last-known persisted state, never raises."""
    global _present_streak, _last_frame, _last_matched_face

    now = datetime.now()
    state = _load_state()

    cap = cv2.VideoCapture(resolve_camera_index())
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

    present, matched_face, matched_tier = _frame_matches_enrollment(frame)
    _last_frame, _last_matched_face = frame, matched_face

    if present:
        _present_streak += 1
        # Same debounce sleep_mode.py applies before exiting sleep mode /
        # firing the wake greeting — a confirmed match on a frame that
        # hasn't cleared it yet must not pollute the enrollment set.
        if matched_face is not None and _present_streak >= PRESENCE_PRESENT_DEBOUNCE:
            _accumulate_embedding(matched_face)
    else:
        _present_streak = 0

    state["present"] = present
    state["last_checked"] = now.isoformat()
    if present:
        state["last_seen"] = now.isoformat()
    _save_state(state)
    presence_log.log_poll(present, matched_tier=matched_tier)  # camera-failure
    # early-returns above are skipped on purpose — those report stale
    # last-known state, not a real observation, and would pollute
    # active-hours aggregation.
    return present
