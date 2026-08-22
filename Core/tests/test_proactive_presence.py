"""check_presence — thin scheduler wrapper around presence.poll_once(),
mirroring check_vip_messages/check_recent_calls: a camera hiccup or a
transient vision-model failure must never crash the scheduler."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from input import presence
from orchestrator import consolidation, proactive_checks as pc

_real_poll_once = presence.poll_once


def _raise():
    raise RuntimeError("camera exploded")


try:
    presence.poll_once = _raise
    pc.check_presence()  # must not raise
finally:
    presence.poll_once = _real_poll_once

print("ok")


# =========================================================
# WAKE GREETING — fires only on waking from a REAL debounced sleep-mode
# absence (sleep_mode.PRESENCE_ABSENT_DEBOUNCE consecutive absent polls),
# not on a single-poll blip that never crosses that threshold.
# =========================================================

def _reset(monkeypatch):
    monkeypatch.setattr(pc.sleep_mode, "_streak", 0)
    monkeypatch.setattr(pc.sleep_mode, "_present_streak", 0)
    monkeypatch.setattr(pc.sleep_mode, "_sleeping", False)
    monkeypatch.setattr(consolidation, "_pending", None)
    monkeypatch.setattr(consolidation.reflection, "has_pending_review", lambda: False)
    # sleep_mode.on_presence_poll's absent-edge also runs
    # consolidation.on_sleep_enter() for real (session_summary/vault_map
    # hitting the real vault) — not what these greeting-only tests are
    # about, so keep it a no-op here.
    monkeypatch.setattr(consolidation, "on_sleep_enter", lambda: None)


def test_first_poll_ever_does_not_greet(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(presence, "poll_once", lambda: True)
    monkeypatch.setattr(consolidation, "notify", lambda *a, **k: calls.append(a))

    pc.check_presence()

    assert calls == []


def test_continued_presence_does_not_regreet(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(presence, "poll_once", lambda: True)
    monkeypatch.setattr(consolidation, "notify", lambda *a, **k: calls.append(a))

    pc.check_presence()  # first poll: no prior state, no greeting
    pc.check_presence()  # still present: no edge, no greeting

    assert calls == []


def test_absent_to_present_edge_greets_once(monkeypatch):
    """Cross the real debounce threshold (3 consecutive absent polls)
    before returning, so sleep_mode actually enters sleep — then cross
    the present-debounce threshold (2 consecutive present polls) before
    verifying the greeting fires on waking and not again while staying
    present.

    No consolidation recap pending here (nothing built on_sleep_enter in
    this test), so the single notify() call's message is just the
    greeting text alone."""
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(consolidation, "notify", lambda *a, **k: calls.append(a))

    monkeypatch.setattr(presence, "poll_once", lambda: False)
    for _ in range(pc.sleep_mode.PRESENCE_ABSENT_DEBOUNCE):
        pc.check_presence()
    assert pc.sleep_mode.is_sleeping() is True

    monkeypatch.setattr(presence, "poll_once", lambda: True)
    for _ in range(pc.sleep_mode.PRESENCE_PRESENT_DEBOUNCE - 1):
        pc.check_presence()  # still under the present-debounce -> no greet yet
        assert pc.sleep_mode.is_sleeping() is True
        assert calls == []

    pc.check_presence()  # crosses the present-debounce -> wakes, greets

    pc.check_presence()  # stays present: must not fire again

    assert len(calls) == 1
    assert calls[0][0] in pc._PRESENCE_GREETINGS


def test_absent_to_present_edge_merges_greeting_with_pending_recap(monkeypatch):
    """Live bug, confirmed 2026-08-22: on a real wake with a consolidation
    recap pending, the greeting used to fire as ITS OWN separate notify()
    call right after consolidation's bundled-recap notify() — both routed
    through pill_app._speak_proactive's single turn_lock, and the second
    one silently lost the race and never spoke. Only one message must ever
    be spoken, and it must contain both the greeting and the recap."""
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(consolidation, "notify", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(consolidation, "_pending", "While you were away: 3 requests today.")

    monkeypatch.setattr(presence, "poll_once", lambda: False)
    for _ in range(pc.sleep_mode.PRESENCE_ABSENT_DEBOUNCE):
        pc.check_presence()
    # check_presence's own absence polls run consolidation.on_sleep_enter,
    # which would clobber the recap we just set — pin it back down.
    monkeypatch.setattr(consolidation, "_pending", "While you were away: 3 requests today.")

    monkeypatch.setattr(presence, "poll_once", lambda: True)
    for _ in range(pc.sleep_mode.PRESENCE_PRESENT_DEBOUNCE):
        pc.check_presence()

    assert len(calls) == 1
    message = calls[0][0]
    assert message in pc._PRESENCE_GREETINGS or any(g in message for g in pc._PRESENCE_GREETINGS)
    assert "3 requests today." in message


def test_single_poll_blip_does_not_greet(monkeypatch):
    """One missed poll followed by present again never crosses the
    debounce threshold, so sleep mode never actually engages — no
    greeting should fire."""
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(consolidation, "notify", lambda *a, **k: calls.append(a))

    monkeypatch.setattr(presence, "poll_once", lambda: True)
    pc.check_presence()  # establishes "present" as the prior state

    monkeypatch.setattr(presence, "poll_once", lambda: False)
    pc.check_presence()  # single absent poll — not enough to sleep

    monkeypatch.setattr(presence, "poll_once", lambda: True)
    pc.check_presence()  # back to present, but never really slept

    assert calls == []
