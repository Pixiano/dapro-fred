# Core/tests/test_media_state.py
#
# Pure logic tests for audio/media_state.py — pycaw's AudioUtilities is
# entirely mocked (fake sessions with a fake _ctl.QueryInterface), no
# real audio hardware needed. The real pycaw API itself (peak-metering,
# self-pid exclusion) was verified live 2026-08-25 — see media_state.py's
# own module docstring; this file only pins the decision logic on top
# of that API.

import os

from audio import media_state


class _FakeMeter:
    def __init__(self, peak):
        self._peak = peak

    def GetPeakValue(self):
        return self._peak


class _FakeCtl:
    def __init__(self, peak):
        self._peak = peak

    def QueryInterface(self, iface):
        return _FakeMeter(self._peak)


class _FakeProcess:
    def __init__(self, pid):
        self.pid = pid


class _FakeSession:
    def __init__(self, pid, peak):
        self.Process = _FakeProcess(pid) if pid is not None else None
        self._ctl = _FakeCtl(peak)


def test_no_sessions_means_not_playing(monkeypatch):
    monkeypatch.setattr(media_state.AudioUtilities, "GetAllSessions", lambda: [])
    assert media_state.is_media_playing() is False


def test_other_process_above_threshold_means_playing(monkeypatch):
    sessions = [_FakeSession(pid=99999, peak=0.5)]
    monkeypatch.setattr(media_state.AudioUtilities, "GetAllSessions", lambda: sessions)
    assert media_state.is_media_playing() is True


def test_own_pid_excluded_even_at_high_peak(monkeypatch):
    """The exact bug this module exists to avoid: FRED's own TTS output
    must never register as "media playing"."""
    sessions = [_FakeSession(pid=os.getpid(), peak=0.9)]
    monkeypatch.setattr(media_state.AudioUtilities, "GetAllSessions", lambda: sessions)
    assert media_state.is_media_playing() is False


def test_below_threshold_means_not_playing(monkeypatch):
    sessions = [_FakeSession(pid=99999, peak=0.0)]
    monkeypatch.setattr(media_state.AudioUtilities, "GetAllSessions", lambda: sessions)
    assert media_state.is_media_playing() is False


def test_session_with_no_process_is_skipped_not_fatal(monkeypatch):
    sessions = [_FakeSession(pid=None, peak=0.9)]
    monkeypatch.setattr(media_state.AudioUtilities, "GetAllSessions", lambda: sessions)
    assert media_state.is_media_playing() is False
