# "Say that again" — a pure state lookup with zero model calls, so it
# works even if the LLM path is degraded. Dispatcher routing is tested
# here; the state-lookup logic itself (_repeat_last) is exercised
# through the dispatcher tests only at the routing level, since it's a
# bound orchestrator method (see orchestrator.py).

from orchestrator.dispatcher import Dispatcher


def test_common_phrasings_route_to_repeat_last():
    d = Dispatcher()
    for phrase in (
        "say that again", "repeat that", "what did you say",
        "come again", "say again", "can you repeat that?",
    ):
        result = d.match(phrase)
        assert result == {"tool": "repeat_last", "arguments": {}}, phrase


def test_unrelated_phrases_do_not_match():
    d = Dispatcher()
    assert d.match("say hello to my friend") is None
    assert d.match("repeat this file to another folder") is None
