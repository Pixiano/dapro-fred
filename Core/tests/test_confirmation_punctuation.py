# Core/tests/test_confirmation_punctuation.py
#
# Regression: a spoken "Yes." cancelled the action it was confirming.
#
# _handle_pending_confirmation used to compare the raw input against a set
# of bare words, so anything Whisper punctuated ("Yes.", "Yeah!") missed
# and fell through to the cancel branch. Confirmed live 2026-08-15 on a
# call_phone confirmation — FRED answered a clear yes with "Cancelled —
# didn't run it." Every destructive tool was affected.

import types

from orchestrator.orchestrator import FREDOrchestrator


def _fake(monkeypatch, ran):
    fred = FREDOrchestrator.__new__(FREDOrchestrator)
    fred.pending_action = {"tool": "call_phone", "arguments": {"number": "mom"}}
    fred.pending_chain = []
    fred.llm = None
    fred.last_turn_id = "t"
    fred._turn_utterance = ""
    fred.tools = types.SimpleNamespace(
        execute=lambda name, **kw: ran.append((name, kw)) or "Calling now.",
    )

    import orchestrator.orchestrator as module
    monkeypatch.setattr(module.tool_call_log, "log_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(module.event_log, "log", lambda *a, **k: None)
    return fred


def test_punctuated_yes_runs_the_action(monkeypatch):
    for spoken in ("Yes.", "yes", "Yeah!", "Sure.", "go ahead", "Okay.", "y"):
        ran = []
        fred = _fake(monkeypatch, ran)
        reply = fred._handle_pending_confirmation(spoken)
        assert ran == [("call_phone", {"number": "mom"})], f"{spoken!r} did not run"
        assert "Cancelled" not in reply, f"{spoken!r} cancelled"


def test_negatives_still_cancel(monkeypatch):
    # The other half of the fix: widening the affirmative match must not
    # start treating a refusal as consent. Placing a call on "no" is a
    # far worse failure than the bug this file exists for.
    for spoken in ("No.", "no", "nope", "don't", "cancel", "stop"):
        ran = []
        fred = _fake(monkeypatch, ran)
        reply = fred._handle_pending_confirmation(spoken)
        assert ran == [], f"{spoken!r} ran the action"
        assert "Cancelled" in reply, f"{spoken!r} did not cancel"
