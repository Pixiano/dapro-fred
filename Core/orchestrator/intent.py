# Core/orchestrator/intent.py
#
# Decides two things before the model ever sees a tool:
#   1. whether this turn needs tools at all
#   2. if so, which small subset of them
#
# The problem being solved is tool-choice accuracy on a small model. With
# 40 tools passed on every turn and tool_choice="auto", the model has to
# pick one option out of forty — and nothing in that list means "just
# reply", so a greeting competes against forty concrete actions and loses.
# It did: "Hello Fred, how are you doing?" selected open_website and
# opened google.com.
#
# Two layers fix it:
#
#   CHAT vs TOOLS   — conversation never sees tool definitions, so a
#                     misfire there is impossible rather than unlikely.
#   CATEGORY SUBSET — an action turn sees only the category it matched,
#                     typically 2-6 tools instead of 40. Choosing between
#                     "set_volume / get_volume / mute / media_control" is
#                     a task a 4B can do reliably; choosing among forty
#                     is not.
#
# Categories are matched by cue words rather than by asking the model,
# because a deterministic router cannot itself be confused — and the whole
# point is to remove a judgement call the small model is bad at. Multiple
# categories can match at once and are unioned, so "turn the volume down
# and open Spotify" still gets both sets.

import re

# Greeting/vocative prefixes stripped before classifying, so "Hey Fred,
# open google" is judged on "open google" and not on the "hey".
_PREFIX = re.compile(
    r"^(?:\s*(?:hey|hi|hello|yo|ok|okay|so|um+|uh+|please|fred+(?:ie)?|"
    r"good\s+(?:morning|afternoon|evening|night))\b[\s,.!]*)+",
    re.IGNORECASE,
)

# Utterances that are purely social or about FRED itself. Matched against
# the whole remaining text, so "how are you" is chat while "how loud is
# the volume" isn't caught by a bare "how".
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

# =========================================================
# CATEGORIES
# =========================================================
#
# Kept small and single-purpose so a matched category is a genuinely
# short menu. A tool may appear in more than one category when the
# request phrasing could reasonably land in either.

TOOL_CATEGORIES = {
    "time": ("get_current_time",),
    "weather": ("get_weather",),
    "math": ("calculate",),
    "search": ("web_search",),
    "sysinfo": ("get_system_status", "get_network_status"),
    "apps": ("launch_application", "open_website", "open_path"),
    "audio": ("get_volume", "set_volume", "adjust_volume", "mute", "media_control"),
    "display": ("get_brightness", "set_brightness", "adjust_brightness", "take_screenshot"),
    "clipboard": ("get_clipboard", "set_clipboard"),
    "windows": (
        "list_windows", "focus_window", "minimize_window",
        "maximize_window", "close_window",
    ),
    "processes": ("list_processes", "kill_process"),
    "power": ("power_action",),
    "files": (
        "create_text_file", "create_folder", "append_to_file", "read_file",
        "list_directory", "search_files", "find_file_smart", "move_file",
        "rename_file", "delete_file", "open_path", "open_last_found",
    ),
    "schedule": (
        "schedule_reminder", "set_timer", "schedule_file_watch",
        "list_scheduled", "cancel_scheduled",
    ),
    "git": ("git_status", "git_log", "git_diff_summary"),
    "recap": ("summarise_today", "save_today_summary"),
}

