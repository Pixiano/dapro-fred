# Multi-tool execution under load — the weakest part of the pipeline, and
# the one with the most confirmed real-world failures behind it.
#
# Every scenario here is either a transcript that actually happened or a
# direct variant of one. The shared shape of the bug class: FRED does the
# FIRST half of a request, narrates it confidently, and silently drops
# the rest. It sounds like success, which is why these went unnoticed
# until Vatsal happened to check.
#
# The three defences being exercised:
#   1. intent.looks_compound   — is there more than one thing here?
#   2. intent.classify         — are BOTH halves' tools even on the menu?
#   3. _generate_with_tools    — does the loop get another round, with
#                                the original goal restated?
#
# (2) matters more than it looks: the tool menu is computed once, before
# round 1, and reused for every later round. A tool missing from it is
# unreachable for the whole turn no matter how many rounds remain.

import pytest

from orchestrator import intent
from orchestrator.orchestrator import FREDOrchestrator


class _FakeLLM:
    """Scripted replies, one per generate_with_tools call."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0
        self.seen_messages = []

    def generate_with_tools(self, messages, tools, local_only=False):
        self.seen_messages.append(list(messages))
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return reply

    def generate(self, messages, local_only=False, **kwargs):
        return "plain reply"


class _FakeTools:
    def __init__(self, destructive=()):
        self._destructive = set(destructive)

    def is_destructive(self, name):
        return name in self._destructive

    def get_tool_definitions(self, only=None):
        return []

    def list_tools(self):
        return [
            "list_scheduled", "schedule_reminder", "schedule_recurring",
            "launch_application", "open_website", "set_volume",
            "todays_workout", "schedule_workouts", "add_task", "list_tasks",
        ]


def _bare_orchestrator(llm, tools=None):
    orch = FREDOrchestrator.__new__(FREDOrchestrator)
    orch.llm = llm
    orch.tools = tools or _FakeTools()
    orch._executed = []
    orch._execute_tool_call = lambda call: orch._executed.append(
        call["function"]["name"]
    ) or f"ran {call['function']['name']}"
    orch._last_tools_offered = None
    orch._last_routing_reason = None
    orch._router = "stub"
    return orch


def _tool_call(call_id, name, arguments="{}"):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _patch_intent(monkeypatch, tool_names, compound):
    monkeypatch.setattr(
        intent, "classify", lambda text, llm, router: (True, tool_names, "test")
    )
    monkeypatch.setattr(intent, "close_candidates", lambda *a, **k: None)
    monkeypatch.setattr(intent, "looks_compound", lambda text: compound)
    monkeypatch.setattr("orchestrator.orchestrator.TOOLS_ENABLED", True)


# ---------------------------------------------------------------
# looks_compound — the gate every other defence depends on
# ---------------------------------------------------------------

def test_real_transcripts_are_recognised_as_compound():
    """
    Each of these is a real utterance from a session log. If
    looks_compound misses one, the SELF_NARRATING_TOOLS shortcut in
    _generate_with_tools returns after the first tool and the rest of
    the request is discarded without a word.
    """
    for phrase in (
        # 2026-08-04: launched nothing, then opened only the website.
        "Open LM studio and go to cerebras.com",
        "I want you to open LM studio THEN proceed to open the cerebras website",
        # 2026-08-02: spoke the time, never answered the goals half.
        "What is the time and what are the goals for today?",
        # 2026-08-03: scheduled Wednesday, silently dropped Friday.
        "Set two reminders, on Wednesday and Friday, called live class, at 5:55 pm.",
        # Two actions, no question word and no weekday pair.
        "set the volume to 50 and open chrome",
        "mute yourself and tell me today's tasks",
    ):
        assert intent.looks_compound(phrase), phrase


def test_ordinary_single_requests_are_not_compound():
    """
    The cost of a false positive is a wasted extra LLM round on every
    such turn, so the tell has to stay narrow. "and" joining two NOUNS
    is not two requests.
    """
    for phrase in (
        "What is the time?",
        "add milk and eggs to the shopping list",
        "remind me on Monday",
        "what's my split",
        "open spotify",
    ):
        assert not intent.looks_compound(phrase), phrase


# ---------------------------------------------------------------
# The menu: both halves must be reachable
# ---------------------------------------------------------------

def test_compound_turn_keeps_the_full_cue_union():
    """
    Confirmed design bug: embedding narrowing ranks every candidate
    against the WHOLE utterance and keeps at most six. On a compound
    spanning two categories the second half's tool competes against the
    first half's wording and can fall off the list — and since the menu
    is frozen before round 1, it is then unreachable for the entire
    turn.

    A router that ranks the schedule tools first must NOT be able to
    drop launch_application from a turn that plainly asks to open
    something.
    """
    class _RouterThatPrefersSchedule:
        def rank(self, text):
            return [
                ("schedule_reminder", 0.9), ("list_scheduled", 0.88),
                ("set_timer", 0.87), ("cancel_scheduled", 0.86),
                ("schedule_file_watch", 0.85), ("schedule_recurring", 0.84),
                ("launch_application", 0.10),
            ]

        def route(self, text, top_k=5, floor=0.0, margin=0.06):
            return ["schedule_reminder"], 0.9

    needs_tools, names, reason = intent.classify(
        "set a reminder for 6pm and open Spotify",
        llm=None,
        router=_RouterThatPrefersSchedule(),
    )

    assert needs_tools
    assert "launch_application" in names, (
        f"the open-an-app half was dropped from the menu ({reason})"
    )
    assert "schedule_reminder" in names


# ---------------------------------------------------------------
# The loop: a forgotten half gets a second chance
# ---------------------------------------------------------------

def test_forgotten_half_is_recovered_in_a_later_round(monkeypatch):
    """
    The model asks for one tool, forgets the other, then remembers once
    the first result is in context. Both must end up executed, and the
    final spoken reply must be the model's — not a bare concatenation
    of tool output.
    """
    _patch_intent(monkeypatch, ["apps", "schedule"], compound=True)

    llm = _FakeLLM([
        {"content": None, "tool_calls": [_tool_call("1", "launch_application")]},
        {"content": None, "tool_calls": [_tool_call("2", "open_website")]},
        {"content": "Opened LM Studio and the Cerebras site.", "tool_calls": None},
    ])
    orch = _bare_orchestrator(llm)

    reply = orch._generate_with_tools(
        [{"role": "user", "content": "Open LM studio and go to cerebras.com"}]
    )

    assert orch._executed == ["launch_application", "open_website"]
    assert reply == "Opened LM Studio and the Cerebras site."


def test_compound_turn_is_reminded_of_the_original_request(monkeypatch):
    """
    By the time the loop re-asks, the model is looking at its own tool
    call and a result — several messages past what was actually asked.
    Nothing in that recent context says a second half exists, so it
    tends to summarise what it just did and stop. The loop restates the
    goal; this asserts that restatement actually reaches the model.
    """
    _patch_intent(monkeypatch, ["apps"], compound=True)

    llm = _FakeLLM([
        {"content": None, "tool_calls": [_tool_call("1", "launch_application")]},
        {"content": "Done.", "tool_calls": None},
    ])
    orch = _bare_orchestrator(llm)

    utterance = "Open LM studio and go to cerebras.com"
    orch._generate_with_tools([{"role": "user", "content": utterance}])

    second_round = llm.seen_messages[1]
    restatements = [
        m for m in second_round
        if m.get("role") == "user" and utterance in (m.get("content") or "")
        and "not been done" in (m.get("content") or "")
    ]
    assert restatements, "the loop never restated the goal before re-asking"


def test_simple_turn_is_not_nudged(monkeypatch):
    """
    The nudge is compound-only on purpose. On a single-action turn,
    telling the model "call the tool for anything not done yet" is an
    invitation to invent extra work.
    """
    _patch_intent(monkeypatch, ["apps"], compound=False)

    llm = _FakeLLM([
        # Not self-narrating, so the loop still re-asks for phrasing.
        {"content": None, "tool_calls": [_tool_call("1", "launch_application")]},
        {"content": "Opened it.", "tool_calls": None},
    ])
    orch = _bare_orchestrator(llm)
    orch._generate_with_tools([{"role": "user", "content": "open spotify"}])

    nudges = [
        m for round_msgs in llm.seen_messages for m in round_msgs
        if "not been done" in (m.get("content") or "")
    ]
    assert not nudges


def test_parallel_tool_calls_in_one_response_all_execute(monkeypatch):
    """
    A capable model may batch both calls into a single response rather
    than taking two rounds. Every call in the batch must run, in order —
    this is the good path, and it must not regress while the
    forgot-then-remembered path is being fixed.
    """
    _patch_intent(monkeypatch, ["apps"], compound=True)

    llm = _FakeLLM([
        {"content": None, "tool_calls": [
            _tool_call("1", "launch_application"),
            _tool_call("2", "open_website"),
        ]},
        {"content": "Both done.", "tool_calls": None},
    ])
    orch = _bare_orchestrator(llm)

    reply = orch._generate_with_tools(
        [{"role": "user", "content": "Open LM studio and go to cerebras.com"}]
    )

    assert orch._executed == ["launch_application", "open_website"]
    assert reply == "Both done."


def test_self_narrating_shortcut_still_fires_on_a_simple_turn(monkeypatch):
    """
    The shortcut exists to skip a pointless second LLM call when the
    tool's own return string is already the whole answer. Broadening
    looks_compound must not have destroyed that — a plain "what time is
    it" should still cost exactly one LLM call.
    """
    _patch_intent(monkeypatch, ["time"], compound=False)

    llm = _FakeLLM([
        {"content": None, "tool_calls": [_tool_call("1", "get_current_time")]},
    ])
    orch = _bare_orchestrator(llm)

    reply = orch._generate_with_tools(
        [{"role": "user", "content": "what time is it"}]
    )

    assert llm.calls == 1, "took a second LLM round on a self-narrating turn"
    assert reply == "ran get_current_time"


def test_round_budget_is_not_infinite(monkeypatch):
    """
    A model that keeps asking for tools forever must terminate with
    whatever actually got done, rather than looping until something
    else times out.
    """
    _patch_intent(monkeypatch, ["apps"], compound=True)

    # Always asks for another tool, never gives a final answer.
    llm = _FakeLLM([
        {"content": None, "tool_calls": [_tool_call("x", "launch_application")]},
    ])
    orch = _bare_orchestrator(llm)

    reply = orch._generate_with_tools(
        [{"role": "user", "content": "open one thing and then another"}]
    )

    from orchestrator.orchestrator import MAX_TOOL_ROUNDS
    assert len(orch._executed) == MAX_TOOL_ROUNDS
    assert "ran launch_application" in reply


def test_destructive_tool_in_a_batch_asks_before_running_anything(monkeypatch):
    """
    The confirmation gate must fire before ANY call in the batch runs,
    including the safe ones — a half-executed turn awaiting a yes/no is
    far harder to reason about than one that has done nothing yet.
    """
    _patch_intent(monkeypatch, ["apps", "processes"], compound=True)

    llm = _FakeLLM([
        {"content": None, "tool_calls": [
            _tool_call("1", "launch_application"),
            _tool_call("2", "kill_process", '{"name": "chrome"}'),
        ]},
    ])
    orch = _bare_orchestrator(llm, tools=_FakeTools(destructive={"kill_process"}))
    orch._request_confirmation = lambda name, args: f"confirm {name}?"

    reply = orch._generate_with_tools(
        [{"role": "user", "content": "open spotify and kill chrome"}]
    )

    assert orch._executed == [], "ran a tool before the confirmation was answered"
    assert reply == "confirm kill_process?"
