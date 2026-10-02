# watch_for_silence is the piece hold-to-talk never needed: there's no
# keyup for a wake-triggered turn, so end-of-utterance has to be
# guessed. Pins the two ways it can end (silence after real speech, or
# the safety cap when nobody ever speaks) and that an external cancel
# never fires the callback at all.

import threading
import time

from input import wakeword


class _FakeSTT:
    """Reports .level as loud for `loud_seconds` from construction, then silent."""

    def __init__(self, loud_seconds):
        self._loud_seconds = loud_seconds
        self._start = time.monotonic()

    @property
    def level(self):
        return 0.5 if time.monotonic() - self._start < self._loud_seconds else 0.0


def test_fires_after_silence_following_speech(monkeypatch):
    monkeypatch.setattr(wakeword, "SILENCE_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(wakeword, "MAX_UTTERANCE_SECONDS", 5.0)

    stt = _FakeSTT(loud_seconds=0.1)
    fired = threading.Event()

    start = time.monotonic()
    wakeword.watch_for_silence(stt, fired.set, threading.Event())
    elapsed = time.monotonic() - start

    assert fired.is_set()
    # ~loud_seconds + SILENCE_TIMEOUT_SECONDS, not instant and nowhere
    # near the much longer MAX_UTTERANCE_SECONDS cap.
    assert 0.15 < elapsed < 1.0


def test_never_speaking_hits_the_max_duration_cap(monkeypatch):
    monkeypatch.setattr(wakeword, "SILENCE_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(wakeword, "MAX_UTTERANCE_SECONDS", 0.2)

    stt = _FakeSTT(loud_seconds=0.0)  # never crosses SPEECH_RMS_FLOOR
    fired = threading.Event()

    wakeword.watch_for_silence(stt, fired.set, threading.Event())

    assert fired.is_set()


def test_stop_flag_cancels_without_firing():
    stt = _FakeSTT(loud_seconds=0.0)
    fired = threading.Event()
    stop_flag = threading.Event()
    stop_flag.set()  # cancelled before the watch even starts

    wakeword.watch_for_silence(stt, fired.set, stop_flag)

    assert not fired.is_set()
