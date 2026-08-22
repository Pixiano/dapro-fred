# Core/orchestrator/security_watch.py
#
# Stranger-at-the-desk detection, layered on top of input/presence.py's
# existing per-poll face detection (see presence.last_classification()) —
# this module adds NO new face detection of its own for the streak check,
# only its own periodic camera poll that reads presence.py's own
# per-poll classification result.
#
# GENUINELY A SEPARATE camera-access path from presence.py's 15s poll
# and focus_checkin.py's own independent capture calls (already a
# precedent for more than one camera-opener existing in this codebase —
# Vatsal's explicit choice, accepting the camera-contention tradeoff
# over reusing the shared 15s poll). Its own periodic check runs every
# SECURITY_WATCH_POLL_SECONDS (5s) via scheduler.add_periodic, same
# mechanism as every other periodic check in this codebase — no
# dedicated timer needed, add_periodic already takes fractional minutes
# (see proactive_checks.register's own PRESENCE_POLL_SECONDS/60 comment
# for why that divides cleanly).
#
# Trigger: SECURITY_STRANGER_DEBOUNCE consecutive 5s ticks where Vatsal
# is genuinely away (not presence.is_present()), an unrecognized face is
# in frame (presence.last_classification()["unrecognized"]), AND real
# input activity is happening right now (idle_seconds() < 5) — someone
# is actually AT the desk using it, not just visible in the background.
# On trigger: burst-save a few photos, engage lockdown, and fold one
# line into consolidation's bundled wake recap.
#
# Wake-edge handling (per-person greeting, and the one-time "lift
# lockdown?" ask) is done by THIS module's own periodic tick detecting
# the sleep_mode.is_sleeping() True->False edge itself (see _was_sleeping
# below), rather than sleep_mode.py calling into this module directly —
# smaller, safer diff: sleep_mode.py/consolidation.py/reflection.py stay
# completely untouched, and this avoids a real import cycle (sleep_mode
# already imports consolidation+reflection; if it also imported this
# module, and this module needed proactive_checks.idle_seconds, that
# would cycle back through proactive_checks' own `from orchestrator
# import ... sleep_mode` import). idle_seconds/sleep_mode/consolidation
# are all imported lazily inside functions here instead, same deferred-
# import trick reflection.py already uses for its own sleep_mode need.

from datetime import datetime, timedelta

from config.settings import SECURITY_STRANGER_DEBOUNCE
from input import presence
from tools import system_tools
from utils import event_log
from utils.notifier import notify

_stranger_streak = 0  # consecutive qualifying 5s ticks, in-memory only
_fired_this_episode = False  # already burst-saved+locked for the current stretch
_locked_by_stranger_event = False  # so a lockdown Vatsal engaged himself is never auto-asked-about
_asked_at = None  # datetime | None — when the lift-lockdown ask was last spoken
_was_sleeping = False  # tracks sleep_mode's own flag to detect the wake edge ourselves

_ASK_WINDOW_MINUTES = 5  # how long the "lift lockdown?" ask stays answerable
_BURST_PHOTO_COUNT = 4  # one-time trigger-time burst, 3-5 per spec — fixed middle value

_prime_carry = None


def configure(prime_carry=None):
    global _prime_carry
    _prime_carry = prime_carry


def check():
    """Periodic entry point, same fail-soft contract as every other
    check in proactive_checks.py — a camera hiccup or lockdown-engage
    failure must never crash the scheduler."""
    try:
        _check()
    except Exception as e:
        event_log.log_error("security_watch", e)


def _check():
    global _stranger_streak, _fired_this_episode, _was_sleeping

    from orchestrator import sleep_mode
    from orchestrator.proactive_checks import idle_seconds

    sleeping_now = sleep_mode.is_sleeping()
    if _was_sleeping and not sleeping_now:
        _on_wake()
    _was_sleeping = sleeping_now

    classification = presence.last_classification()
    triggering = (
        not presence.is_present()
        and classification.get("unrecognized", False)
        and idle_seconds() < 5
    )

    if triggering:
        _stranger_streak += 1
    else:
        _stranger_streak = 0
        _fired_this_episode = False

    if _stranger_streak >= SECURITY_STRANGER_DEBOUNCE and not _fired_this_episode:
        _fired_this_episode = True
        _handle_stranger_detected()


