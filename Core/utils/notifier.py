# Core/utils/notifier.py
#
# Phase 15 — "He Speaks First." Proactive notifications need to
# actually interrupt: a Windows toast (visual) plus spoken TTS
# (voice), not a silent print buried in a log file.

from winotify import Notification

from audio.tts import TTSManager

_tts = None


def _get_tts() -> TTSManager:

    global _tts

    if _tts is None:
        _tts = TTSManager()

    return _tts


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
        _get_tts().speak(message)
    except Exception as e:
        print(f"[NOTIFY ERROR] TTS failed: {e}")
