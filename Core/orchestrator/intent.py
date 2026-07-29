# Core/orchestrator/intent.py
#
# Decides whether a turn should be offered tools at all.
#
# The problem this solves: the tool-calling loop passed all ~30 tools with
# tool_choice="auto" on every single turn. Nothing in that menu represents
# "just answer" — so a weak model has to beat 30 concrete alternatives with
# an implicit option that was never described to it. In practice it
# doesn't: "Hello Fred, how are you doing?" selected open_website and
# launched google.com.
#
# So conversation becomes an explicit route. If a turn is classified as
# conversation, the tool definitions are never shown to the model, and a
# misfire is then not merely unlikely but impossible.
#
# Three tiers, deliberately ordered cheapest and most certain first:
#
#   1. Social/meta phrasing  -> CHAT, decided here, never asks the model.
#      This is the class that actually broke, so it must not depend on
#      model judgement to get right.
#   2. An action cue present -> TOOLS. The cue vocabulary is bounded and
#      knowable because the tool list is (see ACTION_CUES).
#   3. Neither              -> ask the model, biased hard toward CHAT.
#
# The bias direction is the important design choice. Failing to offer a
# tool is a mild annoyance — FRED says something instead of doing it, and
# you rephrase. Wrongly firing one opens browsers, changes volume, moves
# files. Those costs are not symmetric, so every ambiguous case resolves
# to conversation.

import re

# Greeting/vocative prefixes stripped before classifying, so "Hey Fred,
# open google" is judged on "open google" and not on the "hey".
_PREFIX = re.compile(
    r"^(?:\s*(?:hey|hi|hello|yo|ok|okay|so|um+|uh+|please|fred+(?:ie)?|"
    r"good\s+(?:morning|afternoon|evening|night))\b[\s,.!]*)+",
    re.IGNORECASE,
)

# Utterances that are purely social or about FRED itself. Matched against
# the *whole* remaining text, so "how are you" is chat but "how loud is
# the volume" is not accidentally caught by a bare "how".
_SOCIAL = re.compile(
    r"^(?:"
    r"how(?:'s| is| are|s)?\s+(?:you|it going|things|your day|everything)\b.*"
    r"|what(?:'s| is)?\s*up\b.*"
    r"|(?:i'm |im )?(?:good|fine|great|ok|okay|alright)\b.*"
    r"|(?:thanks?|thank you|cheers|ta|nice|cool|awesome|great|perfect|lovely|sweet)\b.*"
    r"|(?:bye|goodbye|good\s?night|see ya|see you|later)\b.*"
    r"|(?:who|what)\s+(?:are|r)\s+you\b.*"
    r"|what(?:'s| is)?\s+your\s+name\b.*"
    r"|what\s+can\s+you\s+do\b.*"
    r"|tell me about (?:yourself|you)\b.*"
    r"|(?:are you|you) (?:there|awake|alive|online|ready|listening)\b.*"
    r"|(?:nothing|never ?mind|forget it|no)\b\.?$"
    r"|(?:yes|yeah|yep|no|nope|sure|maybe)\b\.?$"
    r")$",
    re.IGNORECASE,
)

# Vocabulary tied to the registered tools. Bounded because the tool list
# is: open_website, launch_application, create_text_file, create_folder,
# get_current_time, web_search, get_weather, the window group, volume,
# brightness, clipboard, screenshot, processes, the file group, and the
# scheduling group. Over-inclusive on purpose — a false TOOL here only
# means the model gets offered tools and can still decline, whereas a
# miss means it cannot act at all.
ACTION_CUES = (
    # web / apps
    "open", "launch", "start", "browse", "website", "url", ".com", ".org",
    "go to", "google", "youtube", "search", "look up",
    # time / weather
    "time", "date", "what day", "clock", "weather", "temperature",
    "forecast", "raining", "humidity",
    # files / folders
    "file", "folder", "directory", "note", "save", "create", "make",
    "write", "rename", "move", "delete", "remove", "read",
    # windows
    "window", "minimize", "minimise", "maximize", "maximise", "close",
    "switch to", "focus",
    # system
    "volume", "louder", "quieter", "loud", "quiet", "mute", "unmute",
    "sound", "brightness", "brighter", "dimmer", "dim", "screenshot",
    "screen shot", "capture", "clipboard", "copy", "paste",
    "process", "kill", "task manager", "cpu", "memory",
    # scheduling
    "remind", "reminder", "schedule", "alarm", "timer",
    "cancel", "tomorrow", "tonight", "wake me",
)

# Matched on word boundaries, not as bare substrings — "dim" must not fire
# on "dimension", nor "note" on "nothing". Cues containing punctuation
# (".com") or spaces ("go to") are escaped and matched as phrases.
_ACTION_RE = re.compile(
    r"(?:%s)" % "|".join(
        (r"\b%s\b" if c[0].isalnum() else r"%s") % re.escape(c)
        for c in ACTION_CUES
    ),
    re.IGNORECASE,
)

_CLASSIFIER_SYSTEM = (
    "You classify a user's message for a voice assistant. Answer with "
    "exactly one word: ACTION or CHAT.\n"
    "ACTION means the user is asking the assistant to DO something on the "
    "computer — open, launch, search the web, read or change files, adjust "
    "volume or brightness, take a screenshot, manage windows or processes, "
    "or set a reminder.\n"
    "CHAT means anything else: greetings, small talk, opinions, questions "
    "answerable from knowledge, or questions about the assistant itself.\n"
    "If it is not clearly an instruction to operate the computer, answer "
    "CHAT."
)


def normalise(text: str) -> str:
    text = (text or "").strip().lower()
    text = _PREFIX.sub("", text)
    return text.strip(" ,.!?")


def looks_social(text: str) -> bool:
    stripped = normalise(text)
    # Nothing left after removing the greeting — e.g. a bare "Hey Fred".
    if not stripped:
        return True
    return bool(_SOCIAL.match(stripped))


def has_action_cue(text: str) -> bool:
    return bool(_ACTION_RE.search(normalise(text)))


def classify(text: str, llm=None) -> tuple:
    """
    Returns (needs_tools: bool, reason: str). `reason` is returned rather
    than logged internally so the caller can print it — misroutes are the
    failure mode here, and they're impossible to debug without knowing
    which tier made the call.

    `llm` is optional; without it, tier 3 defaults to CHAT rather than
    guessing.
    """
    if not (text or "").strip():
        return False, "empty"

    if looks_social(text):
        return False, "social/meta phrasing"

    if has_action_cue(text):
        return True, "action cue present"

    if llm is None:
        return False, "no cue, no classifier — defaulting to chat"

    try:
        answer = llm.generate(
            [
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": text.strip()},
            ]
        )
    except Exception as e:
        return False, f"classifier failed ({e}) — defaulting to chat"

    # Only an explicit ACTION flips this. Anything else — CHAT, a refusal,
    # a rambling non-answer, empty output — lands on conversation, per the
    # asymmetry described at the top of this module.
    head = (answer or "").strip().upper()[:40]
    if "ACTION" in head and "CHAT" not in head:
        return True, "classifier said ACTION"
    return False, f"classifier said {head.split() or ['(nothing)']}"[:60]
