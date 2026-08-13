# Cue-collision regressions in orchestrator/intent.py. Every case here
# is a real misroute pulled from a session log, not a hypothetical —
# this class of bug (an ordinary English word doubling as a tool cue)
# has now bitten three times, so each fix gets pinned.

from orchestrator import intent


def test_project_copy_does_not_read_the_clipboard():
    """
    Real transcripts: session_2026-08-01_14-24-11.jsonl, three separate
    turns. "project copy" is a PROJECT NAME; the bare "copy" cue made
    clipboard the only matching category, so get_clipboard fired and
    FRED answered about clipboard contents instead of the project.
    """
    for phrase in (
        "did we do last in project copy?",
        "I am referring to project copy.md",
        "Can you check what we did last in project copy?",
    ):
        assert "clipboard" not in intent.match_categories(phrase), phrase


def test_real_clipboard_requests_still_match():
    for phrase in ("what is in my clipboard", "copy that", "paste that here"):
        assert "clipboard" in intent.match_categories(phrase), phrase


def test_compound_time_and_goals_is_flagged():
    """
    Real transcript: session_2026-08-02.jsonl, 16:31:14. "What is the
    time and what are the goals for today?" called only get_current_time
    (goals aren't a tool — they're answered from vault context by the
    orchestrator's follow-up LLM call) and, because get_current_time is
    in SELF_NARRATING_TOOLS, that follow-up call was skipped entirely.
    FRED spoke only the time and never addressed the goals half. This
    flag is what orchestrator._generate_with_tools now checks before
    taking that shortcut.
    """
    assert intent.looks_compound("What is the time and what are the goals for today?")


def test_single_self_narrating_ask_is_not_flagged():
    for phrase in ("What is the time?", "add milk and eggs to the shopping list"):
        assert not intent.looks_compound(phrase), phrase


def test_two_actions_joined_by_a_conjunction_is_flagged():
    """
    "set the volume to 50 and open chrome" was asserted NOT compound
    here until 2026-08-04, which was wrong: it is two actions, and
    set_volume is in SELF_NARRATING_TOOLS, so the shortcut returned
    after setting the volume and Chrome was never opened — the same
    shape as the confirmed "open LM studio and go to cerebras.com"
    failure. _COMPOUND_RE only looked for a second QUESTION word, so a
    second IMPERATIVE went unnoticed.
    """
    for phrase in (
        "set the volume to 50 and open chrome",
        "open LM studio and go to cerebras.com",
        "I want you to open LM studio THEN proceed to open the cerebras website",
        "set a reminder for 6pm and open Spotify",
    ):
        assert intent.looks_compound(phrase), phrase


def test_two_reminders_two_weekdays_is_flagged():
    """
    Real transcript (session_2026-08-03.jsonl): "Set two reminders, on
    Wednesday and Friday, called live class, at 5:55 pm." has no
    question word after "and" at all, so the original _COMPOUND_RE
    missed it entirely. schedule_reminder is a SELF_NARRATING_TOOLS
    entry, so its shortcut fired after the FIRST reminder (Wednesday)
    and returned immediately — Friday was silently never scheduled.
    """
    assert intent.looks_compound(
        "Set two reminders, on Wednesday and Friday, called live class, at 5:55 pm."
    )
    assert intent.looks_compound("remind me on Monday and on Thursday")


def test_plural_reminders_matches_the_schedule_category():
    """
    Same real transcript, second bug: word-boundary cue matching means
    \\breminder\\b never matches inside "reminders" (no boundary after
    "remind"/"reminder", only after the trailing s). With no category
    matched at all, classify() fell through to the LLM ACTION/CHAT
    classifier, which returns tool_names=[] --
    get_tool_definitions(only=[]) treats that as "no filter" and sends
    literally every registered tool's schema on every round of an
    already multi-round tool-calling turn. That's what actually 413'd
    Groq (task_faf27f8d) -- fixed by adding the plural cues explicitly,
    same convention as "windows" elsewhere in CATEGORY_CUES.
    """
    assert "schedule" in intent.match_categories(
        "Set two reminders, on Wednesday and Friday, called live class, at 5:55 pm."
    )
    assert "schedule" in intent.match_categories("cancel all my alarms")
    assert "schedule" in intent.match_categories("what timers do I have")


def test_single_day_reminder_is_not_flagged():
    assert not intent.looks_compound("remind me on Wednesday at 5:55 pm to call live class")


def test_on_track_is_not_a_volume_request():
    """The original cue collision: bare "track" vs. the idiom."""
    assert "audio" not in intent.match_categories("Am I on track with my bulk?")
    assert "audio" in intent.match_categories("skip to the next track")


def test_find_offers_file_tools_not_just_apps():
    """
    Real transcript: session_2026-08-01_18-21-35.jsonl — "find
    spotify.exe" matched only the apps category (via the "spotify"
    cue), so search_files/find_file_smart were never offered and the
    model had no way to do what was asked.
    """
    categories = intent.match_categories("find spotify.exe")
    assert "files" in categories
    tools = intent.tools_for_categories(categories)
    assert "search_files" in tools or "find_file_smart" in tools


def test_find_near_a_place_also_offers_web_search():
    """
    Real transcript: session_2026-08-12.jsonl, 14:13:19-14:14:38.
    "Find turfs to play football near Malaad West." matched only the
    "files" category (bare "find" was already a files cue, added for
    the spotify.exe bug above), so web_search was never in the offered
    menu at all. The model, offered only file/app tools, called
    find_file_smart twice against a location query with no matching
    file on disk — both failed ("Couldn't find a file matching 'turfs
    near Malaad West'...", "...'Malaad West'...") — and the turn ended
    with nothing useful said. "search" now also matches bare "find", so
    web_search is at least offered alongside the file tools; the model
    picks from the full set instead of never seeing the right option.
    """
    categories = intent.match_categories(
        "Find turfs to play football near Malaad West."
    )
    assert "search" in categories
    tools = intent.tools_for_categories(categories)
    assert "web_search" in tools


def test_look_at_my_screen_offers_whats_on_screen():
    """
    Real transcript, 2026-08-13: "Can you look at my screen ... tell me
    if I have used the correct English or not" matched only "display"
    (bare "screen") — every "vision" cue required the exact phrase "on
    my screen", which "at my screen" isn't. whats_on_screen was never
    offered; the model, given only take_screenshot (saves a file, can't
    describe anything), correctly said it couldn't see the screen. Bare
    "screen" now also in the vision cues, so vision is reachable
    whenever display is, same shape as the find/web_search fix above.
    """
    categories = intent.match_categories(
        "Can you look at my screen on the first to give preview feedback "
        "and tell me if I have used the correct English or not"
    )
    assert "vision" in categories
    tools = intent.tools_for_categories(categories)
    assert "whats_on_screen" in tools

    # The original fix must still hold: this cue is additive, not a
    # replacement, so "find spotify.exe" still offers the file tools too.
    tools = intent.tools_for_categories(intent.match_categories("find spotify.exe"))
    assert "find_file_smart" in tools or "search_files" in tools