# Cue words per category. Over-inclusive on purpose: a spurious category
# only widens the menu slightly, whereas a miss means the tool cannot be
# reached at all.
CATEGORY_CUES = {
    "time": (
        "time", "date", "what day", "clock", "today's date", "day is it",
    ),
    "weather": (
        "weather", "temperature", "forecast", "raining", "rain", "humidity",
        "hot outside", "cold outside", "sunny",
    ),
    # No bare "what is"/"what's" here — they appear in almost every
    # question ("what is my volume") and pulled maths into unrelated
    # turns. Only genuinely arithmetic wording qualifies.
    "math": (
        "calculate", "how much is", "plus", "minus", "multiplied",
        "divided", "percent of", "% of", "square root", "sqrt",
        "sum of", "add up", "work out", "times",
    ),
    "search": (
        "search", "look up", "google", "who is", "what happened",
        "news", "find out", "search for", "on the web",
    ),
    "sysinfo": (
        "battery", "charge", "cpu", "ram", "memory", "disk", "storage",
        "space left", "uptime", "how fast", "wifi", "wi-fi", "internet",
        "online", "network", "connected", "ip address",
    ),
    "apps": (
        "open", "launch", "start", "run", "website", "url", ".com", ".org",
        "go to", "youtube", "spotify", "chrome", "browser",
    ),
    "audio": (
        "volume", "louder", "quieter", "loud", "quiet", "mute", "unmute",
        "sound", "audio", "play", "pause", "resume", "skip", "next track",
        "previous", "song", "music",
        # NOT a bare "track" — "on track", "keep track", "track record"
        # are common enough English idioms that it false-positived
        # "Am I on track with my bulk?" (a fitness/vault question) into
        # the audio category, which mis-routed it to get_volume. "next
        # track" above already covers the real skip-song phrasing, so
        # the bare form was pure liability with no coverage it alone
        # provided.
    ),
    "display": (
        "brightness", "brighter", "dimmer", "dim", "screen",
        "screenshot", "screen shot", "capture", "grab the screen",
    ),
    # Not bare "copy"/"copied" — confirmed false-positive on "project
    # copy" / "project copy.md" (a project name), reproduced directly in
    # session_2026-08-01_14-24-11.jsonl: "did we do last in project
    # copy?" had no other category cue at all, so clipboard was the ONLY
    # category offered and get_clipboard fired on a project name. Same
    # shape as the "on track" collision above — "copy" is too common an
    # ordinary noun/verb to stand alone as a cue. Real clipboard requests
    # reliably say "clipboard" itself, or pair copy/paste with a
    # demonstrative ("copy that", "copy this").
    "clipboard": (
        "clipboard", "paste", "pasted",
        "copy that", "copy this", "copied that", "copied this",
    ),
    # "windows" is listed explicitly: cues match on word boundaries, so
    # \bwindow\b never matched the plural, and "list my open windows"
    # routed to apps+files instead — the model then denied being able to
    # do it at all, because the tool wasn't in its menu.
    "windows": (
        "window", "windows", "minimize", "minimise", "maximize", "maximise",
        "switch to", "focus", "bring up", "close the", "alt tab",
    ),
    "processes": (
        "process", "task manager", "kill", "not responding", "frozen",
        "running apps", "end task",
    ),
    "power": (
        "lock", "sleep", "shut down", "shutdown", "restart", "reboot",
        "power off", "hibernate", "log off",
    ),
    "files": (
        "file", "folder", "directory", "note", "save", "create", "make",
        "write", "rename", "move", "delete", "remove", "read", "append",
        "add to", "list", "what's in", "document", "shopping list", "find",
        # "find" added after a confirmed misroute: "find spotify.exe"
        # matched only "apps" (via the "spotify" cue), so search_files/
        # find_file_smart were never offered at all — the model had no
        # way to do what was actually asked. Over-inclusive on purpose,
        # same as every other cue here: this unions with whatever else
        # matches rather than replacing it, so "find spotify.exe" now
        # offers both the file-search tools AND launch_application,
        # letting the model pick correctly instead of only seeing one.
    ),
    "schedule": (
        "remind", "reminder", "schedule", "alarm", "timer", "countdown",
        "wake me", "tomorrow", "tonight", "cancel", "pending", "in an hour",
        "at 7", "later",
    ),
    "git": (
        "git", "commit", "commits", "branch", "repo", "repository",
        "uncommitted", "pushed", "pull request",
    ),
    "recap": (
        "what did we do", "what have we done", "recap", "summarise today",
        "summarize today", "today's summary", "wrap up", "sum up today",
        "log today", "save today",
    ),
}


# Minimum cosine similarity for the embedder alone to declare a turn an
# action. Calibrated, not guessed: over 13 cue-free action paraphrases and
# 8 plain-chat utterances, chat topped out at 0.601 ("tell me a joke"
# against web_search) while confident actions ran 0.66-0.85. So 0.65 sits
# in the gap — it catches the strong paraphrases keywords miss entirely and
# rejects every chat sample.
#
# The mean scores overlap badly (actions 0.677, chat 0.502, and the worst
# action scored 0.444 — below the best chat). That is why this is a high
# bar for *adding* actions rather than a general action/chat divider:
# embeddings are good at which tool, unreliable at whether. Anything below
# this still falls through to the LLM check, exactly as before, so this can
# only add correct routes, never take one away.
SEMANTIC_FLOOR = 0.65


def _build_cue_regex(cues):
    """Word-boundary match for alphanumeric cues, literal for the rest —
    so "dim" doesn't fire on "dimension" and ".com" still matches."""
    return re.compile(
        "(?:%s)" % "|".join(
            (r"\b%s\b" if c[0].isalnum() else r"%s") % re.escape(c)
            for c in cues
        ),
        re.IGNORECASE,
    )


