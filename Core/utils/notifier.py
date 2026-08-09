# Core/utils/notifier.py
#
# Phase 15 — "He Speaks First." Proactive notifications need to
# actually interrupt: a Windows toast (visual) plus spoken TTS
# (voice), not a silent print buried in a log file.

from winotify import Notification

_tts = None
_voice = None
_recorder = None


def set_recorder(record_callable):
    """
    Route what FRED says proactively back into its own conversation
    history, so a reply to it isn't answered from amnesia.

    Confirmed 2026-08-09: a proactive check asked "are you prepped for
    [the movie you just logged]?", the user answered "No, not yet.",
    and FRED replied "I won't log the movie" — treating the negative as
    declining to LOG something, because notify() never told
    ConversationState this question had been asked at all.
    _build_messages only ever sees self.state.get_recent_messages(), and
    proactive speech happens entirely outside process()/process_stream(),
    the only places that ever wrote to it. Every check in
    proactive_checks.py has this gap, not just the ones that ask a
    question — this fixes it once, at the one place all of them speak
    through, rather than patching each check that happens to ask
    something.

    Pass None (the default) to record nothing — the CLI, or any caller
    with no conversation state to keep in sync.
    """
    global _recorder
    _recorder = record_callable


def set_voice(speak_callable):
    """
    Route proactive speech through FRED's own voice.

    GUI mode speaks with Kokoro while this module's fallback is SAPI, so
    without this a reminder interrupted you in a completely different —
    and much worse — voice than the assistant you'd been talking to. The
    Kokoro instance lives in the UI controller, which the scheduler must
    not depend on, so the controller injects it here instead.

    Pass None to fall back to SAPI.
    """
    global _voice
    _voice = speak_callable


def _get_tts():
    """Lazy SAPI fallback, used only when set_voice hasn't been called —
    i.e. the CLI, where Kokoro was never started."""
    global _tts

    if _tts is None:
        from audio.tts import TTSManager
        _tts = TTSManager()

    return _tts


def _speak(message: str):
    if _voice is not None:
        _voice(message)
    else:
        _get_tts().speak(message)


def notify(message: str, title: str = "F.R.E.D."):
    """
    Surfaces a proactive message three ways at once: printed to the
    console, a Windows toast notification, and spoken aloud — so it
    reaches the user regardless of whether they're looking at the
    screen, away from it, or mid-voice-conversation.
    """

    print(f"\n[F.R.E.D.] {message}\n")

    try:
        Notification(
            app_id="F.R.E.D.",
            title=title,
            msg=message,
            duration="short",
        ).show()
    except Exception as e:
        print(f"[NOTIFY ERROR] Toast failed: {e}")

    try:
        _speak(message)
    except Exception as e:
        print(f"[NOTIFY ERROR] TTS failed: {e}")

    if _recorder is not None:
        try:
            _recorder(message)
        except Exception as e:
            print(f"[NOTIFY ERROR] Recording to conversation state failed: {e}")
