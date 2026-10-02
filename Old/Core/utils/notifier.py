# Core/utils/notifier.py
#
# Phase 15 — "He Speaks First." Proactive notifications need to
# actually interrupt: a Windows toast (visual) plus spoken TTS
# (voice), not a silent print buried in a log file.

import time

from winotify import Notification

_tts = None
_voice = None
_recorder = None

# Set by notify(); read by the orchestrator when building a turn. See the
# comment at the write site for why this is separate from the transcript.
_last_proactive = None

# How long an interruption stays worth mentioning as context. Long enough
# to cover "what was that?" asked after a pause, short enough that an
# hour-old reminder isn't still colouring an unrelated conversation.
PROACTIVE_CONTEXT_SECONDS = 600


def last_proactive(within_seconds: float = PROACTIVE_CONTEXT_SECONDS):
    """
    The most recent unprompted thing FRED said, if it was recent enough
    to still be what the user is talking about. None otherwise.
    """
    if not _last_proactive:
        return None
    if time.time() - _last_proactive["at"] > within_seconds:
        return None
    return dict(_last_proactive)


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

    # What was interrupted with, and when — kept OUT of the transcript on
    # purpose. The recorder below still gets the raw sentence, because
    # whatever it records becomes a message attributed to FRED, and a
    # "[Reminder]" prefix in there is both something FRED never said and
    # a format the model would start imitating aloud.
    #
    # This is the missing half instead: the transcript says WHAT was said,
    # this says it was unprompted and what kind. _build_messages renders
    # it as context for a short window, so a follow-up ("what was that?",
    # "how long till then?") has a handle on it. See last_proactive().
    global _last_proactive
    _last_proactive = {
        "kind": (title or "").strip(),
        "message": message,
        "at": time.time(),
    }

    if _recorder is not None:
        try:
            _recorder(message)
        except Exception as e:
            print(f"[NOTIFY ERROR] Recording to conversation state failed: {e}")
