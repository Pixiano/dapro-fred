# Core/tests/test_followup_retrieval.py

from orchestrator.orchestrator import _retrieval_query

HISTORY = [
    {"role": "user", "content": "What is the Whitby scheduling policy?"},
    {"role": "assistant", "content": "A reply."},
]


def test_short_followup_is_widened_with_the_previous_user_turn():
    query = _retrieval_query("Why is that??", HISTORY)
    assert "Whitby" in query and "Why is that??" in query


def test_a_full_question_is_left_alone():
    q = "What does the vault say about my chemistry journal deadline?"
    assert _retrieval_query(q, HISTORY) == q


def test_the_current_turn_is_not_prepended_to_itself():
    history = HISTORY + [{"role": "user", "content": "Why is that??"}]
    assert _retrieval_query("Why is that??", history).count("Why is that??") == 1


def test_falls_back_to_the_input_with_no_user_turn_to_use():
    assert _retrieval_query("Why is that??", []) == "Why is that??"
    assert _retrieval_query("Why?", [{"role": "assistant", "content": "x"}]) == "Why?"
