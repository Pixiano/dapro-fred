# Confirmed bug: "Set a reminder for 6pm called X and if there's already
# a reminder, tell me" only ever ran list_scheduled — the model asked for
# one tool on its first pass and _generate_with_tools took the single
# tool_calls batch it got as the whole turn, so schedule_reminder never
# ran. Fixed by turning that single shot into a bounded round-trip loop
# (orchestrator._generate_with_tools) so a forgotten second tool gets one
# more chance to be requested once the first result is in context.
#
# Orchestrator.__init__ needs real models/tools, so this constructs a
# bare instance via __new__ and stubs only what _generate_with_tools
# actually touches — same pattern as test_turn_dedup.py.

from orchestrator import intent
from orchestrator.orchestrator import FREDOrchestrator


class _FakeLLM:
    """Scripts one generate_with_tools() reply per call."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def generate_with_tools(self, messages, tools):
        reply = self._replies[self.calls]
        self.calls += 1
        return reply


class _FakeTools:
    def is_destructive(self, name):
        return False

    def get_tool_definitions(self, only=None):
        return []

    def list_tools(self):
        return ["list_scheduled", "schedule_reminder"]


def _bare_orchestrator(llm):
    orch = FREDOrchestrator.__new__(FREDOrchestrator)
    orch.llm = llm
    orch.tools = _FakeTools()
    orch._executed = []
    orch._execute_tool_call = lambda call: orch._executed.append(
        call["function"]["name"]
    ) or f"ran {call['function']['name']}"
    orch._last_tools_offered = None
    orch._last_routing_reason = None
    # Pre-set so _tool_router() short-circuits instead of building a real
    # (slow, model-backed) one — intent.classify is mocked, so the value
    # returned here is never actually used.
    orch._router = "stub"
    return orch


def _tool_call(call_id, name, arguments="{}"):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def test_compound_request_runs_both_tools_across_rounds(monkeypatch):
    monkeypatch.setattr(
        intent, "classify", lambda text, llm, router: (True, ["schedule"], "test")
    )
    monkeypatch.setattr(intent, "close_candidates", lambda *a, **k: None)
    monkeypatch.setattr(intent, "looks_compound", lambda text: True)
    monkeypatch.setattr("orchestrator.orchestrator.TOOLS_ENABLED", True)

    llm = _FakeLLM([
        # Round 1: model only asks for the check, forgetting to set it.
        {"content": None, "tool_calls": [_tool_call("1", "list_scheduled")]},
        # Round 2: with that result in context, it asks for the one it
        # forgot.
        {"content": None, "tool_calls": [_tool_call(
            "2", "schedule_reminder",
            '{"message": "Alan J.E. class", "minutes": 60}',
        )]},
        # Round 3: done — final spoken reply, no more tool calls.
        {"content": "Set for 6pm. You already had one other reminder.",
         "tool_calls": None},
    ])
    orch = _bare_orchestrator(llm)

    messages = [{"role": "user", "content": (
        "Set a reminder for 6 p.m. called Alan J.E. class and if there's "
        "already a reminder, please tell me."
    )}]

    reply = orch._generate_with_tools(messages)

    assert orch._executed == ["list_scheduled", "schedule_reminder"]
    assert reply == "Set for 6pm. You already had one other reminder."
