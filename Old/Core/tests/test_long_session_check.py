# Core/tests/test_long_session_check.py
#
# check_long_session's restart-gap bug, fixed 2026-08-28: a stale
# last_break surviving a real FRED-down gap (crash/restart, not just
# Vatsal idle-but-FRED-alive) was silently counted as continuous work,
# producing false "3 hours straight" claims. Verifies the fix (an
# anomalously large gap between polls resets the continuity clock) and
# that the ordinary on-cadence path is unchanged.

import json
from datetime import datetime, timedelta

from orchestrator import proactive_checks as pc


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "PROACTIVE_STATE_PATH", tmp_path / "proactive_state.json")
    monkeypatch.setattr(pc, "PROACTIVE_BREAK_IDLE_MINUTES", 15)
    monkeypatch.setattr(pc, "PROACTIVE_LONG_SESSION_HOURS", 3)
    monkeypatch.setattr(pc, "PROACTIVE_LONG_SESSION_RESTART_GAP_MINUTES", 45)


def _notifications(monkeypatch):
    seen = []
    monkeypatch.setattr(pc, "notify", lambda msg, title="F.R.E.D.": seen.append(msg))
    return seen


def _write_state(path, last_break, last_poll_at, notified=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "long_session": {
            "last_break": last_break.isoformat(),
            "last_poll_at": last_poll_at.isoformat(),
            **({"notified": True} if notified else {}),
        }
    }), encoding="utf-8")


def test_normal_cadence_still_fires_after_threshold(tmp_path, monkeypatch):
    """No regression: a normal on-cadence poll sequence (small gaps
    between polls) still fires once the real elapsed time crosses the
    3-hour threshold."""
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    monkeypatch.setattr(pc, "idle_seconds", lambda: 0.0)

    now = datetime.now()
    _write_state(pc.PROACTIVE_STATE_PATH,
                  last_break=now - timedelta(hours=3, minutes=1),
                  last_poll_at=now - timedelta(minutes=10))  # normal-size gap

    pc.check_long_session()

    assert len(seen) == 1
    assert "3 hours" in seen[0]


def test_restart_gap_resets_continuity_instead_of_firing(tmp_path, monkeypatch):
    """The actual 2026-08-28 bug: last_break is 3+ hours old, but the
    gap since the last poll is huge (FRED was down, not just idle) --
    must NOT fire, must reset last_break instead of claiming continuity
    through the downtime."""
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    monkeypatch.setattr(pc, "idle_seconds", lambda: 0.0)

    now = datetime.now()
    _write_state(pc.PROACTIVE_STATE_PATH,
                  last_break=now - timedelta(hours=4),
                  last_poll_at=now - timedelta(hours=2))  # FRED was down 2h

    pc.check_long_session()

    assert seen == []
    state = json.loads(pc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    last_break = datetime.fromisoformat(state["long_session"]["last_break"])
    assert (now - last_break).total_seconds() < 5  # reset to ~now, not left at 4h ago


def test_restart_gap_does_not_refire_already_notified_flag_incorrectly(tmp_path, monkeypatch):
    """A restart-gap reset also clears the 'notified' flag, same as a
    real break does -- the next genuine 3h stretch should be able to
    notify again, not stay silenced by a stale flag."""
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    monkeypatch.setattr(pc, "idle_seconds", lambda: 0.0)

    now = datetime.now()
    _write_state(pc.PROACTIVE_STATE_PATH,
                  last_break=now - timedelta(hours=5),
                  last_poll_at=now - timedelta(hours=2),
                  notified=True)

    pc.check_long_session()
    assert seen == []

    state = json.loads(pc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    assert "notified" not in state["long_session"]


def test_small_gap_between_polls_does_not_reset(tmp_path, monkeypatch):
    """An ordinary poll-to-poll gap (well under the restart-gap
    threshold) must not be mistaken for a FRED-down stretch."""
    _isolate(tmp_path, monkeypatch)
    seen = _notifications(monkeypatch)
    monkeypatch.setattr(pc, "idle_seconds", lambda: 0.0)

    now = datetime.now()
    original_break = now - timedelta(hours=1)
    _write_state(pc.PROACTIVE_STATE_PATH,
                  last_break=original_break,
                  last_poll_at=now - timedelta(minutes=15))

    pc.check_long_session()

    assert seen == []  # not at 3h yet
    state = json.loads(pc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    last_break = datetime.fromisoformat(state["long_session"]["last_break"])
    assert abs((last_break - original_break).total_seconds()) < 5  # untouched
