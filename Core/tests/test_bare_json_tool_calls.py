# Core/tests/test_bare_json_tool_calls.py
#
# Qwen sometimes writes its tool call as bare JSON with no <tool_call>
# wrapper. On 2026-08-05 that shape was dropped by the parser and FRED
# answered "no tasks recorded for today" on a day with six of them.

import json
import types

from orchestrator.orchestrator import FREDOrchestrator


def _fred():
    fred = FREDOrchestrator.__new__(FREDOrchestrator)
    fred.tools = types.SimpleNamespace(
        tools={"list_tasks": {"parameters": {"required": []}}},
        list_tools=lambda: ["list_tasks"],
    )
    return fred


def test_bare_json_call_is_parsed():
    calls = _fred()._parse_text_tool_calls('{"name": "list_tasks", "arguments": {}}')
    assert [c["function"]["name"] for c in calls] == ["list_tasks"]
    assert json.loads(calls[0]["function"]["arguments"]) == {}


def test_nested_arguments_and_trailing_prose():
    calls = _fred()._parse_text_tool_calls(
        'Let me check. {"name": "list_tasks", "arguments": {"day": "2026-08-05"}} '
        "One moment."
    )
    assert json.loads(calls[0]["function"]["arguments"]) == {"day": "2026-08-05"}


def test_unknown_tool_name_is_ignored():
    assert _fred()._parse_text_tool_calls('{"name": "not_a_tool", "arguments": {}}') == []


def test_malformed_call_is_treated_as_leaked_syntax_not_an_answer():
    # Unparseable, so it must never be spoken — the orchestrator
    # regenerates instead.
    fred = _fred()
    assert fred._parse_text_tool_calls('{"name": "list_tasks", "arguments": }') == []
    assert fred._looks_like_leaked_tool_syntax('{"name": "list_tasks", "arguments": }')
    assert not fred._looks_like_leaked_tool_syntax("You have six tasks today, sir.")