def _burst_save_photos() -> int:
    """One-time burst at trigger time — deliberately NOT part of the
    recurring poll above (see module docstring), a disclosed exception
    since it's a single short burst, not ongoing contention. Same brief
    open/read/release-per-shot pattern as everywhere else in this
    codebase (presence.py's poll_once, focus_checkin._capture_frame)."""
    import cv2
    from config.settings import PRESENCE_CAMERA_INDEX, VAULT_DIR

    now = datetime.now()
    day_dir = VAULT_DIR / "personal" / "security-events" / f"{now:%Y-%m-%d_%A}"
    day_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for i in range(_BURST_PHOTO_COUNT):
        cap = cv2.VideoCapture(PRESENCE_CAMERA_INDEX)
        try:
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok:
            continue
        cv2.imwrite(str(day_dir / f"{now:%Y-%m-%d_%H%M%S}_{i}.jpg"), frame)
        saved += 1
    return saved


def _handle_stranger_detected():
    global _locked_by_stranger_event

    from orchestrator import consolidation
    from state import lockdown_state

    n = _burst_save_photos()

    already_locked = lockdown_state.is_locked()
    system_tools.lockdown_engage()
    if not already_locked:
        # Only claim credit for a lockdown WE engaged — one Vatsal set
        # himself for unrelated reasons must never later get an
        # unsolicited "lift lockdown?" ask from this feature.
        _locked_by_stranger_event = True

    ts = datetime.now().strftime("%I:%M %p").lstrip("0")
    consolidation.append_pending(
        f"An unrecognized person was detected at the desk at {ts} — "
        f"{n} photo(s) saved and lockdown was engaged."
    )
    event_log.log("security_stranger_detected", photos=n, already_locked=already_locked)


def _family_greet_enabled(name: str) -> bool:
    """Reads family_enrollment.json's own per-person `greet` flag
    directly — presence.py's last_classification() only exposes matched
    NAMES, not the greet setting, and this is only read once per wake
    edge so a direct file read is cheap enough not to need caching here
    too."""
    import json

    try:
        data = json.loads(presence.FAMILY_ENROLLMENT_PATH.read_text(encoding="utf-8"))
        return bool(data.get("people", {}).get(name, {}).get("greet", True))
    except (OSError, ValueError):
        return True


def _on_wake():
    """Real presence-return wake edge, detected by _check() above via
    sleep_mode.is_sleeping()'s own True->False transition. Two
    independent things, each folded into an EXISTING single-message
    channel rather than firing a second, competing notify() call (same
    reasoning consolidation.on_sleep_exit's own docstring gives for its
    greeting/recap merge):

      1. Per-person greeting for any recognized family member with
         greet: true — rides consolidation's own bundled wake recap.
      2. If lockdown is still engaged AND WE'RE the one who engaged it
         (_locked_by_stranger_event), ask once whether to lift it. This
         one is its own notify() + _prime_carry, not folded into the
         recap — unlike the greeting lines, it's a real yes/no question
         needing a primed reply, not an informational line."""
    global _asked_at

    from orchestrator import consolidation
    from state import lockdown_state

    for name in presence.last_classification().get("known_people", []):
        if _family_greet_enabled(name):
            consolidation.append_pending(f"Welcome back, {name}.")

    if _locked_by_stranger_event and lockdown_state.is_locked():
        notify("Welcome back, sir — lift lockdown?", title="Security")
        if _prime_carry is not None:
            try:
                _prime_carry(["confirm_lockdown_lift"])
            except Exception as e:
                print(f"[security_watch] prime_carry failed: {e}")
        _asked_at = datetime.now()


def ask_window_open() -> bool:
    """True while a just-spoken 'lift lockdown?' ask is still within its
    answerable window — see tools/security_tools.confirm_lockdown_lift,
    the only caller."""
    return _asked_at is not None and datetime.now() - _asked_at <= timedelta(minutes=_ASK_WINDOW_MINUTES)
