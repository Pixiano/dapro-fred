# Reported bug: FRED "speaking forever" with the hotkey unable to stop
# it. Root cause was _speak_proactive's self.tts.speak() call (every
# reminder/timer/greeting/proactive-check routes through this one
# function) omitting `cancel=self._cancel` -- so a hotkey press during a
# proactive utterance set the flag but nothing was listening for it.
#
# Fixed by passing cancel=self._cancel through, same as the main-turn
# path already did. That reopened a second risk: self._cancel is never
# cleared between activations except by whoever holds _turn_lock next
# (see _run_turn's clear right after acquiring it) -- so a stale cancel
# left set by an EARLIER interrupted turn would make the very next
# proactive utterance's tts.speak() abort before a single word played.
# _speak_proactive now clears it too, right after acquiring the lock,
# same pattern.
#
# PillApp.__init__ needs real hardware; bare __new__ instance with only
# what _speak_proactive's run() actually touches, same pattern as
# test_turn_dedup.py.

import threading

from ui.pill_app import PillApp


class _FakeWindow:
    def set_transcript(self, *a, **k):
        pass

    def set_state(self, *a, **k):
        pass

    def show(self):
        pass

    def set_level(self, *a, **k):
        pass


class _FakeWakeword:
    def pause(self):
        pass

    def resume(self):
        pass


class _RecordingTTS:
    """Stands in for KokoroTTS.speak -- records the cancel kwarg it was
    called with instead of actually synthesising anything."""

    def __init__(self):
        self.calls = []

    def speak(self, message, on_level=None, cancel=None):
        self.calls.append(cancel)
        # A real cancel event set before this call would abort playback
        # immediately (see tts_kokoro.py's stopping()) -- modelled here
        # as returning nothing spoken.
        if cancel is not None and cancel.is_set():
            return ""
        return message


def _bare_app(tts):
    app = PillApp.__new__(PillApp)
    app._turn_lock = threading.Lock()
    app._cancel = threading.Event()
    app._recording = False
    app.tts = tts
    app.window = _FakeWindow()
    app.wakeword = _FakeWakeword()
    app._to_idle_and_hide = lambda: None
    return app


def test_proactive_speech_passes_the_shared_cancel_event():
    tts = _RecordingTTS()
    app = _bare_app(tts)

    app._speak_proactive("reminder: stand up")
    for t in threading.enumerate():
        if t is not threading.current_thread():
            t.join(timeout=2)

    assert tts.calls == [app._cancel]


def test_stale_cancel_from_an_earlier_turn_does_not_silence_a_new_proactive_utterance():
    tts = _RecordingTTS()
    app = _bare_app(tts)
    app._cancel.set()  # simulate: a hotkey press during a PREVIOUS, already-finished turn

    app._speak_proactive("reminder: stand up")
    for t in threading.enumerate():
        if t is not threading.current_thread():
            t.join(timeout=2)

    # Must have been cleared before speak() ran, or the utterance would
    # have been silently aborted on arrival.
    assert tts.calls and not tts.calls[0].is_set()
