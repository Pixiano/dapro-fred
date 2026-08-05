# Core/tests/test_followup_reclassify.py
#
# The 2026-08-05 failure, replayed: FRED listed tasks, was told they were
# yesterday's, and answered the correction — and then a bare "Check it
# then" — from conversation context, asserting a vault file didn't exist
# without ever looking. Neither follow-up matches a cue, because the
# subject lives in the PREVIOUS turn.

from orchestrator.orchestrator import FREDOrchestrator


def _bare():
    """An orchestrator with no LLM/router — _classify_turn is pure
    routing, so nothing else needs to boot."""
    o = FREDOrchestrator.__new__(FREDOrchestrator)
    o.llm = None
    o._tool_router = lambda: None
    return o


def test_followups_reuse_the_previous_turns_tools():
    o = _bare()

    needs, names, _ = o._classify_turn("what are my tasks for today")
    assert needs and "list_tasks" in names

    for follow_up in ("No, that was for yesterday. I mean today.", "Check it then"):
        needs, names, reason = o._classify_turn(follow_up)
        assert needs, f"{follow_up!r} was answered from context"
        assert "list_tasks" in names
        assert "follow-up" in reason

    # Bounded: CARRY_TOOLS_TURNS follow-ups, then back to normal routing.
    needs, _, _ = o._classify_turn("mm alright I see")
    assert not needs


def test_classification_is_stable_when_asked_twice_for_one_turn():
    """process_stream() and _generate_with_tools() both classify the same
    turn; the carry-forward is consumed, so it must be memoised."""
    o = _bare()
    o._classify_turn("what are my tasks for today")

    first = o._classify_turn("Check it then")
    assert o._classify_turn("Check it then") == first


def test_social_turn_does_not_inherit_tools():
    o = _bare()
    o._classify_turn("what are my tasks for today")

    needs, _, _ = o._classify_turn("thanks fred")
    assert not needs
