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
# CLASSIFICATION METHOD, second revision 2026-08-23 — read this before
# touching _wearing_headphones again, the history matters:
#
#   1. Vision-LLM, full-resolution 3-image compare (on-reference,
#      off-reference, live frame) via llm/vision_server.py. Worked
#      accuracy-wise once CONTEXT_WINDOW_BY_TIER["Vision"] (was 4096,
#      too small for even a 2-image request) was raised to 16384 — but
#      each call took 10-20s at full ~2560px resolution (~3600 prompt
#      tokens per image), too slow for a check that should run on
#      presence's own 15s poll.
#   2. Classical CV: presence.py's face detector locates the head, a
#      region above/around the bbox is cropped and colour-histogram-
#      compared (cv2.compareHist) against reference photos. Fast, but
#      confirmed live 2026-08-23 to misfire in practice (a real false
#      "headphones on" switch) — self-classification against all 12
#      reference photos only scored 9/12, and critically the
#      correlation GAP between the two candidate scores did NOT track
#      with correctness (a wrong answer could have a LARGER gap than a
#      right one), so no debounce/margin tweak on top of this signal
#      could have fixed it. The underlying feature (whole-head-region
#      colour histogram) is just too diluted by hair/skin/background/
#      clothing variance to isolate "is there a white plastic object on
#      this head" reliably.
#   3. Vision-LLM again, but with the reference AND live images
#      downscaled to 512px on the long edge before encoding (was full
#      resolution). Confirmed live 2026-08-23: prompt cost drops from
#      ~10800 tokens/call to ~1450, latency from 10-20s to ~0.4-0.5s,
#      and self-classification accuracy against all 12 reference photos
#      is 10/12 — better than approach 2, at a fraction of approach 1's
#      cost. (256px was tried first and was too degraded — the model's
#      answers became biased toward one class, the discriminating
#      detail was gone; 512px is the point that keeps enough of the
#      headphones themselves visible.) This is the FALLBACK now — see 4.
#   4. TRAINED CLASSIFIER, preferred when present. A real object
#      detector (YOLOv8n/s pretrained on Open Images V7, which HAS a
#      "Headphones" class) was tried and tested against all 12
#      reference photos, at multiple crop strategies — 0% recall, not a
#      threshold problem, a domain mismatch between these specific
#      photos and the pretrained class's training distribution. Bigger
#      object detectors were judged not worth chasing (nano and small
#      both totally blind to this object, likely the same gap). Instead:
#      scripts/train_headphones_classifier.py fits a small scikit-learn
#      SVM on headphone_features.py's HSV-histogram feature (the SAME
#      feature approach 2 used, but now actually TRAINED on 30-50
#      labeled photos per class via cross-validation, instead of
#      correlation against 1-2 hand-picked references) — Vatsal's own
#      call 2026-08-23. Structure built before the photos exist; this
#      module falls back to approach 3 until HEADPHONES_CLASSIFIER_PATH
#      actually exists on disk.
#
# Gated on presence.is_present() (Vatsal's own call 2026-08-23): skips
# entirely — no camera open, no work at all — the instant presence's
# own last-known state says nobody's there. Deliberately NOT reworked
# when the cadence below decoupled from presence's own poll (Vatsal's
# own call 2026-08-25, "stay fully skipped while absent") — absence is
# still a hard stop, not just a slower poll.
#
# POLL CADENCE, 2026-08-25 — no longer strictly rides presence.py's
# cadence. See HEADPHONE_POLL_SECONDS_ON_HEADPHONES/_ON_SPEAKERS in
# settings.py: fast (3s) while currently on headphones, same as
# presence's own cadence while on speakers. The scheduler job (see
# proactive_checks.register()) is registered at the fast interval;
# check_and_switch() self-throttles internally on the slower state —
# see _last_check_ts below.
#
# RESOURCE FIX 2026-08-24: this used to also open its own camera
# connection and run its own insightface detection pass every cycle —
# fully redundant with presence.py doing the exact same thing on the
# exact same cadence a moment earlier. Now reuses presence.py's own
# last-poll frame + confirmed-match face (presence.last_poll_frame_and_
# face()) instead, cutting both the camera opens and the face-detection
# passes in half.
#
# MEDIA-PLAYING PRIORITY SIGNAL, 2026-08-25 — Vatsal's own ask: if
# audio/media is playing anywhere on the machine (audio.media_state),
# that alone means the target is headphones, no camera check needed —
# "who uses speakers in family." The camera/classifier path only runs
# when nothing's playing. See media_state.py's own docstring for how
# "is media playing" is detected (pycaw session peak-metering,
# excluding FRED's own process so its own TTS doesn't count).

