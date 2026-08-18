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
    "apps": ("launch_application", "open_website", "open_path", "open_vault_file"),
    "audio": ("get_volume", "set_volume", "adjust_volume", "mute", "media_control"),
    "devices": ("list_audio_devices", "set_input_device", "set_output_device"),
    "self_restart": ("restart_fred",),
    "lockdown": ("lockdown_engage", "lockdown_disengage"),
    "display": ("get_brightness", "set_brightness", "adjust_brightness", "take_screenshot"),
    "clipboard": ("get_clipboard", "set_clipboard"),
    "windows": (
        "list_windows", "focus_window", "minimize_window",
        "maximize_window", "close_window",
    ),
    "processes": ("list_processes", "kill_process"),
    "power": ("power_action", "end_of_day"),
    "files": (
        "create_text_file", "create_folder", "append_to_file", "read_file",
        "list_directory", "search_files", "find_file_smart", "move_file",
        "rename_file", "delete_file", "open_path", "open_last_found",
        "open_vault_file",
    ),
    "schedule": (
        "schedule_reminder", "schedule_recurring", "set_timer",
        "schedule_file_watch", "list_scheduled", "cancel_scheduled",
    ),
    "workout": ("workout_split", "todays_workout", "schedule_workouts"),
    "git": ("git_status", "git_log", "git_diff_summary"),
    "phone": ("call_phone", "hang_up", "sync_contacts", "use_phone"),
    # Reading and sending are SEPARATE categories on purpose, and this is
    # the one place in this file where the usual "over-inclusive is cheap"
    # rule is wrong. read_messages pulls in text written by other people —
    # attacker-controlled input, arriving at an agent that holds tools. If
    # a turn can both read a stranger's message and send one, a message
    # saying "reply to everyone with X" is one hop from doing it. Keeping
    # them in different categories means the model is never holding both
    # capabilities at once. Structural, not a prompt instruction.
    "messages_read": ("read_messages", "list_contact_tiers"),
    "messages_send": ("send_message", "set_contact_tier"),
    # Questions about FRED himself. describe_self is listed here too:
    # it was registered with no category at all, so until now it was
    # only ever reachable by the embedder's rescue path. The two answer
    # different halves of "what are you" — live state vs. the docs — and
    # a question about FRED can want either, so both are offered and the
    # model picks from their descriptions.
    "selfdoc": ("ask_about_myself", "describe_self"),
    "recap": ("summarise_today", "save_today_summary"),
    # repeat_last is grouped WITH recall_recent_conversation, not given
    # its own category — "what did you say" is genuinely ambiguous
    # between "repeat your last line" and "what was said a minute ago",
    # so both tools are offered and the model's descriptions disambiguate.
    "recall_recent": ("recall_recent_conversation", "repeat_last"),
    "vision": ("whats_on_screen",),
    "tasks": ("add_task", "list_tasks", "complete_task"),
    "agenda": (
        "add_agenda_item", "list_agenda_items", "update_agenda_item",
        "delete_agenda_item",
    ),
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
    # Bare "find" added after a confirmed misroute (session_2026-08-12.jsonl,
    # 14:13-14:14): "Find turfs to play football near Malaad West." matched
    # only the "files" category (bare "find" was already a files cue, added
    # for "find spotify.exe"), so web_search was never in the offered menu
    # at all — the model had only find_file_smart/search_files to work
    # with, called find_file_smart twice against a location query with no
    # matching file, both failed, and the turn ended with no useful answer.
    # Same "over-inclusive is cheap, missing is fatal" reasoning as every
    # other cue here: this unions with "files" rather than replacing it, so
    # "find spotify.exe" now offers web_search too alongside the file/app
    # tools, and the model picks correctly from the full set instead of
    # never seeing the right one.
    "search": (
        "search", "look up", "google", "who is", "what happened",
        "news", "find out", "search for", "on the web", "find",
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
    "lockdown": (
        "lockdown", "lock down", "lockdown protocol", "engage lockdown",
        "unlock", "lift lockdown", "stand down",
    ),
    "devices": (
        "microphone", "mic", "speaker", "audio device", "input device",
        "output device",
    ),
    # Multi-word phrases only, so this never fires on the bare
    # "restart"/"reboot" that already routes to power_action (PC restart).
    "self_restart": (
        "restart yourself", "restart fred", "reboot yourself",
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
        "end of day", "wind down", "done for today", "goodnight",
        "good night", "call it a day", "wrap up for the day",
    ),
    "files": (
        "file", "folder", "directory", "note", "save", "create", "make",
        "write", "rename", "move", "delete", "remove", "read", "append",
        "add to", "list", "what's in", "document", "shopping list", "find",
        "vault", "priorities", "priority",
        # open_last_found, convert_file, print_file, reindex_drive,
        # search_index had no cues at all — reachable only via the
        # semantic-embedding fallback. Added 2026-08-17 per review.
        "open it", "open that one", "the last one", "convert", "print",
        "reindex", "file index",
        # "find" added after a confirmed misroute: "find spotify.exe"
        # matched only "apps" (via the "spotify" cue), so search_files/
        # find_file_smart were never offered at all — the model had no
        # way to do what was actually asked. Over-inclusive on purpose,
        # same as every other cue here: this unions with whatever else
        # matches rather than replacing it, so "find spotify.exe" now
        # offers both the file-search tools AND launch_application,
        # letting the model pick correctly instead of only seeing one.
    ),
    # Plurals added explicitly (not stemmed) alongside their singulars,
    # same convention as "windows" below. Confirmed bug
    # (session_2026-08-03.jsonl, tool_call_log.jsonl): "Set two
    # reminders, on Wednesday and Friday..." matched NEITHER "remind"
    # nor "reminder" — cue matching is \b-anchored on both sides
    # (_build_cue_regex), and "reminders" has no word boundary after
    # "remind"/"reminder", only after the trailing "s". With no
    # category matched, classify() fell all the way through to the LLM
    # ACTION/CHAT classifier, which returns tool_names=[] — and
    # get_tool_definitions(only=[]) treats empty as "no filter", so
    # EVERY registered tool's full schema (~40) was sent on every round
    # of an already multi-round tool-calling turn. That's what
    # eventually 413'd Groq and, on the same bloated messages/tools
    # payload, plausibly overflowed the local tier's context window too
    # (task_faf27f8d) — one missed plural took out both the cloud and
    # local fallback paths for this turn.
    "schedule": (
        "remind", "reminder", "reminders", "schedule", "alarm", "alarms",
        "timer", "timers", "countdown", "wake me", "tomorrow", "tonight",
        "cancel", "pending", "in an hour", "at 7", "later",
        # Recurrence words, so "remind me every weekday" reaches
        # schedule_recurring rather than only the one-shot tools.
        "every", "each", "daily", "weekly", "weekdays", "weekends",
        "recurring", "repeating",
        # schedule_file_watch had no cue of its own beyond generic
        # "schedule" wording. Added 2026-08-17 per review.
        "watch for", "let me know when",
        # cancel_scheduled already accepts identifier="all" but had no
        # phrasing that reaches it as a "clear everything" request.
        "clear my day", "clear my schedule",
    ),
    "workout": (
        "workout", "workouts", "training", "train", "gym", "split",
        "exercise", "rest day", "muscles", "lifting", "session",
    ),
    "git": (
        "git", "commit", "commits", "branch", "repo", "repository",
        "uncommitted", "pushed", "pull request",
    ),
    # "call" alone is ambiguous ("call it a day", "recall") but the cue
    # only widens the menu, and a miss means FRED can't dial at all.
    "phone": (
        "call", "dial", "phone", "ring", "hang up", "end the call",
        "hangup", "cut the call", "contact", "contacts",
        "which phone", "use my", "switch phone",
    ),
    # Cues for these two are kept as disjoint as phrasing allows — see the
    # comment on messages_read/messages_send above. If both fire they
    # union into one menu and the isolation is gone, so read is written
    # question-shaped and plural, send verb-led and singular.
    # PLURAL only. "any message" was here and had to go: bare "message"
    # is the natural way to ASK for a send ("message Mom saying hello"),
    # and a phrase matching both categories unions them into one menu,
    # which is exactly the isolation this pair exists to keep.
    "messages_read": (
        "messages", "new messages", "unread",
        "who messaged", "who texted", "check whatsapp", "read whatsapp",
        "my whatsapp", "what did", "anything from",
    ),
    # SINGULAR "message" as a verb. Its absence is why "Message Mom saying
    # hello" reached the model with no messaging tool offered at all, and
    # FRED narrated sending instead of sending (2026-08-16, 23:41).
    # "whatsapp" stays out deliberately - it appears in the read cues, and
    # adding it here would make "check whatsapp" match both.
    "messages_send": (
        "message", "msg", "send a message", "send message",
        "message to", "text to", "text him", "text her", "text them",
        "reply to", "reply saying", "tell him", "tell her", "tell them",
        "whatsapp him", "whatsapp her", "whatsapp them",
        "trust", "untrust", "vip", "mark as",
    ),
    # Deliberately NOT "who are you" / "what's your name" / "how are
    # you": those are persona questions, answered from persona.md in the
    # system prompt, and pulling docs in would make a greeting cost a
    # retrieval. What belongs here is the shape "does FRED have X / how
    # does FRED's X work / why is FRED built that way" — the questions
    # backlog #13 exists for, where the failure mode is the model
    # inventing an answer from conversation context.
    "selfdoc": (
        "what can you do", "what else can you do", "your capabilities",
        "capable of", "your tools", "do you have a", "do you have any",
        "can you actually", "your features", "your documentation",
        "your docs", "readme", "your roadmap", "your setup",
        "how do you work", "how were you built", "how are you built",
        "why were you", "why are you built", "why was that built",
        "about yourself", "your own code", "your source code",
        "your phases", "your plan", "what are you made of",
        # "how does the phone thing work" is one of backlog #13's own
        # example questions and says nothing self-referential at all —
        # "phone" alone routes it to call_phone, which dials someone
        # instead of explaining anything. Cued on the generic "how does
        # the ..." shape rather than enumerating features, accepting
        # that it also fires on things like "how does the weather look":
        # a spurious match only adds two read-only tools to that turn's
        # menu, which is this file's stated tradeoff everywhere else.
        "how does the", "how does your", "how do your",
    ),
    # "session log" / "daily log" / "daily note" added 2026-08-17. Asked
    # "Is today's session log maintained?", the router matched no category
    # at all, offered workout tools via the semantic rescue path, and FRED
    # answered the filesystem question from nothing — twice, insisting it
    # had checked. It had no way to check, so it guessed.
    "recap": (
        "what did we do", "what have we done", "recap", "summarise today",
        "summarize today", "today's summary", "wrap up", "sum up today",
        "log today", "save today", "session log", "daily log", "daily note",
        "today's log", "log for today", "logged today",
    ),
    "recall_recent": (
        "what did we just talk about", "what did i just say",
        "what did you just say", "what did i say", "what did you say",
        "a minute ago", "just now", "just said", "repeat what we said",
        "our conversation", "what were we talking about",
        # repeat_last cues (see TOOL_CATEGORIES comment on this category).
        "say that again", "repeat that",
    ),
    # Confirmed miss, live 2026-08-13: "Can you look AT my screen..."
    # matched only "display" (bare "screen") — none of the specific
    # phrases below require "on", not "at"/"check"/"see"/"view", so
    # whats_on_screen was never offered, only take_screenshot (saves a
    # file, can't describe anything). Model correctly said it couldn't
    # see the screen, given the only tool it had access to genuinely
    # can't. Same shape as the "windows"/"find" cue-coverage misses
    # elsewhere in this file — added bare "screen" here too rather than
    # trying to enumerate every preposition, so vision is reachable any
    # time display is (over-inclusive on purpose, per this file's own
    # stated philosophy above): the two tools' good descriptions (see
    # orchestrator.py's take_screenshot/whats_on_screen registration)
    # are what should disambiguate save-a-file vs. describe-what's-on-it
    # from here, not a keyword pre-filter deciding it before the model
    # ever sees the option.
    # Second confirmed miss, same day: "tell me what exactly I'm looking
    # at" has no "screen" at all and doesn't contain the exact phrase
    # "what am i looking at" (word order/contraction differ), so it
    # matched nothing — not even the bare-"screen" fix above helps when
    # the utterance never says "screen". Cued on the stable 2-word core
    # ("looking at") instead of the one full sentence it was wrapped in,
    # same reasoning as dropping "what am i looking at" would suggest:
    # a whole-phrase cue only ever covers itself.
    "vision": (
        "what's on my screen", "on my screen",
        "what's on screen", "screen say", "what does my screen", "screen",
        "looking at",
    ),
    "tasks": (
        # Bare "to do" is deliberately left out — "things to do", "what
        # should I do" are common phrasing with nothing to do with the
        # task list, and would widen the menu on nearly every turn.
        "task", "tasks", "to-do", "to-do list", "to do list", "todo",
        "checklist", "today's tasks", "my tasks", "mark as done",
        # "shopping list" was a FILES cue only, so it never offered
        # add_task at all — confirmed root cause (review, 2026-08-17) of
        # to-do items landing in create_text_file/append_to_file instead.
        # "remind me to"/"need to"/"add to my list" widen tasks-vs-files
        # coverage the same way; over-inclusive on purpose, same
        # reasoning as every other cue in this file.
        "shopping list", "remind me to", "need to", "add to my list",
        "mark as complete", "mark complete", "mark incomplete", "mark done",
    ),
    # Homework/project/event tracking (add_agenda_item etc.) — school
    # words AND general-plan words both belong here, since "event" was
    # always meant to cover a movie or meeting friends alongside
    # homework, not homework alone (the whole subsystem was briefly
    # named/cued as if it were school-only; fixed 2026-08-09 the same
    # day a movie got logged through a tool called add_school_item).
    #
    # Not bare "questions" — "I have a question" / "any questions" is
    # common phrasing unrelated to homework, and would widen the menu on
    # nearly every turn, same reasoning as "tasks" above excluding bare
    # "to do". "questions in" is specific to the actual shape ("3
    # questions in Geography") without that cost.
    "agenda": (
        "homework", "assignment", "assignments", "school", "due",
        "deadline", "deadlines", "project", "projects", "prep", "prepped",
        "exam", "exams", "test", "quiz", "submit", "submission",
        "questions in", "journal", "remaining", "left to do", "left for",
        "what's due", "progress on",
        # General-plan words — not school-specific. Kept modest: no
        # named activities like "movie"/"turf" (that's overfitting to
        # one example), just the structural words a plan/event is
        # actually described with.
        "event", "events", "plan", "plans", "appointment", "trip",
        "getting ready",
        # "today" specifically (not "tomorrow"/"tonight" — those are
        # already "schedule" cues): confirmed gap 2026-08-09, "I have a
        # movie today at 2:45pm" matched no category at all cold, and
        # only reached add_school_item live because of carry-forward
        # left over from an unrelated earlier turn — a coincidence, not
        # a route. "schedule"'s own cues don't cover bare "today" either.
        "today",
        # Already "files" cues too — repeated here so a cold "delete my
        # geography homework" (no prior turn to carry delete_agenda_item
        # forward from) offers it alongside delete_file rather than
        # only the file-deletion tool. Confirmed necessary 2026-08-09:
        # a merged item needed splitting and there was no tool at all
        # for "remove this one", let alone a route to it.
        "delete", "remove",
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


# Matched against the RAW text, not normalise()'s output: _PREFIX strips a
# leading "ok"/"okay", so a bare "okay" normalises to "" and would never
# reach a pattern applied afterwards.
#
# Negatives are deliberately absent. "yes" and "no" are both listed in
# _SOCIAL above, and for a lone utterance out of the blue that is right
# for both — but in context they are not symmetric: an affirmative
# answering FRED's own question needs the tools back to act on, while a
# "no" needs nothing but an acknowledgement, and handing a model tools on
# the turn the user just declined something is how a refusal turns into
# an action.
_AFFIRMATIVE = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok|okay|correct|right|affirmative|"
    r"do it|go ahead|go on|proceed|confirm(?:ed)?|please do|"
    r"yes\s+please|that's\s+right|sounds\s+good)\b[\s,.!]*$",
    re.IGNORECASE,
)


def is_affirmative(text: str) -> bool:
    """
    True for a turn that is nothing but agreement.

    Such a turn is social in isolation and an ANSWER in context; only the
    caller knows which, so this reports the shape and decides nothing.
    """
    return bool(_AFFIRMATIVE.match((text or "").strip()))


# Signals a turn is asking for more than one thing, e.g. "what's the
# time and what are my goals for today". This matters for the
# SELF_NARRATING_TOOLS shortcut in orchestrator.py: that shortcut skips
# the follow-up LLM call and returns a tool's raw result directly, which
# is correct when the tool result IS the whole answer ("what time is
# it") but silently drops the rest of the turn when it's paired with
# something else — confirmed live 2026-08-02: "What is the time and
# what are the goals for today?" called only get_current_time (goals
# aren't a tool at all, they're answered from vault context by the
# follow-up LLM call) and FRED spoke only the time, never touching the
# goals half. A second question word after a conjunction is the cheap,
# reliable tell; it doesn't need to be exhaustive, only to catch the
# common "X and Y" phrasing this shortcut is unsafe for.
_QUESTION_WORD = r"(?:what|when|where|who|which|how|why|is there|do i|did i|can you|could you)"
_COMPOUND_RE = re.compile(
    r"\b(?:and|also|plus)\b\s+" + _QUESTION_WORD, re.IGNORECASE
)

# Two more "cheap, reliable tells" for the same SELF_NARRATING_TOOLS
# problem, but for "do the same action twice" rather than "ask two
# questions". Confirmed bug (session_2026-08-03.jsonl): "Set two
# reminders, on Wednesday and Friday, called live class, at 5:55 pm."
# has no question word after "and" at all, so the original _COMPOUND_RE
# missed it — schedule_reminder (a SELF_NARRATING_TOOLS entry) fired
# once for Wednesday and the shortcut returned immediately, silently
# dropping Friday. A turn naming two different weekdays, or an explicit
# count word before a plural tool-ish noun, is asking for more than one
# of something regardless of phrasing.
_MULTI_DAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r".*\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_MULTI_COUNT_RE = re.compile(
    r"\b(?:two|three|four|five|both|several|multiple)\s+"
    r"(?:reminders?|timers?|tasks?|alarms?)\b",
    re.IGNORECASE,
)

