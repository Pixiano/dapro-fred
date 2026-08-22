# Core/orchestrator/consolidation.py
#
# Sleep-mode consolidation: build a day-summary + MAP.md-gap preview
# while Vatsal's away (on_sleep_enter), then speak one bundled recap
# the moment he's back (on_sleep_exit). Both previews are read-only —
# nothing is written to the vault until Vatsal explicitly says yes
# afterward, same propose-then-write split tools/session_summary.py and
# tools/vault_map.py already follow.
#
# In-memory only, fire-once, lost on restart — same reasoning as
# sleep_mode.py's own state: a restart is a real event, not a crash to
# recover from.
#
# Deliberately does NOT import orchestrator.sleep_mode — sleep_mode.py
# imports this module (not the other way), so importing it back here
# would be circular. Also does NOT go through proactive_checks.notify:
# same import-cycle reason, and by the time on_sleep_exit runs
# is_sleeping() has already gone False, so that gate would pass through
# regardless — utils.notifier.notify is called directly instead, the
# same underlying function proactive_checks.py itself wraps.

from orchestrator import reflection
from tools import session_summary, vault_map
from utils.notifier import notify

_llm = None
_pending = None  # str | None — the bundled recap, waiting to be spoken


def configure(llm):
    global _llm
    _llm = llm


def on_sleep_enter():
    """Build the recap while Vatsal's away. Read-only, and never
    raises — a failure here must not block sleep-mode entry itself."""
    global _pending
    try:
        summary = session_summary.preview_session_summary(llm=_llm)
        gap = vault_map.preview_missing()
        _pending = f"While you were away: {summary}" + (f" Also, {gap}" if gap else "")
    except Exception as e:
        print(f"[consolidation] sleep-entry build failed: {e}")
        _pending = None


def append_pending(text: str):
    """
    Fold another module's own audit line into the same bundled recap,
    rather than competing with it as a second proactive announcement.
    reflection.py's sleep_mode.py hook calls this with its own short
    audit line ("Updated people/x.md ...") right after on_sleep_enter()
    runs, same call site, same cycle.
    """
    global _pending
    if not text:
        return
    _pending = f"{_pending} {text}" if _pending else text


def on_sleep_exit(greeting: str = None):
    """
    Speak the bundled recap once, then clear it — fire-once, not
    re-spoken on a later wake with nothing new pending.

    greeting: an optional short wake-greeting (e.g. "Welcome back,
    sir.") to prefix onto the same message, passed in by
    sleep_mode.on_presence_poll on a real presence-triggered wake.
    Confirmed live 2026-08-22: proactive_checks.check_presence used to
    fire its own separate notify() for this right after this function's
    notify() call — both landed in the same tick, competing for
    pill_app._speak_proactive's turn_lock, and the second one silently
    lost the race and never spoke. Folded in here instead, same fix
    this function already applies to reflection's review-offer below:
    one bundled message, not competing proactive announcements.

    Also offers reflection's staged self-fact review right alongside
    it, same wake moment, same single notify call — not a second,
    competing proactive announcement. This can fire even when there's
    no `_pending` recap at all (an unreviewed draft can be sitting
    there from days ago, long after the cycle that staged it).
    """
    global _pending
    message = _pending
    if greeting:
        message = f"{greeting} {message}" if message else greeting

    try:
        if reflection.has_pending_review():
            offer = reflection.offer_review_text()
            message = f"{message} {offer}" if message else offer
    except Exception as e:
        print(f"[consolidation] reflection review-offer failed: {e}")

    _pending = None
    if not message:
        return
    try:
        notify(message, title="Welcome back")
    except Exception as e:
        print(f"[consolidation] sleep-exit notify failed: {e}")


if __name__ == "__main__":
    # Self-check: pending-state lifecycle with the real tools stubbed
    # out (no LLM, no real vault needed to prove enter->exit->cleared).
    import tools.session_summary as _ss
    import tools.vault_map as _vm
    from orchestrator import reflection as _refl

    calls = []
    globals()["notify"] = lambda msg, title="F.R.E.D.": calls.append((msg, title))
    _ss.preview_session_summary = lambda day=None, llm=None: "3 requests today."
    _vm.preview_missing = lambda: "2 vault files aren't mapped yet: a.md, b.md."
    _refl.has_pending_review = lambda: False

    assert _pending is None
    on_sleep_exit()  # nothing pending yet — must be a no-op
    assert calls == []

    on_sleep_enter()
    assert _pending is not None
    assert "3 requests today." in _pending
    assert "2 vault files" in _pending

    on_sleep_exit()
    assert calls and "3 requests today." in calls[0][0]
    assert _pending is None  # fire-once

    on_sleep_exit()  # already cleared — still a no-op
    assert len(calls) == 1

    print("consolidation self-check: all passed")