import base64
import json as _json
import random
import re
import urllib.error
import urllib.request

import time

import cv2

from config.settings import (
    HEADPHONE_CHECK_STREAK,
    HEADPHONE_OUTPUT_DEVICE_FALLBACK_NAME,
    HEADPHONE_OUTPUT_DEVICE_NAME,
    HEADPHONE_POLL_SECONDS_ON_HEADPHONES,
    HEADPHONE_POLL_SECONDS_ON_SPEAKERS,
    HEADPHONES_CLASSIFIER_PATH,
    HEADPHONES_OFF_PATHS,
    HEADPHONES_ON_PATHS,
    SPEAKER_OUTPUT_DEVICE_NAME,
)
from audio import device_info, media_state
from input import presence
from orchestrator import intent
from orchestrator.headphone_features import extract_feature_from_face
from utils import event_log

_classifier = None  # lazy-loaded, cached — None also means "tried and not present"
_classifier_checked = False

# Long edge in pixels for both the reference photos and the live frame
# before they're sent to the vision model — see this module's own
# docstring (revision 3) for the accuracy/latency numbers behind this
# specific value. Not "smaller is always better": 256 was tried and
# lost too much discriminating detail.
_ENCODE_SIZE = 512
_JPEG_QUALITY = 80

# None = never checked yet / unknown. True = headphones last confirmed
# on, False = confirmed off. In-memory only, same "a restart is a real
# event" reasoning sleep_mode.py's own streak state holds to — on
# restart this just re-detects and switches once on the first
# confirmed read rather than assuming yesterday's state.
_last_state = None
_streak = 0
_pending_state = None  # the state the current streak is confirming

# Real work last actually happened at this monotonic timestamp — the
# scheduler job itself is registered at the fast (on-headphones) cadence
# (see proactive_checks.register()), so on the slower on-speakers state
# this function self-throttles rather than needing a second scheduler
# job or dynamic per-job rescheduling. See HEADPHONE_POLL_SECONDS_ON_
# HEADPHONES/_ON_SPEAKERS in settings.py for the actual intervals.
_last_check_ts = 0.0

# How many times in a row check_and_switch will SPEAK a "can't find that
# device" heads-up before going quiet — Vatsal's own call 2026-08-24: a
# device that's genuinely gone (unplugged/renamed) fails every single
# poll, and repeating the same complaint forever is just nagging. Still
# logged via event_log every time regardless (see below) — only the
# spoken notification stops. Resets to 0 on any successful switch.
_SWITCH_FAILED_ANNOUNCE_MAX = 3
_switch_failed_count = 0

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

# Heads-up when NONE of the target device(s) are in
# device_info.list_output_devices() (renamed/unplugged/OS didn't
# enumerate it) — added 2026-08-24, Vatsal's own ask: a silent failure
# here just leaves him talking into the wrong output with no idea why,
# same "say something" reasoning as the successful-switch phrases
# above. Shortened and generalized 2026-08-25, Vatsal's own ask: now
# that headphones tries HEADPHONE_OUTPUT_DEVICE_NAME then
# HEADPHONE_OUTPUT_DEVICE_FALLBACK_NAME before failing, a phrase naming
# one specific device/reason no longer fits every failure case.
_SWITCH_FAILED_TO_HEADPHONES_PHRASES = (
    "Can't switch to headphones, sir.",
    "No headphone output available, sir.",
    "Headphones aren't reachable right now, sir.",
)
_SWITCH_FAILED_TO_SPEAKERS_PHRASES = (
    "Can't switch to speakers, sir.",
    "No speaker output available, sir.",
    "Speakers aren't reachable right now, sir.",
)