_CATEGORY_RE = {name: _build_cue_regex(cues) for name, cues in CATEGORY_CUES.items()}

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
    if not stripped:
        return True  # nothing left after the greeting, e.g. a bare "Hey Fred"
    return bool(_SOCIAL.match(stripped))


def match_categories(text: str) -> list:
    """Every category whose cues appear, in declaration order."""
    stripped = normalise(text)
    return [name for name, rx in _CATEGORY_RE.items() if rx.search(stripped)]


def tools_for_categories(categories) -> list:
    """Flatten categories to a de-duplicated tool-name list."""
    names = []
    for category in categories:
        for tool in TOOL_CATEGORIES.get(category, ()):
            if tool not in names:
                names.append(tool)
    return names


def classify(text: str, llm=None, router=None) -> tuple:
    """
    Returns (needs_tools, tool_names, reason).

    tool_names is the subset to offer the model; an empty list alongside
    needs_tools=True means "no category matched, offer everything", which
    is the safe fallback rather than blocking a real request.

    `reason` is returned rather than logged internally so the caller can
    print it — a misroute is the failure mode here and is near-impossible
    to debug without knowing which layer decided.
    """
    if not (text or "").strip():
        return False, [], "empty"

    if looks_social(text):
        return False, [], "social/meta phrasing"

    categories = match_categories(text)

    if categories:
        names = tools_for_categories(categories)

        # Semantic ranking narrows a broad cue hit. "files" alone is ten
        # tools; the embedder usually knows which two or three are meant,
        # and intersecting keeps the cue result as a safety bound so a bad
        # embedding can't pull in something unrelated.
        if router is not None:
            ranked_all = [n for n, _ in router.rank(text)]
            narrowed = [n for n in ranked_all if n in names]

            # Never narrow below two candidates. Measured: "what's using
            # all my cpu" narrowed cleanly to a single tool — the wrong
            # one — where the cue set had contained the right one. Keeping
            # a runner-up lets the model correct a near-miss from context,
            # and two options is still trivial for a small model against
            # the forty it started with.
            narrowed = narrowed[: max(2, min(6, len(narrowed)))]

            # Rescue a cue-coverage gap. "What's using all my cpu" matched
            # only the sysinfo category, so list_processes — the right
            # answer — was never a candidate at all. If the embedder is
            # confident about a tool the cues didn't reach, add it rather
            # than let a missing keyword decide the turn.
            rescued = []
            if ranked_all:
                top, best_score = router.route(text, top_k=1)
                if top and best_score >= SEMANTIC_FLOOR and top[0] not in names:
                    rescued = [top[0]]

            candidates = rescued + (narrowed or names)

            if len(candidates) < len(names) or rescued:
                best = router.route(text, top_k=1)[1]
                note = "narrowed" if not rescued else "narrowed+rescued"
                return (
                    True,
                    candidates,
                    f"cues {'+'.join(categories)} {note} by embedding "
                    f"{len(names)}->{len(candidates)} (sim {best:.2f})",
                )

        return True, names, f"cues {'+'.join(categories)} -> {len(names)} tools"

    # No cue matched. This is where keyword routing used to give up and
    # hand a 4B the ACTION/CHAT question it answers badly. Ask the
    # embedder instead: if some tool is clearly close to what was said,
    # this is an action and we already know which tools to offer.
    if router is not None:
        ranked, best = router.route(text, top_k=5, floor=SEMANTIC_FLOOR, margin=0.08)
        if ranked:
            return True, ranked, f"embedding {best:.2f} -> {len(ranked)} tools"

    if llm is None:
        return False, [], "no cue, no classifier — defaulting to chat"

    try:
        answer = llm.generate(
            [
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {"role": "user", "content": text.strip()},
            ]
        )
    except Exception as e:
        return False, [], f"classifier failed ({e}) — defaulting to chat"

    # Only an explicit ACTION flips this. Anything else — CHAT, a refusal,
    # a rambling non-answer, empty output — lands on conversation. Failing
    # to offer a tool is a mild annoyance; wrongly firing one opens
    # browsers, deletes files and changes volume. Not symmetric.
    head = (answer or "").strip().upper()[:40]
    if "ACTION" in head and "CHAT" not in head:
        return True, [], "classifier said ACTION (no category — all tools)"

    words = head.split() or ["(nothing)"]
    return False, [], f"classifier said {words[0]}"
