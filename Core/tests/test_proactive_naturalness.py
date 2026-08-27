# Core/tests/test_proactive_naturalness.py
#
# Pure logic tests for proactive_checks.py's naturalness gate
# (_update_interruptibility/_ready_to_interrupt/notify's urgent bypass)
# -- principles 2-5 from plan_perception_features_2026-08-25.md's
# "Proactivity naturalness principles" section. Camera/media/window
# reads are all mocked, no real hardware.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from orchestrator import proactive_checks as pc


@pytest.fixture(autouse=True)
def _reset_gate_state(monkeypatch):
    """Module-level streak state, same "reset per test" need every other
    in-memory streak counter in this codebase's tests has (see
    test_headphone_watch.py's own HEADPHONE_CHECK_STREAK resets)."""
    monkeypatch.setattr(pc, "_interruptible_streak", 0)
    monkeypatch.setattr(pc, "_last_window_title", None)
    monkeypatch.setattr(pc, "_last_media_playing", False)
    monkeypatch.setattr(pc, "_task_boundary_this_tick", False)
    monkeypatch.setattr(pc, "PROACTIVE_INTERRUPT_STREAK", 3)


def _tick(monkeypatch, present, media_playing, title):
    """_update_interruptibility() imports both audio.media_state and
    win32gui LOCALLY (see its own docstring on why -- module-level would
    load pycaw/win32 for every process that imports proactive_checks.py,
    including the CLI), so patching the real modules is what actually
    takes effect here, not pc's own namespace."""
    import win32gui

    monkeypatch.setattr(pc.presence, "is_present", lambda: present)
    monkeypatch.setattr("audio.media_state.is_media_playing", lambda: media_playing)
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(win32gui, "GetWindowText", lambda h: title)
    pc._update_interruptibility()


def test_ready_false_before_any_tick():
    assert pc._ready_to_interrupt() is False


def test_streak_builds_and_then_fires(monkeypatch):
    for _ in range(pc.PROACTIVE_INTERRUPT_STREAK - 1):
        _tick(monkeypatch, present=True, media_playing=False, title="Notepad")
        assert pc._ready_to_interrupt() is False  # streak not there yet

    _tick(monkeypatch, present=True, media_playing=False, title="Notepad")
    assert pc._ready_to_interrupt() is True  # streak cleared the requirement


def test_media_playing_blocks_and_resets_streak(monkeypatch):
    for _ in range(pc.PROACTIVE_INTERRUPT_STREAK):
        _tick(monkeypatch, present=True, media_playing=False, title="Notepad")
    assert pc._ready_to_interrupt() is True

    _tick(monkeypatch, present=True, media_playing=True, title="Notepad")
    assert pc._ready_to_interrupt() is False  # busy -> streak reset to 0


def test_absent_blocks(monkeypatch):
    for _ in range(pc.PROACTIVE_INTERRUPT_STREAK):
        _tick(monkeypatch, present=False, media_playing=False, title="Notepad")
    assert pc._ready_to_interrupt() is False


def test_task_boundary_skips_the_wait(monkeypatch):
    # First real observation -- title/media baseline, no boundary yet
    # (first tick can never be "changed" from an unobserved state).
    _tick(monkeypatch, present=True, media_playing=False, title="Notepad")
    assert pc._ready_to_interrupt() is False  # streak=1, still below requirement

    # Foreground window changes -- a task boundary -- while still a good
    # moment: should be allowed to fire immediately, streak requirement
    # or not.
    _tick(monkeypatch, present=True, media_playing=False, title="Chrome")
    assert pc._ready_to_interrupt() is True


def test_media_just_stopped_is_a_task_boundary(monkeypatch):
    _tick(monkeypatch, present=True, media_playing=True, title="Spotify")
    assert pc._ready_to_interrupt() is False  # busy -- media playing

    _tick(monkeypatch, present=True, media_playing=False, title="Spotify")
    assert pc._ready_to_interrupt() is True  # media just stopped -> boundary


def test_boundary_during_busy_moment_still_does_not_fire(monkeypatch):
    _tick(monkeypatch, present=True, media_playing=False, title="Notepad")
    # Window changes AND media starts in the same tick -- a boundary, but
    # the composite signal is currently bad (busy), so it must not fire.
    _tick(monkeypatch, present=True, media_playing=True, title="Chrome")
    assert pc._ready_to_interrupt() is False


def test_notify_urgent_bypasses_the_gate(monkeypatch):
    calls = []
    monkeypatch.setattr(pc, "sleep_mode", type("_S", (), {"is_sleeping": staticmethod(lambda: False)}))
    monkeypatch.setattr(pc, "_real_notify", lambda *a, **k: calls.append(a))

    pc.notify("hello", title="Test")  # streak=0 -> gated, no call
    assert calls == []

    pc.notify("hello", title="Test", urgent=True)  # bypasses the gate
    assert calls == [("hello",)]