# Chance that a real successful switch is ALSO followed by a spoken
# "was that right?" — added 2026-08-24, Vatsal's own ask: an indefinite,
# ongoing way to grow the training pool from real live reads (a "yes"
# saves the frame that got classified) rather than only dedicated
# capture sessions. A single fixed value from the requested "10-25% of
# the time" range, not a variable roll — no reason for this probability
# to itself be random. Deliberately no cap/expiry, unlike
# _SWITCH_FAILED_ANNOUNCE_MAX above — this keeps firing forever.
_CONFIRM_PROMPT_CHANCE = 0.15
_CONFIRM_PROMPT_PHRASES = (
    "Did I get that right, sir?",
    "Was that switch correct, sir?",
    "Quick check — was that right, sir?",
    "Did that match what you're actually wearing, sir?",
)

# No is_negative counterpart exists in orchestrator/intent.py (its
# _handle_pending_confirmation treats "not affirmative" as "no" for its
# own destructive-tool flow) — this needs a real three-way split
# (yes / no / unclear-passthrough, see handle_confirmation_reply), so a
# small local negative match, same shape/style as intent._AFFIRMATIVE.
_NEGATIVE_RE = re.compile(
    r"^(?:no|nope|nah|negative|incorrect|wrong|not\s+really|that's\s+wrong)\b[\s,.!]*$",
    re.IGNORECASE,
)

# Set only when check_and_switch's confirmation-prompt roll actually
# fires: {"frame": ndarray, "label": bool} for the switch being
# confirmed. In-memory only, same "restart drops it" convention as
# _last_state/_streak above — an unanswered prompt across a restart is
# just quietly forgotten, not persisted.
_pending_confirmation = None


def _encode_small(image) -> str | None:
    """`image` is either a frame (numpy array, from the live camera) or
    a path (a reference photo on disk). Downscaled to _ENCODE_SIZE on
    the long edge before JPEG-encoding — see this module's own
    docstring for why."""
    frame = cv2.imread(str(image)) if not hasattr(image, "shape") else image
    if frame is None:
        return None
    h, w = frame.shape[:2]
    scale = _ENCODE_SIZE / max(h, w)
    if scale < 1:
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


def _get_classifier():
    """Loaded once, cached — None (and cached as such) if
    HEADPHONES_CLASSIFIER_PATH doesn't exist yet or fails to load, so
    every poll doesn't re-stat the filesystem. Restart the process to
    pick up a freshly (re)trained model, same convention as every other
    lazy-load-once cache in this codebase (presence.py's own
    enrollment-embeddings cache included)."""
    global _classifier, _classifier_checked
    if _classifier_checked:
        return _classifier
    _classifier_checked = True
    if not HEADPHONES_CLASSIFIER_PATH.exists():
        return None
    try:
        import joblib
        _classifier = joblib.load(HEADPHONES_CLASSIFIER_PATH)
    except Exception as e:
        event_log.log_error("headphone_watch", e)
        _classifier = None
    return _classifier


# A confidence gate on predict_proba (added, then reverted, 2026-08-24)
# was tried after a false positive survived the 3-poll streak debounce.
# Measured live: even CORRECT training-photo reads only average
# ~0.75 confidence (min 0.67, max 0.90-0.99) — this SVM's probabilities
# are too compressed at this sample size to threshold on, so a 0.75 cut
# called >half of genuinely correct reads "ambiguous" and sent nearly
# every poll to the vision-LLM fallback (10-20s call, GPU pegged).
# Back to a plain hard decision; the streak debounce is the only
# guard against a single bad read, same as before that detour.
def _wearing_headphones_classifier(frame, face) -> bool | None:
    """True/False from the trained scikit-learn model
    (scripts/train_headphones_classifier.py), None if it isn't trained
    yet or `face` is None (presence.py had nothing confirmed to reuse
    this cycle — see check_and_switch) — caller falls back to the
    vision-LLM path in either case.

    Takes an already-detected `face` (presence.py's own cached match,
    see presence.last_poll_frame_and_face) rather than running its own
    analyzer.get(frame) — added 2026-08-24, Vatsal's own report that
    presence polling and this check were each independently opening the
    camera and running a full face-detection pass on the same ~15s
    cadence, doubling that cost for nothing."""
    model = _get_classifier()
    if model is None:
        return None
    feature = extract_feature_from_face(frame, face)
    if feature is None:
        return None
    prediction = model.predict([feature])[0]
    return bool(prediction)


