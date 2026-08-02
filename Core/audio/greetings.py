# Core/audio/greetings.py
#
# What FRED says once, on its own, when it comes up.
#
# Every line addresses the user as "sir" — that's the house style, not a
# stylistic accident, and the whole point of the greeting is that it
# sounds like the same assistant every time.
#
# No line is phrased as a question. A greeting that asks something and
# then falls silent reads as though FRED is waiting for an answer it
# isn't actually listening for — the hotkey hasn't been touched, so
# nothing is recording.
#
# Pre-synthesised into the phrase cache alongside filler (see
# pill_app._warm_phrase_cache), so the greeting plays instantly rather
# than making Kokoro generate it live at start-up.

import random
import time

NEUTRAL = (
    "Good to see you again, sir. All systems online.",
    "Welcome back, sir. Everything is exactly where you left it.",
    "Online and listening, sir.",
    "At your service, sir. Standing by.",
    "Systems nominal, sir. Ready when you are.",
    "Powered up, sir. The reactor is warm.",
    "Ready and waiting, sir. Say the word.",
)

# Keyed by the first hour of each band; resolved by _band() below.
BY_TIME = {
    "morning":   "Good morning, sir. Systems are warm and ready.",
    "afternoon": "Good afternoon, sir. All systems online.",
    "evening":   "Good evening, sir. Standing by.",
    "night":     "Late one tonight, sir. Everything is online.",
}

ALL_GREETINGS = NEUTRAL + tuple(BY_TIME.values())


def _band(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def pick_greeting(now=None) -> str:
    """
    A neutral line, or the one that matches the current time of day.

    The time-of-day line is only ever one candidate among the neutrals
    rather than always winning, so restarting twice in an afternoon
    doesn't produce the identical sentence both times.
    """
    try:
        hour = (now or time.localtime()).tm_hour
        return random.choice(NEUTRAL + (BY_TIME[_band(hour)],))
    except Exception:
        return random.choice(NEUTRAL)
