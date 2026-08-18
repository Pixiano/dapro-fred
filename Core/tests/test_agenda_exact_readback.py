# Core/tests/test_agenda_exact_readback.py
#
# EXACT_READBACK_TOOLS (orchestrator.py), built for "3 questions in
# Geography and 1 in physics, due in 3 days": on a compound turn, the
# existing loop lets the model paraphrase its own two tool results into
# one final sentence once it stops calling tools. That paraphrase is a
# second chance to garble a date the deterministic tool already stated
# correctly one message earlier — this drives the real loop with a
# scripted LLM to confirm the raw tool strings win instead, without
# breaking the existing schedule_reminder+list_scheduled compound case
# (test_compound_tool_calls.py), which deliberately keeps the model's
# own synthesis.

from orchestrator import intent
from orchestrator.orchestrator import FREDOrchestrator


class _FakeLLM:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def generate_with_tools(self, messages, tools, local_only=False):
        reply = self._replies[self.calls]
        self.calls += 1
        return reply

    def generate(self, messages, local_only=False):
        return "unused"


class _FakeTools:
    def is_destructive(self, name):
        return False

    def get_tool_definitions(self, only=None):
        return []

    def list_tools(self):
        return ["add_agenda_item", "list_scheduled", "schedule_reminder"]


def _bare_orchestrator(llm, results_by_tool):
    orch = FREDOrchestrator.__new__(FREDOrchestrator)
    orch.llm = llm
    orch.tools = _FakeTools()
    orch._execute_tool_call = lambda call: results_by_tool[call["function"]["name"]]
    orch._last_tools_offered = None
    orch._last_routing_reason = None
    orch._router = "stub"
    return orch


def _tool_call(call_id, name, arguments="{}"):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _messages(text):
    return [{"role": "user", "content": text}]


def test_compound_agenda_add_returns_the_raw_confirmations_not_a_paraphrase(monkeypatch):
    monkeypatch.setattr(
        intent, "classify", lambda text, llm, router: (True, ["agenda"], "test")
    )
    monkeypatch.setattr(intent, "close_candidates", lambda *a, **k: None)
    monkeypatch.setattr(intent, "looks_compound", lambda text: True)
    monkeypatch.setattr("orchestrator.orchestrator.TOOLS_ENABLED", True)

    geo_result = "Logged, sir — Geography, 3 questions, due Wednesday the 12th."
    phys_result = "Logged, sir — Physics, 1 question, due Wednesday the 12th."

    llm = _FakeLLM([
        {"content": None, "tool_calls": [_tool_call("1", "add_agenda_item")]},
        {"content": None, "tool_calls": [_tool_call("2", "add_agenda_item")]},
        # Round 3: the model stops calling tools and tries to summarise
        # in its own words — this must NOT be what gets spoken.
        {"content": "Got both of those logged for you, sir, due soon.", "tool_calls": None},
    ])
    orch = _bare_orchestrator(llm, {"add_agenda_item": None})
    # _execute_tool_call needs to return per-call results, not a fixed
    # dict lookup — override with a small stateful stub instead.
    calls = iter([geo_result, phys_result])
    orch._execute_tool_call = lambda call: next(calls)

    reply = orch._generate_with_tools(
        _messages("3 questions in Geography and 1 in physics, due in 3 days")
    )

    assert geo_result in reply
    assert phys_result in reply
    assert "Got both of those logged" not in reply


