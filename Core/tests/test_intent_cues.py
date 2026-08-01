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