# A fourth tell, for "3 questions in Geography and 1 in physics": two
# counts either side of "and", with no plural noun required after the
# second one (it's carried over from the first clause — "1 in physics"
# means "1 question", the word itself is never repeated). _MULTI_COUNT_RE
# above needs that noun explicitly and misses this shape entirely, which
# is exactly how one school subject silently dropped out of a two-item
# turn without this. A number on each side of "and" is a weaker, more
# general version of the same "asking for more than one" signal; false
# positives cost one extra confirmation round (see the compound-turn
# nudge in orchestrator._generate_with_tools), never a wrong answer.
_NUMBER_WORD = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
_MULTI_ITEM_RE = re.compile(
    rf"\b{_NUMBER_WORD}\b.*?\band\b.*?\b{_NUMBER_WORD}\b", re.IGNORECASE
)


# The third tell: a conjunction followed by a second ACTION verb.
# _COMPOUND_RE above only catches a second QUESTION ("...and what are
# the goals"), so "set the volume to 50 and open chrome" — two actions,
# no question word — read as a single request and the
# SELF_NARRATING_TOOLS shortcut returned after set_volume, never opening
# Chrome. Same shape as the confirmed 2026-08-04 LM Studio failure
# ("open LM studio and go to cerebras.com"), which dispatcher.py had to
# grow its own _COMPOUND_CONNECTOR guard for; this is that guard's
# equivalent one layer up, where it decides whether the tool loop gets a
# second round.
#
# "then" is included here but deliberately NOT in _COMPOUND_RE: "what is
# the time then" is a filler word after a question, whereas "then open
# X" is genuinely sequencing two actions.
_ACTION_VERB = (
    r"(?:open|launch|start|play|close|stop|set|turn|add|remind|schedule|"
    r"tell|show|check|create|make|send|search|find|mute|unmute|pause|"
    r"skip|take|save|delete|move|rename|read|list|cancel|go)"
)
_COMPOUND_ACTION_RE = re.compile(
    r"\b(?:and|then|also|plus|after that)\b\s+(?:please\s+|proceed\s+to\s+)?"
    + _ACTION_VERB + r"\b",
    re.IGNORECASE,
)