def _wearing_headphones_llm(frame) -> bool | None:
    """True/False on a clear signal, None if the reference photos
    aren't ready, encoding failed, or the model/network genuinely
    couldn't produce a clear signal (caller keeps the last known state
    rather than guessing)."""
    from llm import vision_server

    on_path, off_path = HEADPHONES_ON_PATHS[0], HEADPHONES_OFF_PATHS[0]
    if not (on_path.exists() and off_path.exists()):
        return None

    if not vision_server.ensure_running():
        event_log.log("headphone_watch", note="vision server unavailable")
        return None

    on_uri, off_uri = _encode_small(on_path), _encode_small(off_path)
    cur_uri = _encode_small(frame)
    if on_uri is None or off_uri is None or cur_uri is None:
        return None

    prompt = (
        "Image1 shows headphones worn. Image2 shows no headphones. Does "
        "Image3 match Image1 or Image2? Answer with ONLY the single word "
        "WEARING or NOT, no explanation."
    )
    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": on_uri}},
                {"type": "image_url", "image_url": {"url": off_uri}},
                {"type": "image_url", "image_url": {"url": cur_uri}},
            ],
        }],
        "max_tokens": 60,
        "temperature": 0.1,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{vision_server._BASE_URL}/v1/chat/completions",
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            data = _json.loads(resp.read())
        reply = (data["choices"][0]["message"]["content"] or "").strip().lower()
    except (urllib.error.URLError, KeyError, IndexError, ValueError) as e:
        event_log.log_error("headphone_watch", e)
        return None

    has_wearing = "wearing" in reply
    has_not = reply.startswith("not") or " not" in f" {reply}"
    if has_wearing and not has_not:
        return True
    if has_not and not has_wearing:
        return False
    return None


