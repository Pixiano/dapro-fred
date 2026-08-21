# Core/orchestrator/focus_checkin.py
#
# Focus-awareness check-in, scoped with Vatsal 2026-08-21: if he's been
# present at the desk but hasn't had a real turn with FRED in a while,
# grab one webcam frame and let the LOCAL vision model decide whether
# anything about it (plus recent session context) is worth a short
# spoken remark.
#
# HARD PRIVACY CONSTRAINT: this captures Vatsal's face/room, categorically
# more sensitive than the text-based personal/ vault content. The vision
# call below goes through llm/vision_server.py (local llama-server.exe
# subprocess) UNCONDITIONALLY — never gated behind SENSITIVE_LOCAL_ONLY,
# never configurable, never a cloud model. Do not change this.
#
# Threshold growth lives in PROACTIVE_STATE_PATH under its own
# "focus_checkin" key (the same file proactive_checks.py already uses for
# every other check's dedup state) rather than a separate state file —
# small helper duplicated here instead of importing proactive_checks.py,
# since that module will import THIS one to register it and a two-way
# import would cycle.
#
# Sleep-mode gating: deliberately NOT re-checked here. presence.is_present()
# is required before this ever fires, and sleep_mode only enters on a
# debounced run of ABSENT polls — on_presence_poll(True) exits sleep mode
# immediately. So "present" and "sleeping" can't both be true at once; the
# notify() callback this module is handed (proactive_checks.notify, which
# already gates on sleep_mode.is_sleeping()) is sufficient on its own.

import base64
import json
from datetime import datetime

from config.settings import (
    FOCUS_CHECKIN_BASE_MINUTES,
    FOCUS_CHECKIN_STEP_MINUTES,
    PRESENCE_CAMERA_INDEX,
    PROACTIVE_STATE_PATH,
    VAULT_DIR,
)
from input import presence
from utils import event_log

# Where captured frames live — never the git repo, kept indefinitely, no
# pruning (Vatsal's explicit call: the simple "just keep appending"
# version). 2026-08-22: moved under personal/images/, one subfolder per
# calendar day named "YYYY-MM-DD_Weekday" (date + weekday name), journal-
# style rather than the old flat focus-checkins/ folder. Only this
# module's journal/observation captures live here — face_reference.jpg
# (Core/data/, enroll_face.py/presence.py's functional vision-fallback
# reference photo) is deliberately NOT here, it needs a stable fixed path
# code reads, not a dated journal entry.
FOCUS_PHOTO_BASE_DIR = VAULT_DIR / "personal" / "images"

_NO_OBSERVATION = "NO_OBSERVATION"


def _load_state() -> dict:
    if not PROACTIVE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict):
    PROACTIVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROACTIVE_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(PROACTIVE_STATE_PATH)


def _last_interaction_at():
    """Timestamp of the most recent real turn with FRED — the last
    user_speech or tool_call event in today's session log. There's no
    existing "last active with FRED" tracker in this codebase (checked:
    proactive_state.json's idle tracking is OS-level keyboard/mouse idle
    via GetLastInputInfo, a different signal — someone can be present and
    idle-at-OS-level-zero while never actually talking to FRED). The
    session log IS the record of real interaction, so read it directly
    rather than building a second tracker."""
    from tools import session_summary

    events = session_summary._read_events(session_summary._today_logs())
    timestamps = [
        e.get("ts") for e in events
        if e.get("type") in ("user_speech", "tool_call") and e.get("ts")
    ]
    if not timestamps:
        return None
    try:
        return datetime.fromisoformat(max(timestamps))
    except ValueError:
        return None


def _capture_frame():
    """Same open-one-frame-release-immediately pattern as
    input/presence.py's poll_once() — lifted rather than imported since
    poll_once() also does face-matching and mutates presence_state.json,
    neither of which this needs."""
    import cv2

    cap = cv2.VideoCapture(PRESENCE_CAMERA_INDEX)
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
    finally:
        cap.release()
    return frame if ok else None


def _save_frame(frame):
    import cv2

    now = datetime.now()
    day_dir = FOCUS_PHOTO_BASE_DIR / f"{now:%Y-%m-%d_%A}"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{now:%Y-%m-%d_%H%M%S}.jpg"
    cv2.imwrite(str(path), frame)
    return path