def looks_compound(text: str) -> bool:
    """True if the turn appears to ask for more than one thing."""
    text = text or ""
    return bool(
        _COMPOUND_RE.search(text)
        or _COMPOUND_ACTION_RE.search(text)
        or _MULTI_DAY_RE.search(text)
        or _MULTI_COUNT_RE.search(text)
        or _MULTI_ITEM_RE.search(text)
    )


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


# How close the top two candidates' embedding scores need to be for a
# turn to count as genuinely ambiguous, for the pill's disambiguation
# chip (see ui/pill_app.py). Deliberately tight — this should fire on
# real near-ties ("turn it up" between set_volume/set_brightness), not
# on every turn that merely has more than one candidate tool.
CLOSE_CANDIDATE_MARGIN = 0.03


def close_candidates(text: str, tool_names: list, router) -> tuple:
    """
    Returns (top_tool, runner_up_tool) if the two best-scoring names
    among `tool_names` are within CLOSE_CANDIDATE_MARGIN of each other,
    else None. Deliberately separate from classify() rather than a 4th
    return value there — this is purely informational (for a UI hint),
    not part of routing itself, and every existing classify() caller
    should keep working unchanged if this is never called at all.
    """
    if router is None or len(tool_names) < 2:
        return None

    scored = [(name, score) for name, score in router.rank(text) if name in tool_names]
    if len(scored) < 2:
        return None

    top_name, top_score = scored[0]
    second_name, second_score = scored[1]

    if (top_score - second_score) <= CLOSE_CANDIDATE_MARGIN:
        return top_name, second_name

    return None


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

    categories = match_categories(text)

    # The social short-circuit runs first EXCEPT for self-documentation
    # questions. _SOCIAL matches "what can you do" and "tell me about
    # yourself" outright, so before this the one tool built to answer
    # them (ask_about_myself, backlog #13) could never be offered on the
    # exact phrasing the backlog item quotes as its example. Only this
    # one category overrides it — every other social utterance still
    # short-circuits to chat exactly as before.
    if looks_social(text) and "selfdoc" not in categories:
        return False, [], "social/meta phrasing"

    if categories:
        names = tools_for_categories(categories)

        # A compound turn keeps the FULL cue union — narrowing is what
        # breaks it. The embedder ranks each tool against the whole
        # utterance, so on "set a reminder and open Spotify" the second
        # half's tool competes against the first half's wording and can
        # fall below the six-tool cut. And because the tool menu is
        # computed once and reused for every round of the loop (see
        # _generate_with_tools), a tool dropped here is unreachable for
        # the entire turn no matter how many rounds the model gets — the
        # forgotten half can never be recovered.
        #
        # Costs a longer tool list on compound turns only, which is the
        # case where the model most needs to see both options anyway.
        if router is not None and not looks_compound(text):
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
