# Routing for the school-tracking feature (2026-08-09): the "school"
# category has to actually be reachable, and a turn naming two items in
# one breath ("3 questions in Geography and 1 in physics") has to be
# recognised as compound or the second item silently never gets logged
# — same failure shape as the reminders bugs test_intent_cues.py already
# pins, just for a phrasing those tells don't cover.

from orchestrator import intent


def test_school_phrasing_reaches_the_school_tools():
    for phrase in (
        "I have geography homework due tomorrow",
        "what's my progress on the physics project",
        "add a chemistry journal assignment",
        "what's due this week",
        "what is remaining for tomorrow",
        "did I submit the history essay",
    ):
        categories = intent.match_categories(phrase)
        assert "school" in categories, phrase
        tools = intent.tools_for_categories(categories)
        assert "list_school_items" in tools or "add_school_item" in tools or "update_school_item" in tools


def test_delete_school_item_is_reachable_cold():
    """A cold "delete my geography homework" with no prior turn to carry
    from must still offer delete_school_item alongside delete_file, not
    just the file tool. Confirmed necessary 2026-08-09."""
    categories = intent.match_categories("delete my geography homework")
    assert "school" in categories
    tools = intent.tools_for_categories(categories)
    assert "delete_school_item" in tools


def test_bare_questions_word_does_not_widen_to_school():
    """"I have a question" / "any questions" is ordinary conversation,
    not homework — same reasoning test_intent_cues.py already applies to
    "tasks" excluding bare "to do"."""
    assert "school" not in intent.match_categories("I have a question about this")
    assert "school" not in intent.match_categories("any questions?")


def test_two_school_items_in_one_utterance_is_flagged_compound():
    """The exact shape from the request: two counts, one "and", no
    plural noun after the second count (it's implied from the first
    clause) — _MULTI_COUNT_RE alone does not catch this."""
    assert intent.looks_compound(
        "today I got 3 questions in Geography and 1 in physics, due in 3 days"
    )
    assert intent.looks_compound("two questions in chemistry and one in biology")


def test_a_single_school_item_is_not_flagged_compound():
    assert not intent.looks_compound("I have 3 questions in Geography due tomorrow")
    assert not intent.looks_compound("mark the geography homework as done")


def test_multi_item_tell_does_not_fire_without_and():
    """A count next to a time ("5:55 pm") must not look like two items
    just because a clock reads as two numbers — same phrase already
    pinned safe in test_intent_cues.py, checked again against the new
    tell specifically."""
    assert not intent.looks_compound("remind me on Wednesday at 5:55 pm to call live class")


def test_multi_item_tell_does_not_fire_on_and_with_no_second_number():
    assert not intent.looks_compound("add milk and eggs to the shopping list")
