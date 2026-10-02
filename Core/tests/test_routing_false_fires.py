# Core/tests/test_routing_false_fires.py
#
# General suite, 2026-09-30: "what's the time complexity of binary search"
# ran get_current_time (the dispatcher's time pattern was an unanchored
# prefix), and "say 'systems online' and nothing else" ran get_system_status
# (the quoted word "online" is a sysinfo cue).

from orchestrator import intent
from orchestrator.dispatcher import Dispatcher


def test_time_pattern_is_anchored():
    d = Dispatcher()
    assert d.match("what's the time complexity of binary search") is None
    for q in ("what time is it", "what's the time?", "what is the time right now, fred"):
        assert d.match(q)["tool"] == "get_current_time"


def test_time_complexity_is_not_a_clock_cue():
    assert "get_current_time" not in intent.classify("what's the time complexity of binary search")[1]


def test_quoted_echo_gets_no_tools():
    for q in ("say 'systems online' and nothing else", 'Say "all good"', "repeat 'x' and nothing more"):
        assert intent.classify(q) [0] is False
    # a message to someone is not an echo
    assert intent.looks_social("say 'hello' to mom") is False
