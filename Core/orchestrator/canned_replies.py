# Core/orchestrator/canned_replies.py
#
# Instant, no-model replies for the handful of things you say after
# almost every FRED turn or action — "thanks", "got it", "never mind" —
# where the real reply is always going to be one of a small set of
# essentially interchangeable sentences. Running the full pipeline for
# these (memory retrieval, vault retrieval, a reasoning block, TTS) is
# pure waste when a coin flip over five fixed sentences reads exactly
# the same to you.
#
# Deliberately whole-utterance matching, not cue matching — contrast
# vault_intent.py / orchestrator/intent.py, which fire on a cue
# *appearing* in a longer sentence. "Thanks, but can you also open
# Chrome" must NOT get canned-replied; there's a real request riding
# along with the thanks. This only fires when the entire turn, once
# normalised, IS one of the trigger phrases and nothing else.
#
# Reviewed phrase-by-phrase with the user before being written (10
# categories, each judged for plausibility) rather than guessed.

import random
import re

from orchestrator import intent

_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalize(text: str) -> str:
    """Lowercase, strip a leading vocative ("hey", "hi", "Fred", "good
    morning", ...), drop punctuation, drop any remaining mention of
    "Fred" — so "Hey, Fred. How are you doing?" and "how are you doing"
    hit the same trigger key.

    Reuses intent.normalise() (the same leading-prefix strip already
    proven for tool routing) rather than a second hand-rolled version —
    a bare "\\bfred\\b" strip alone missed vocatives like "hey"/"hi" in
    front of it, which is exactly why real speech ("Hey, Fred...") was
    failing to match trigger phrases written without them.

    intent.normalise() strips "hi"/"hello"/"okay"/"good morning" etc. as
    a *prefix*, which collapses them to "" when one of those words is
    the entire message rather than a vocative in front of one — and
    several of this module's own triggers ("hi", "okay", "good morning")
    are exactly that. Falling back to the un-stripped text whenever
    stripping would erase the message keeps "hi" and "okay" distinct
    instead of colliding on the same empty key.
    """
    raw = (text or "").strip().lower()
    stripped = intent.normalise(raw)
    t = stripped if stripped else raw
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"\bfred\b", "", t)
    return re.sub(r"\s+", " ", t).strip()


# (category, trigger phrases, reply pool). Triggers don't overlap across
# categories by construction, so lookup is a flat dict, not a priority list.
CATEGORIES = [
    ("thanks", (
        "thanks", "thank you", "thank you very much", "good job",
    ), (
        "You're welcome, sir.", "Anytime, sir.", "Happy to help.",
        "Of course, sir.", "My pleasure.", "Glad I could help, sir.",
        "Good to hear from you, sir.", "Always happy to help, sir.",
    )),
    ("greeting", (
        "hey fred", "hello", "hi", "hi fred", "hey",
        "good morning", "good afternoon", "good evening", "yo fred",
    ), (
        "Hello, sir.", "Hey there.", "At your service, sir.",
        "Good to hear from you, sir.", "Hey, what can I do for you, sir?",
        "Good morning to you too.", "Always glad to hear from you, sir.",
        "Good to see you, sir.",
    )),
    ("farewell", (
        "bye", "goodbye", "good night", "talk to you later",
        "see you later", "catch you later", "gotta go", "heading out",
    ), (
        "Goodbye, sir.", "Talk soon, sir.", "See you later.",
        "Good night, sir.", "Take care, sir.", "Catch you later.",
        "Rest well, sir.", "Until next time.", "Take it easy, sir.",
    )),
    ("acknowledgment", (
        "okay", "ok", "got it", "cool", "makes sense", "alright",
        "sounds good", "fair enough", "gotcha",
    ), (
        "Understood, sir.", "Noted.", "Glad that helps, sir.",
        "Alright, sir.", "Good to know.", "Perfect, noted, sir.",
        "Sounds good to me.", "Fair enough, sir.", "Got it, sir.",
    )),
    ("apology", (
        "sorry", "my bad", "oops", "my mistake", "apologies",
    ), (
        "No worries at all, sir.", "It happens.", "All good, sir.",
        "Not a problem, sir.", "Don't worry about it.",
        "Think nothing of it, sir.",
    )),
    ("compliment", (
        "you're awesome", "you're the best", "you're amazing",
        "nice work, fred", "you're incredible", "i love you fred",
        "you're a lifesaver", "well done, fred", "you're brilliant",
    ), (
        "Glad to be of service, sir.", "Just doing my job.",
        "I appreciate that, sir.", "That's kind of you to say, sir.",
        "You're too kind, sir.", "Happy to help.",
        "You flatter me, sir.",
    )),
    ("presence_check", (
        "are you there", "can you hear me", "you awake",
        "hello, anyone there",
    ), (
        "Right here, sir.", "Yes, I'm here, sir.",
        "Always listening when you're holding the key.",
        "Right here and listening, sir.",
    )),
    ("cancel", (
        "never mind", "forget it", "scratch that", "nvm", "doesn't matter",
    ), (
        "Understood, cancelled, sir.", "No problem, dropping that.",
        "Alright, never mind then, sir.", "Consider it dropped, sir.",
    )),
    ("confirm_result", (
        "perfect", "that's exactly right", "that worked", "exactly",
        "that's it", "spot on", "nailed it", "that's correct",
        "yes, that's right",
    ), (
        "Glad that's right, sir.", "Perfect.", "Great, glad it worked, sir.",
        "Excellent, sir.", "Good to hear.",
        "That's what I was aiming for, sir.", "Exactly as planned, sir.",
    )),
    ("checkin", (
        "how are you", "how's it going", "how are you doing",
        "how's your day", "you doing okay",
    ), (
        "Functioning perfectly, sir.", "All systems normal, sir.",
        "Can't complain.", "Running smoothly, sir.",
        "Doing well, thank you for asking.", "Steady as ever, sir.",
    )),
]

# Built once at import: normalised trigger -> (category, reply pool).
_LOOKUP = {}
for _name, _triggers, _replies in CATEGORIES:
    for _t in _triggers:
        _LOOKUP[_normalize(_t)] = (_name, _replies)


def match(user_input: str) -> str | None:
    """
    Returns a random canned reply if `user_input`, once normalised, IS
    one of the fixed trigger phrases in full — not merely contains one.
    Returns None otherwise, so the caller falls through to the real
    pipeline (dispatcher / LLM) completely unchanged.
    """
    hit = _LOOKUP.get(_normalize(user_input))
    if not hit:
        return None
    category, replies = hit
    reply = random.choice(replies)
    print(f"[canned_replies] '{user_input}' -> {category}")
    return reply
