# Core/tests/test_focus_checkin.py
#
# Pure logic tests for orchestrator/focus_checkin.py's threshold-growth
# state machine and the NO_OBSERVATION gate. Camera, vision model, and
# session-log reads are all mocked — no real inference, no real hardware.

import json
from datetime import datetime, timedelta

import pytest

from orchestrator import focus_checkin as fc


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point PROACTIVE_STATE_PATH at a throwaway file so this test never
    touches the real proactive_state.json."""
    monkeypatch.setattr(fc, "PROACTIVE_STATE_PATH", tmp_path / "proactive_state.json")


def test_threshold_grows_by_step_on_repeated_fires(monkeypatch):
    interaction_time = datetime.now() - timedelta(minutes=200)
    monkeypatch.setattr(fc, "_last_interaction_at", lambda: interaction_time)
    monkeypatch.setattr(fc.presence, "is_present", lambda: True)
    monkeypatch.setattr(fc, "_capture_frame", lambda: object())
    monkeypatch.setattr(fc, "_save_frame", lambda frame: "fake_path")
    monkeypatch.setattr(fc, "_build_digest", lambda: "digest")
    monkeypatch.setattr(fc, "_ask_vision", lambda photo, digest: "You look focused, sir.")

    calls = []
    notify = lambda *a, **k: calls.append(a)

    # First tick: no prior state -> records the interaction, does not fire.
    fc.check(notify)
    assert calls == []

    # Second tick: same interaction still current, past base threshold (60) -> fires.
    fc.check(notify)
    assert len(calls) == 1
    state = json.loads(fc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    assert state["focus_checkin"]["threshold_minutes"] == 60 + 10
    assert state["focus_checkin"]["fired_at_iso"] is not None

    # Third tick, same stale interaction time (idle-since-interaction
    # still reads 200min): must NOT refire immediately. This is the
    # exact bug this anchor fixes -- backoff is measured from the last
    # FIRE (just now), not from idle-since-interaction, so a poll
    # moments later stays quiet until the grown threshold (70min) has
    # actually elapsed since the fire.
    fc.check(notify)
    assert len(calls) == 1


def test_threshold_never_exceeds_max(monkeypatch):
    interaction_time = datetime.now() - timedelta(minutes=200)
    monkeypatch.setattr(fc, "_last_interaction_at", lambda: interaction_time)
    monkeypatch.setattr(fc.presence, "is_present", lambda: True)
    monkeypatch.setattr(fc, "_capture_frame", lambda: object())
    monkeypatch.setattr(fc, "_save_frame", lambda frame: "fake_path")
    monkeypatch.setattr(fc, "_build_digest", lambda: "digest")
    monkeypatch.setattr(fc, "_ask_vision", lambda photo, digest: "You look focused, sir.")

    calls = []
    notify = lambda *a, **k: calls.append(a)

    fc.check(notify)  # record

    # Seed state just under the cap, fired long enough ago (and cycle
    # started recently, so the 12h reset doesn't interfere) to be
    # eligible to fire again this tick.
    state = json.loads(fc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    state["focus_checkin"]["threshold_minutes"] = fc.FOCUS_CHECKIN_MAX_MINUTES - 5
    state["focus_checkin"]["fired_at_iso"] = (
        datetime.now() - timedelta(minutes=fc.FOCUS_CHECKIN_MAX_MINUTES)
    ).isoformat()
    state["focus_checkin"]["cycle_started_iso"] = datetime.now().isoformat()
    fc._save_state(state)

    fc.check(notify)  # fires -- would grow past the cap without clamping
    assert len(calls) == 1
    state = json.loads(fc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    assert state["focus_checkin"]["threshold_minutes"] == fc.FOCUS_CHECKIN_MAX_MINUTES


def test_12h_cycle_resets_without_real_interaction(monkeypatch):
    interaction_time = datetime.now() - timedelta(minutes=200)
    monkeypatch.setattr(fc, "_last_interaction_at", lambda: interaction_time)
    monkeypatch.setattr(fc.presence, "is_present", lambda: True)

    calls = []
    notify = lambda *a, **k: calls.append(a)

    fc.check(notify)  # record, sets cycle_started_iso to now

    # Seed a grown threshold with a cycle_started_iso 13h in the past --
    # same stale interaction the whole time, no real interaction reset
    # available to lean on.
    state = json.loads(fc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    state["focus_checkin"]["threshold_minutes"] = 150
    state["focus_checkin"]["fired_at_iso"] = (datetime.now() - timedelta(minutes=200)).isoformat()
    state["focus_checkin"]["cycle_started_iso"] = (datetime.now() - timedelta(hours=13)).isoformat()
    fc._save_state(state)

    fc.check(notify)  # 12h elapsed since cycle start -> resets to base, does not fire
    assert calls == []
    state = json.loads(fc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    assert state["focus_checkin"]["threshold_minutes"] == fc.FOCUS_CHECKIN_BASE_MINUTES


def test_real_interaction_resets_threshold_to_base(monkeypatch):
    monkeypatch.setattr(fc.presence, "is_present", lambda: True)
    monkeypatch.setattr(fc, "_capture_frame", lambda: object())
    monkeypatch.setattr(fc, "_save_frame", lambda frame: "fake_path")
    monkeypatch.setattr(fc, "_build_digest", lambda: "digest")
    monkeypatch.setattr(fc, "_ask_vision", lambda photo, digest: "You look focused, sir.")

    calls = []
    notify = lambda *a, **k: calls.append(a)

    old_interaction = datetime.now() - timedelta(minutes=200)
    monkeypatch.setattr(fc, "_last_interaction_at", lambda: old_interaction)
    fc.check(notify)  # record
    fc.check(notify)  # fire, threshold -> 70
    state = json.loads(fc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    assert state["focus_checkin"]["threshold_minutes"] == 70

    # A fresh, real interaction arrives.
    new_interaction = datetime.now()
    monkeypatch.setattr(fc, "_last_interaction_at", lambda: new_interaction)
    fc.check(notify)  # sees a newer interaction -> resets, does not fire
    state = json.loads(fc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    assert state["focus_checkin"]["threshold_minutes"] == 60
    assert len(calls) == 1  # unchanged from before the reset


def test_no_observation_reply_does_not_notify(monkeypatch):
    interaction_time = datetime.now() - timedelta(minutes=200)
    monkeypatch.setattr(fc, "_last_interaction_at", lambda: interaction_time)
    monkeypatch.setattr(fc.presence, "is_present", lambda: True)
    monkeypatch.setattr(fc, "_capture_frame", lambda: object())
    monkeypatch.setattr(fc, "_save_frame", lambda frame: "fake_path")
    monkeypatch.setattr(fc, "_build_digest", lambda: "digest")
    monkeypatch.setattr(fc, "_ask_vision", lambda photo, digest: "NO_OBSERVATION")

    calls = []
    notify = lambda *a, **k: calls.append(a)

    fc.check(notify)  # record
    fc.check(notify)  # eligible, but model declines

    assert calls == []
    state = json.loads(fc.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    # Threshold must NOT grow on a silent tick — only a real fire grows it.
    assert state["focus_checkin"]["threshold_minutes"] == 60


def test_not_present_does_not_fire(monkeypatch):
    interaction_time = datetime.now() - timedelta(minutes=200)
    monkeypatch.setattr(fc, "_last_interaction_at", lambda: interaction_time)
    monkeypatch.setattr(fc.presence, "is_present", lambda: False)

    calls = []
    notify = lambda *a, **k: calls.append(a)

    fc.check(notify)  # record
    fc.check(notify)  # would be eligible on time alone, but not present

    assert calls == []


def test_below_threshold_does_not_fire(monkeypatch):
    interaction_time = datetime.now() - timedelta(minutes=5)
    monkeypatch.setattr(fc, "_last_interaction_at", lambda: interaction_time)
    monkeypatch.setattr(fc.presence, "is_present", lambda: True)

    calls = []
    notify = lambda *a, **k: calls.append(a)

    fc.check(notify)  # record
    fc.check(notify)  # only 5 minutes idle, base threshold is 60

    assert calls == []


# =========================================================
# DAILY-FOLDER-WITH-WEEKDAY PATH CONSTRUCTION -- _save_frame() now saves
# under FOCUS_PHOTO_BASE_DIR/YYYY-MM-DD_Weekday/, one subfolder per
# calendar day, instead of the old flat focus-checkins/ folder.
# =========================================================

def test_save_frame_uses_daily_weekday_subfolder(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "FOCUS_PHOTO_BASE_DIR", tmp_path)

    saved = {}

    def _fake_imwrite(path, frame):
        saved["path"] = path
        return True

    import cv2
    monkeypatch.setattr(cv2, "imwrite", _fake_imwrite)

    path = fc._save_frame(object())

    today = datetime.now()
    expected_dir = tmp_path / f"{today:%Y-%m-%d_%A}"
    assert path.parent == expected_dir
    assert expected_dir.is_dir()
    assert path.name.startswith(f"{today:%Y-%m-%d}_") and path.suffix == ".jpg"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
