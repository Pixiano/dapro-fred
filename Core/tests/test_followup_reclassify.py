# Core/tests/test_followup_reclassify.py
#
# The 2026-08-05 failure, replayed: FRED listed tasks, was told they were
# yesterday's, and answered the correction — and then a bare "Check it
# then" — from conversation context, asserting a vault file didn't exist
# without ever looking. Neither follow-up matches a cue, because the
# subject lives in the PREVIOUS turn.

import orchestrator.intent as intent
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


# --- the 2026-08-06 deletion failures -----------------------------------
#
# Both turns below routed away from delete_file, and both times the model
# answered by describing a deletion it had no way to perform.


def test_a_correction_keeps_the_previous_turns_tools_too(monkeypatch):
    """The observed routing: "I meant identity.md" classified POSITIVE, on
    the vault-open category, which replaced the menu and took delete_file
    out of it. The older follow-up branch never runs on a positive
    classification, so only the union covers this."""
    o = _bare()

    needs, names, _ = o._classify_turn("delete personal/identity.md")
    assert needs and "delete_file" in names

    monkeypatch.setattr(
        intent, "classify",
        lambda text, llm=None, router=None: (True, ["read_vault_file"], "vault cue"),
    )

    needs, names, reason = o._classify_turn("I meant identity.md")
    assert needs
    assert "delete_file" in names, "the correction dropped the tool it was correcting"
    assert "read_vault_file" in names, "the new category must survive too"
    assert "correction" in reason


def test_the_union_does_not_shrink_an_offer_everything_turn(monkeypatch):
    """classify returns [] for "no category matched, offer everything".
    Merging that with a carry would REMOVE options, not add them."""
    o = _bare()
    o._classify_turn("delete personal/identity.md")

    monkeypatch.setattr(
        intent, "classify",
        lambda text, llm=None, router=None: (True, [], "no category"),
    )

    needs, names, _ = o._classify_turn("do the thing")
    assert needs and names == []


def test_a_bare_yes_answers_the_question_instead_of_chatting():
    """When the MODEL asks in prose, nothing is pending upstream, so this
    is the only thing standing between "Yes" and a fabricated reply."""
    o = _bare()
    o._classify_turn("delete personal/identity.md")

    for answer in ("Yes", "yes.", "Okay", "go ahead", "Do it"):
        o._carry_left = o.CARRY_TOOLS_TURNS      # fresh window per answer
        needs, names, _ = o._classify_turn(answer)
        assert needs, f"{answer!r} was routed to chat"
        assert "delete_file" in names


def test_a_bare_no_stays_conversation():
    """Declining must not re-arm the menu — the turn after a refusal is
    the worst possible moment to hand the model a destructive tool."""
    o = _bare()
    o._classify_turn("delete personal/identity.md")

    needs, _, _ = o._classify_turn("No")
    assert not needs


def test_an_affirmative_out_of_the_blue_is_still_chat():
    """No carry, no tools — "sure" on its own is just agreement."""
    o = _bare()
    needs, _, _ = o._classify_turn("sure")
    assert not needs