def _wearing_headphones(frame, face) -> bool | None:
    """Trained classifier if it exists and has an opinion, else the
    vision-LLM comparison — see this module's own docstring for the
    full method history. Restart the process after running
    scripts/train_headphones_classifier.py to pick up a newly (re)
    trained model — _get_classifier caches a "not present" result too,
    same as every other lazy-load-once cache in this codebase."""
    result = _wearing_headphones_classifier(frame, face)
    if result is not None:
        return result
    return _wearing_headphones_llm(frame)


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
    global _last_state, _streak, _pending_state, _switch_failed_count, _pending_confirmation
    global _last_check_ts

    if not (HEADPHONES_ON_PATHS[0].exists() and HEADPHONES_OFF_PATHS[0].exists()):
        return  # not enrolled yet — see scripts/enroll_headphones.py

    if not presence.is_present():
        return  # presence.py's own poll already says nobody's there — no camera capture needed

    # Self-throttle: the scheduler job fires every HEADPHONE_POLL_SECONDS_
    # ON_HEADPHONES (the fast cadence), but real work only happens that
    # often while _last_state is actually True (on headphones). On
    # speakers (or before any state is known), it backs off to the
    # slower HEADPHONE_POLL_SECONDS_ON_SPEAKERS instead — see this
    # module's own docstring / settings.py's own comment on both
    # constants for why.
    min_interval = HEADPHONE_POLL_SECONDS_ON_HEADPHONES if _last_state else HEADPHONE_POLL_SECONDS_ON_SPEAKERS
    now = time.monotonic()
    if now - _last_check_ts < min_interval:
        return
    _last_check_ts = now

    try:
        if media_state.is_media_playing():
            # Media playing beats the camera check entirely, and doesn't
            # need a frame at all — checked first so a not-yet-cached
            # presence frame (e.g. the first poll cycle after a restart)
            # can't block this signal. See this module's own docstring,
            # "MEDIA-PLAYING PRIORITY SIGNAL".
            result = True
        else:
            # Reuse presence.py's own frame + confirmed-match face from
            # its last poll (same ~15s cadence) rather than opening the
            # camera and running face detection again here — see
            # _wearing_headphones_classifier's docstring for why. face is
            # None whenever presence's own present result didn't come
            # from a confirmed match this cycle; the classifier treats
            # that the same as "no opinion".
            frame, face = presence.last_poll_frame_and_face()
            if frame is None:
                return

            result = _wearing_headphones(frame, face)
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

        # Headphones tries the primary device, then the fallback pair —
        # Vatsal's own ask 2026-08-25, see HEADPHONE_OUTPUT_DEVICE_
        # FALLBACK_NAME's own comment in settings.py. Speakers has no
        # fallback: SPEAKER_OUTPUT_DEVICE_NAME is the machine's built-in
        # output, not a Bluetooth pairing that can just not be there.
        candidates = (
            (HEADPHONE_OUTPUT_DEVICE_NAME, HEADPHONE_OUTPUT_DEVICE_FALLBACK_NAME) if result
            else (SPEAKER_OUTPUT_DEVICE_NAME,)
        )
        devices = device_info.list_output_devices()
        device_name = device_index = None
        for name in candidates:
            match = next((d for d in devices if d["name"] == name), None)
            if match is not None:
                device_name, device_index = name, match["index"]
                break

        if device_name is None:
            event_log.log_error(
                "headphone_watch", OSError(f"none of {candidates!r} present")
            )
            # Structured entry, same shape as the successful "headphone_switch"
            # event below — so a failed switch is queryable/reviewable the same
            # way, not just buried in the generic error log.
            event_log.log("headphone_switch_failed", wearing=result, device=candidates[0])
            _switch_failed_count += 1
            if notify is not None and _switch_failed_count <= _SWITCH_FAILED_ANNOUNCE_MAX:
                phrases = (
                    _SWITCH_FAILED_TO_HEADPHONES_PHRASES if result
                    else _SWITCH_FAILED_TO_SPEAKERS_PHRASES
                )
                notify(random.choice(phrases), title="Audio")
            return
        device_info.set_output_device(device_index)
        event_log.log("headphone_switch", wearing=result, device=device_name)
        _last_state = result
        _switch_failed_count = 0

        if notify is not None:
            phrases = _TO_HEADPHONES_PHRASES if result else _TO_SPEAKERS_PHRASES
            notify(random.choice(phrases), title="Audio")

            if random.random() < _CONFIRM_PROMPT_CHANCE:
                _pending_confirmation = {"frame": frame, "label": result}
                notify(random.choice(_CONFIRM_PROMPT_PHRASES), title="Audio")
    except Exception as e:
        event_log.log_error("headphone_watch", e)


def handle_confirmation_reply(user_input: str) -> str | None:
    """Answers the confirmation prompt check_and_switch sometimes asks
    right after a switch (see _CONFIRM_PROMPT_CHANCE) — checked on
    EVERY turn by orchestrator.py, so this must be cheap when there's
    nothing pending: None immediately, no work done.

    Clear yes (intent.is_affirmative): saves the frame from that switch
    into data/headphones_training/{on,off}/ (via
    scripts.enroll_headphones._next_training_path, same numbering the
    dedicated capture sessions use) for a future retrain. Clear no
    (_NEGATIVE_RE): discards it — never save a photo under a label the
    user just said was wrong. Anything else: also clears the pending
    state (doesn't stay open indefinitely waiting for exact phrasing)
    and returns None so the turn falls through to normal processing
    instead of an unrelated reply getting swallowed here."""
    global _pending_confirmation
    if _pending_confirmation is None:
        return None

    pending = _pending_confirmation
    _pending_confirmation = None

    if intent.is_affirmative(user_input):
        from scripts.enroll_headphones import _next_training_path

        state = "on" if pending["label"] else "off"
        path = _next_training_path(state)
        cv2.imwrite(str(path), pending["frame"])
        return "Saved, thanks."
    if _NEGATIVE_RE.match((user_input or "").strip()):
        return "Noted, won't use that one."
    return None
