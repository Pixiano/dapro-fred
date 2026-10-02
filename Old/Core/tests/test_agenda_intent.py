# Routing for the homework/project/event-tracking feature (2026-08-09):
# the "agenda" category has to actually be reachable, and a turn naming
# two items in one breath ("3 questions in Geography and 1 in physics")
# has to be recognised as compound or the second item silently never
# gets logged — same failure shape as the reminders bugs
# test_intent_cues.py already pins, just for a phrasing those tells
# don't cover.

from orchestrator import intent


def test_agenda_phrasing_reaches_the_agenda_tools():
    for phrase in (
        "I have geography homework due tomorrow",
        "what's my progress on the physics project",
        "add a chemistry journal assignment",
        "what's due this week",
        "what is remaining for tomorrow",
        "did I submit the history essay",
    ):
        categories = intent.match_categories(phrase)
        assert "agenda" in categories, phrase
        tools = intent.tools_for_categories(categories)
        assert "list_agenda_items" in tools or "add_agenda_item" in tools or "update_agenda_item" in tools


def test_delete_agenda_item_is_reachable_cold():
    """A cold "delete my geography homework" with no prior turn to carry
    from must still offer delete_agenda_item alongside delete_file, not
    just the file tool. Confirmed necessary 2026-08-09."""
    categories = intent.match_categories("delete my geography homework")
    assert "agenda" in categories
    tools = intent.tools_for_categories(categories)
    assert "delete_agenda_item" in tools


def test_bare_questions_word_does_not_widen_to_agenda():
    """"I have a question" / "any questions" is ordinary conversation,
    not homework — same reasoning test_intent_cues.py already applies to
    "tasks" excluding bare "to do"."""
    assert "agenda" not in intent.match_categories("I have a question about this")
    assert "agenda" not in intent.match_categories("any questions?")


def test_general_event_phrasing_reaches_agenda_too():
    """The "event" kind was always meant to cover a movie or meeting
    friends, not just school — confirmed necessary 2026-08-09, the same
    day a movie got logged through a tool that was still called
    add_school_item. Cues have to actually reach non-school plans."""
    for phrase in (
        "I have a movie today at 2:45pm",
        "add an event for the trip on Saturday",
        "what are my plans for tomorrow",
    ):
        assert "agenda" in intent.match_categories(phrase), phrase


def test_two_agenda_items_in_one_utterance_is_flagged_compound():
    """The exact shape from the request: two counts, one "and", no
    plural noun after the second count (it's implied from the first
    clause) — _MULTI_COUNT_RE alone does not catch this."""
    assert intent.looks_compound(
        "today I got 3 questions in Geography and 1 in physics, due in 3 days"
    )
    assert intent.looks_compound("two questions in chemistry and one in biology")


def test_a_single_agenda_item_is_not_flagged_compound():
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
