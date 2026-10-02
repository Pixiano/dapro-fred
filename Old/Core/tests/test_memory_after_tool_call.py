# Core/tests/test_memory_after_tool_call.py
#
# Confirmed against the real persisted memory file (Core/data/memory/
# default_user.jsonl): several past "What's the weather?" turns have a
# stored user entry with NO matching assistant entry anywhere near it —
# the tool's actual answer never made it into long-term memory
# (2026-08-09, two on 08-10, 08-12). process()'s current code stores
# both sides unconditionally at the end of every path, so today's live
# weather/lockdown exchanges ARE both stored correctly — this pins that
# staying true, specifically for a reply that came from a tool (the
# dispatcher fast path and the LLM tool-calling path), not just plain
# chat. "Ask FRED to check the weather and it should actually remember
# what the weather was" is the concrete case this exists for.
#
# FREDOrchestrator.__new__ skips __init__ (no model/memory loading —
# same pattern test_no_fake_completion.py already uses), so this is
# fast and offline like the rest of this folder.

import types

from orchestrator.orchestrator import FREDOrchestrator
from orchestrator.dispatcher import Dispatcher
from state.conversation_state import ConversationState


def _fred(dispatch_reply=None, llm_reply=None):
    fred = FREDOrchestrator.__new__(FREDOrchestrator)
    fred.state = ConversationState()
    fred.dispatcher = Dispatcher()  # real — no model, just regex
    fred.pending_action = None

    stored = []
    fred.memory = types.SimpleNamespace(
        store=lambda role, content: stored.append((role, content))
    )

    fred._run_or_confirm = lambda tool, args: dispatch_reply
    fred._process_with_llm = lambda text: llm_reply

    return fred, stored


def test_dispatcher_tool_reply_is_stored_to_memory():
    """The literal weather case: "what's the weather" is dispatcher-
    routed (no LLM), and get_weather's actual answer must be what lands
    in memory — not left un-stored just because no LLM touched it."""
    fred, stored = _fred(dispatch_reply="It's 30°C in Mumbai right now, with sunny.")

    reply = fred.process("what's the weather in Mumbai")

    assert reply == "It's 30°C in Mumbai right now, with sunny."
    assert ("user", "what's the weather in Mumbai") in stored
    assert ("assistant", "It's 30°C in Mumbai right now, with sunny.") in stored


def test_llm_tool_call_reply_is_also_stored_to_memory():
    """Same contract on the path that goes through the LLM tool-calling
    loop instead of the deterministic dispatcher — a request the
    dispatcher doesn't recognize must not fall through the storage
    step."""
    fred, stored = _fred(llm_reply="Tomorrow looks sunny, high of 29.")

    reply = fred.process("will it be nice out tomorrow")

    assert reply == "Tomorrow looks sunny, high of 29."
    assert ("assistant", "Tomorrow looks sunny, high of 29.") in stored


def test_both_sides_stored_together_not_just_the_question():
    """Pins the exact shape of the historical gap found in the real
    memory file: a user turn present with no assistant turn beside it.
    Both calls must happen, in order, every time."""
    fred, stored = _fred(dispatch_reply="Lockdown engaged, sir.")

    fred.process("lockdown")

    roles = [role for role, _ in stored]
    assert roles == ["user", "assistant"], stored
