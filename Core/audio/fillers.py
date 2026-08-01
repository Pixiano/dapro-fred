# Core/audio/fillers.py
#
# Filler phrase pools, moved out of ui/pill_app.py so audio/phrase_cache.py
# can pre-synthesise every phrase in them without importing the UI layer.
# Picking logic lives here too — it's audio behaviour, not UI behaviour.

import random

from orchestrator import intent

# Spoken immediately on every turn, before the real reply is even fully
# generated. Masks time-to-first-word: gemma4/Qwen spend real time on
# reasoning before anything is streamable, and this gives the user audio
# to listen to during that gap instead of silence. Real generation starts
# on a background thread the instant the filler starts playing, so the
# two overlap rather than stack.
#
# Picked per turn by a cue check, not a fixed pool — "let me have a
# look" answering "how are you doing" reads as broken, so what kind of
# turn this looks like (social small talk vs. an action vs. everything
# else) picks which flavour of filler is even eligible. Same
# cheap-check-before-anything-expensive shape as orchestrator/intent.py
# and vault_intent.py: word cues, no model call.
FILLER_SOCIAL = (
    "One moment.",
    "Just a second.",
    "Give me a moment.",
    "One sec.",
)

FILLER_ACTION = (
    "On it.",
    "Let me check on that.",
    "Working on it now.",
    "Let me have a look.",
    "Give me one second.",
)

FILLER_DEFAULT = (
    "Let me think about that.",
    "Give me a second.",
    "Hold on, thinking it through.",
    "Let's see here.",
    "Just a moment.",
)

ALL_FILLERS = FILLER_SOCIAL + FILLER_ACTION + FILLER_DEFAULT


def pick_filler(text: str) -> str:
    """Social turns (greetings, "how are you", thanks/bye — see
    intent.looks_social) get neutral filler with no task language.
    Turns matching a tool category (intent.match_categories — "open X",
    "what's the volume") get task-flavoured filler. Everything else
    (real questions, general chat) gets the thinking-flavoured default.
    Falls through to FILLER_DEFAULT on any classification hiccup rather
    than block the turn on it."""
    try:
        if intent.looks_social(text):
            return random.choice(FILLER_SOCIAL)
        if intent.match_categories(text):
            return random.choice(FILLER_ACTION)
    except Exception:
        pass
    return random.choice(FILLER_DEFAULT)
