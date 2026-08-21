"""check_presence — thin scheduler wrapper around presence.poll_once(),
mirroring check_vip_messages/check_recent_calls: a camera hiccup or a
transient vision-model failure must never crash the scheduler."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from input import presence
from orchestrator import proactive_checks as pc

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
# WAKE GREETING — absent->present edge fires it exactly once;
# staying present, and the first-ever poll, must not fire it.
# =========================================================

def _reset(monkeypatch):
    monkeypatch.setattr(pc, "_was_present", None)
    monkeypatch.setattr(pc.sleep_mode, "_streak", 0)
    monkeypatch.setattr(pc.sleep_mode, "_sleeping", False)


def test_first_poll_ever_does_not_greet(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(presence, "poll_once", lambda: True)
    monkeypatch.setattr(pc, "notify", lambda *a, **k: calls.append(a))

    pc.check_presence()

    assert calls == []
    assert pc._was_present is True


def test_continued_presence_does_not_regreet(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(presence, "poll_once", lambda: True)
    monkeypatch.setattr(pc, "notify", lambda *a, **k: calls.append(a))

    pc.check_presence()  # first poll: no prior state, no greeting
    pc.check_presence()  # still present: no edge, no greeting

    assert calls == []


def test_absent_to_present_edge_greets_once(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(pc, "notify", lambda *a, **k: calls.append(a))

    monkeypatch.setattr(presence, "poll_once", lambda: False)
    pc.check_presence()  # establishes "absent" as the prior state

    monkeypatch.setattr(presence, "poll_once", lambda: True)
    pc.check_presence()  # absent -> present edge

    pc.check_presence()  # stays present: must not fire again

    assert len(calls) == 1
    assert calls[0][0] in pc._PRESENCE_GREETINGS
