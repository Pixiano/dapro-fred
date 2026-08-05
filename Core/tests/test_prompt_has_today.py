# Core/tests/test_prompt_has_today.py
#
# 2026-08-05: asked to log that day's event, FRED wrote "2026-08-02"
# into a people/ file — nothing in SYSTEM_PROMPT, the vault or the tool
# menu ever told it what day it was, so it dated the entry from a date
# it had just read. The wrong date got persisted where it will later be
# believed. Every turn's system prompt must carry today.

from datetime import datetime

from orchestrator.orchestrator import FREDOrchestrator


def test_system_prompt_states_todays_date():
    o = FREDOrchestrator.__new__(FREDOrchestrator)
    o._screen_context = staticmethod(lambda: "")
    o._vault_router = lambda: None

    messages = o._build_messages(recent_messages=[], memories=[], user_input="hi")

    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert datetime.now().strftime("%Y-%m-%d") in system
    # The instruction matters as much as the date: the failure was
    # copying a date out of a file, not lacking one entirely.
    assert "never date an entry from a date you read" in system.lower()
