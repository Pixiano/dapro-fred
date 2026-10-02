# Core/tests/test_agents.py
#
# orchestrator/agents.py's guarantees, no model involved: the tool
# allowlist is enforced in code, the toolless role never sees a tool menu,
# the round budget ends the loop, and delegation is the only way in.

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import agents, intent


class _Registry:
    def __init__(self):
        self.ran = []

    def get_tool_definitions(self, only=None):
        assert only, "an empty 'only' means ALL tools -- agents must never pass one"
        return [{"type": "function", "function": {"name": n}} for n in only]

    def execute(self, name, **kwargs):
        self.ran.append((name, kwargs))
        return f"results for {kwargs}"


def _call(name, **args):
    return {"id": "1", "function": {"name": name, "arguments": json.dumps(args)}}


class _LLM:
    def __init__(self, tool_turns):
        self.tool_turns = list(tool_turns)  # one list of calls per round, then an answer
        self.plain_calls = []

    def generate_with_tools(self, messages, tools=None, tier=None):
        if self.tool_turns:
            return {"content": "", "tool_calls": self.tool_turns.pop(0)}
        return {"content": "Answer from the results.", "tool_calls": None}

    def generate(self, messages, tier=None):
        self.plain_calls.append(messages)
        return "plain answer"


def test_researcher_refuses_tools_off_its_allowlist():
    reg = _Registry()
    llm = _LLM([[_call("web_search", query="gpus"), _call("delete_file", path="C:/x"), _call("send_message", contact="x")]])
    assert agents.run("researcher", "compare gpus", llm, reg) == "Answer from the results."
    assert reg.ran == [("web_search", {"query": "gpus"})]


def test_general_agent_never_gets_a_tool_menu():
    reg, llm = _Registry(), _LLM([[_call("web_search", query="x")]])
    assert agents.run("general", "rewrite: gonna be late", llm, reg) == "plain answer"
    assert reg.ran == [] and llm.tool_turns  # tool-calling path never touched


def test_round_budget_forces_an_answer():
    reg = _Registry()
    llm = _LLM([[_call("web_search", query=str(i))] for i in range(10)])
    assert agents.run("researcher", "endless", llm, reg) == "plain answer"
    assert len(reg.ran) == agents.AGENTS["researcher"]["rounds"]


def test_empty_task_spawns_nothing():
    reg, llm = _Registry(), _LLM([])
    assert "needs a task" in agents.run("researcher", "  ", llm, reg)
    assert reg.ran == [] and llm.plain_calls == []


def test_routing_offers_delegation_only_when_asked():
    assert "delegate" in intent.match_categories("research the best local TTS models under 1GB")
    assert "delegate" in intent.match_categories("rewrite this to sound more formal")
    for plain in ("what time is it", "set the volume to 40", "how are you"):
        assert "delegate" not in intent.match_categories(plain)
