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
    """The two follow-ups below take two DIFFERENT correct paths, not
    the same one — "I mean today" independently re-matches a category
    (the "today" agenda cue added 2026-08-09) and goes through the
    correction-union branch, while "Check it then" matches no cue at
    all and goes through the plain no-cue follow-up branch. Both must
    still keep list_tasks reachable; that's the actual invariant, not
    which internal branch name produced it."""
    o = _bare()

    needs, names, reason = o._classify_turn("what are my tasks for today")
    assert needs and "list_tasks" in names

    needs, names, reason = o._classify_turn("No, that was for yesterday. I mean today.")
    assert needs, "the correction was answered from context"
    assert "list_tasks" in names
    assert "correction" in reason

    needs, names, reason = o._classify_turn("Check it then")
    assert needs, "the follow-up was answered from context"
    assert "list_tasks" in names
    assert "follow-up" in reason

    # No expiry check here on purpose: the correction turn above is a
    # POSITIVE match (it independently re-matches "agenda" via "today"),
    # which resets the carry window to a fresh CARRY_TOOLS_TURNS rather
    # than consuming one — correct (a correction re-anchors the subject,
    # so it earns a full follow-up allowance again), but it means the
    # exact turn this window would next expire on depends on phrasing
    # that isn't this test's concern. See
    # test_an_ordinary_carry_expires_after_bounded_turns below for that,
    # using phrasing that doesn't re-match anything mid-sequence.


def test_an_ordinary_carry_expires_after_bounded_turns():
    """Same shape as test_followups_reuse_the_previous_turns_tools, but
    every follow-up is cue-free, so the carry only ever decrements —
    nothing re-matches and resets the window mid-sequence. Each
    follow-up uses distinct wording: _classify_turn memoises on the
    literal text (asking twice about the SAME turn must not double-
    consume it — see its own docstring), so repeating one string here
    would look like re-asking about one turn, not a new one."""
    o = _bare()
    o._classify_turn("what are my tasks")

    follow_ups = ["Check it then", "go on then", "and also"][: o.CARRY_TOOLS_TURNS]
    assert len(follow_ups) == o.CARRY_TOOLS_TURNS

    for phrase in follow_ups:
        needs, names, reason = o._classify_turn(phrase)
        assert needs
        assert "list_tasks" in names
        assert "follow-up" in reason

    needs, _, _ = o._classify_turn("one more after that")
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


# --- proactive priming (2026-08-09) --------------------------------------
#
# A carryover/upcoming-event question is FRED speaking first, not the
# user — there is no prior user turn for the ordinary carry-forward to
# key off. _prime_carry is the thing that plants the tools anyway, right
# when proactive_checks.py speaks the question, so whatever comes back
# ("yeah I did it", a bare "no", a full sentence) still reaches the
# right tool instead of falling to conversation memory or chat.


def test_primed_carry_survives_into_the_next_turn():
    o = _bare()
    o._prime_carry(["update_agenda_item"])

    needs, names, reason = o._classify_turn("yeah I finished it")
    assert needs
    assert "update_agenda_item" in names
    assert "follow-up" in reason


def test_primed_carry_reaches_a_no_that_carries_a_reason():
    """Vatsal's own example: "no, teacher extended it to Friday" — not
    an affirmative, but not bare social "no" either (looks_social's "no"
    alternative is end-anchored and doesn't match once more follows it),
    so `not looks_social(...)` is what carries this one."""
    o = _bare()
    o._prime_carry(["update_agenda_item"])

    needs, names, _ = o._classify_turn("no, teacher extended it to friday")
    assert needs
    assert "update_agenda_item" in names


def test_primed_carry_lets_a_bare_no_stay_conversation():
    """A carryover question's bare "no" means the item is unchanged —
    still open, nothing to update — so there is genuinely nothing for
    update_agenda_item to do here; matches the deletion-confirmation
    precedent (test_a_bare_no_stays_conversation above) that a lone "no"
    must not re-arm the menu."""
    o = _bare()
    o._prime_carry(["update_agenda_item"])

    needs, _, _ = o._classify_turn("no")
    assert not needs


def test_primed_carry_is_bounded_the_same_as_an_ordinary_carry():
    o = _bare()
    o._prime_carry(["update_agenda_item"])
    o._classify_turn("no")
    o._classify_turn("teacher extended it")

    needs, _, _ = o._classify_turn("mm alright I see")
    assert not needs