def test_reminder_and_check_compound_still_uses_the_models_own_synthesis(monkeypatch):
    """Non-regression: the EXISTING compound case this loop was built
    for (test_compound_tool_calls.py) must keep getting the model's
    natural-language synthesis, not raw concatenation — schedule_reminder
    and list_scheduled are self-narrating but deliberately NOT in
    EXACT_READBACK_TOOLS."""
    monkeypatch.setattr(
        intent, "classify", lambda text, llm, router: (True, ["schedule"], "test")
    )
    monkeypatch.setattr(intent, "close_candidates", lambda *a, **k: None)
    monkeypatch.setattr(intent, "looks_compound", lambda text: True)
    monkeypatch.setattr("orchestrator.orchestrator.TOOLS_ENABLED", True)

    llm = _FakeLLM([
        {"content": None, "tool_calls": [_tool_call("1", "list_scheduled")]},
        {"content": None, "tool_calls": [_tool_call("2", "schedule_reminder")]},
        {"content": "Set for 6pm. You already had one other reminder.", "tool_calls": None},
    ])
    calls = iter(["1 reminder: pasta at 5pm", "Reminder set for 6pm."])
    orch = _bare_orchestrator(llm, {})
    orch._execute_tool_call = lambda call: next(calls)

    reply = orch._generate_with_tools(_messages(
        "Set a reminder for 6pm called check oven and tell me if I have one already."
    ))

    assert reply == "Set for 6pm. You already had one other reminder."


def test_mixed_agenda_and_scheduled_now_both_exact_readback_raw_joins(monkeypatch):
    """list_scheduled joined EXACT_READBACK_TOOLS 2026-08-18 (review
    finding: it reads live scheduler state and a due date/reminder count
    deserves the same raw-readback protection add_agenda_item already
    gets, especially on a compound turn) — so this pairing now raw-joins
    instead of falling back to model synthesis, the same shape as
    test_compound_agenda_add_returns_the_raw_confirmations_not_a_paraphrase
    above. The schedule_reminder+list_scheduled pairing tested above this
    one is unaffected: schedule_reminder is still deliberately NOT
    exact-readback, so that compound case keeps its synthesis."""
    monkeypatch.setattr(
        intent, "classify", lambda text, llm, router: (True, ["agenda", "schedule"], "test")
    )
    monkeypatch.setattr(intent, "close_candidates", lambda *a, **k: None)
    monkeypatch.setattr(intent, "looks_compound", lambda text: True)
    monkeypatch.setattr("orchestrator.orchestrator.TOOLS_ENABLED", True)

    agenda_result = "Logged, sir — Geography, due tomorrow."
    scheduled_result = "No reminders scheduled."

    llm = _FakeLLM([
        {"content": None, "tool_calls": [_tool_call("1", "add_agenda_item")]},
        {"content": None, "tool_calls": [_tool_call("2", "list_scheduled")]},
        # Round 3: the model stops calling tools and tries to summarise
        # in its own words — this must NOT be what gets spoken.
        {"content": "Logged the homework and you've no reminders pending.", "tool_calls": None},
    ])
    calls = iter([agenda_result, scheduled_result])
    orch = _bare_orchestrator(llm, {})
    orch._execute_tool_call = lambda call: next(calls)

    reply = orch._generate_with_tools(_messages(
        "log geography homework due tomorrow and tell me if I have any reminders"
    ))

    assert agenda_result in reply
    assert scheduled_result in reply
    assert "Logged the homework and you've no reminders pending." not in reply


def test_single_agenda_add_still_takes_the_fast_self_narrating_path(monkeypatch):
    """Non-compound single item: the existing SELF_NARRATING_TOOLS
    shortcut fires on round 1, no second LLM call at all — confirms
    EXACT_READBACK_TOOLS didn't change the common case's cost."""
    monkeypatch.setattr(
        intent, "classify", lambda text, llm, router: (True, ["agenda"], "test")
    )
    monkeypatch.setattr(intent, "close_candidates", lambda *a, **k: None)
    monkeypatch.setattr(intent, "looks_compound", lambda text: False)
    monkeypatch.setattr("orchestrator.orchestrator.TOOLS_ENABLED", True)

    result = "Logged, sir — Geography, 3 questions, due tomorrow."
    llm = _FakeLLM([{"content": None, "tool_calls": [_tool_call("1", "add_agenda_item")]}])
    orch = _bare_orchestrator(llm, {})
    orch._execute_tool_call = lambda call: result

    reply = orch._generate_with_tools(_messages("3 questions in geography due tomorrow"))

    assert reply == result
    assert llm.calls == 1