def _build_digest() -> str:
    """Short plain-text context: this week's session logs (as
    session_summary's own no-llm counts summary, not raw transcripts —
    reusing that module rather than writing a second summarizer), today's
    tail transcript, and current tasks/agenda via the existing tools'
    own text-rendering functions."""
    from datetime import timedelta

    from tools import agenda, daily_tasks, session_summary

    week_lines = []
    for i in range(1, 7):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        summary = session_summary.summarise_today(day, llm=None)
        if summary and not summary.startswith("Nothing logged"):
            week_lines.append(f"{day}: {summary}")
    week_text = "\n".join(week_lines) or "(nothing notable logged this past week)"

    today_text = session_summary.recall_recent_conversation(count=20)

    try:
        tasks_text = daily_tasks.list_tasks()
    except Exception as e:
        event_log.log_error("focus_checkin_tasks", e)
        tasks_text = "(unavailable)"

    try:
        agenda_text = agenda.list_items()
    except Exception as e:
        event_log.log_error("focus_checkin_agenda", e)
        agenda_text = "(unavailable)"

    return (
        f"This week:\n{week_text}\n\n"
        f"Today so far:\n{today_text}\n\n"
        f"Open tasks:\n{tasks_text}\n\n"
        f"Agenda:\n{agenda_text}"
    )


def _ask_vision(photo_path, digest: str) -> str:
    """The ONLY vision call this module makes — always local
    vision_server.describe_image(), see the module docstring's privacy
    constraint. Prompted to answer NO_OBSERVATION verbatim when nothing
    stands out, so a comment is the exception, not the default."""
    from llm import vision_server

    data = base64.b64encode(photo_path.read_bytes()).decode("ascii")
    uri = f"data:image/jpeg;base64,{data}"

    prompt = (
        "You are FRED, a voice assistant. This photo was just captured "
        "because Vatsal (address him as 'sir') has been sitting at his "
        "desk but hasn't interacted with you in a while. Context from his "
        f"recent sessions:\n\n{digest}\n\n"
        f"Look at the photo. If nothing about it or the context above is "
        f"genuinely worth a short spoken remark, respond with exactly "
        f"{_NO_OBSERVATION} and nothing else. Otherwise respond with "
        "exactly one short, spoken-style sentence addressed to him as "
        "sir, grounded in what you actually see and/or the context above "
        "— for example noticing what he seems focused on, or a pattern "
        "across recent sessions. No question, no preamble, one sentence."
    )

    return vision_server.describe_image(uri, prompt, max_tokens=80)


def check(notify):
    """Periodic entry point — call with proactive_checks.notify (already
    gated on sleep_mode.is_sleeping()) so a failure here never crashes
    the scheduler, same fail-soft contract as every other check in
    proactive_checks.py."""
    try:
        _check(notify)
    except Exception as e:
        event_log.log_error("focus_checkin", e)


def _check(notify):
    last_interaction = _last_interaction_at()
    if last_interaction is None:
        return

    state = _load_state()
    fc = state.setdefault("focus_checkin", {})

    # A real interaction happened since we last recorded one — reset the
    # threshold back to base and remember this interaction, don't fire.
    if fc.get("last_interaction_iso") != last_interaction.isoformat():
        fc["last_interaction_iso"] = last_interaction.isoformat()
        fc["threshold_minutes"] = FOCUS_CHECKIN_BASE_MINUTES
        _save_state(state)
        return

    if not presence.is_present():
        return

    threshold = fc.get("threshold_minutes", FOCUS_CHECKIN_BASE_MINUTES)
    idle_minutes = (datetime.now() - last_interaction).total_seconds() / 60
    if idle_minutes < threshold:
        return

    frame = _capture_frame()
    if frame is None:
        return
    photo_path = _save_frame(frame)

    reply = (_ask_vision(photo_path, _build_digest()) or "").strip()
    if not reply or reply.upper() == _NO_OBSERVATION:
        return

    notify(reply, title="Focus check-in")
    # Only grows on an actual fire — a silent (NO_OBSERVATION) tick stays
    # at the same threshold and may try again next poll, still gated on
    # presence + no new interaction.
    fc["threshold_minutes"] = threshold + FOCUS_CHECKIN_STEP_MINUTES
    _save_state(state)
