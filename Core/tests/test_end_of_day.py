# Core/tests/test_end_of_day.py
#
# The wind-down sequence: one confirmation per open window, then the
# recap, then the shutdown offer. Built on pending_action, so what's
# tested here is that the queue advances on yes AND on no, and that only
# an abort word ends it early.

import types

from orchestrator.orchestrator import FREDOrchestrator


def _fake(monkeypatch, titles, closed):
    """An orchestrator with just enough wired up to walk the chain."""
    fred = FREDOrchestrator.__new__(FREDOrchestrator)
    fred.pending_action = None
    fred.pending_chain = []
    fred.llm = None
    fred.last_turn_id = "t"
    fred._turn_utterance = ""
    fred.tools = types.SimpleNamespace(
        execute=lambda name, **kw: closed.append((name, kw)) or "done",
    )

    import orchestrator.orchestrator as module
    monkeypatch.setattr(module.machine_tools, "open_window_titles", lambda: titles)
    monkeypatch.setattr(module.session_summary, "summarise_today",
                        lambda llm=None: "You did three things.")
    monkeypatch.setattr(module.tool_call_log, "log_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(module.event_log, "log", lambda *a, **k: None)
    return fred


def test_asks_once_per_window_then_recaps_and_offers_shutdown(monkeypatch):
    closed = []
    fred = _fake(monkeypatch, ["Chrome", "VS Code"], closed)

    opening = fred.end_of_day()
    assert "Close Chrome?" in opening

    second = fred._handle_pending_confirmation("yes")
    assert "Close VS Code?" in second

    last = fred._handle_pending_confirmation("yes")
    assert "You did three things." in last
    assert "Shut the machine down?" in last

    assert [kw["title"] for _, kw in closed] == ["Chrome", "VS Code"]

    # The shutdown itself is the final confirmation, nothing queued after.
    fred._handle_pending_confirmation("yes")
    assert closed[-1] == ("power_action", {"action": "shutdown"})
    assert fred.pending_chain == []
    assert fred.pending_action is None


def test_no_skips_one_window_but_keeps_going(monkeypatch):
    closed = []
    fred = _fake(monkeypatch, ["Chrome", "VS Code"], closed)
    fred.end_of_day()

    nxt = fred._handle_pending_confirmation("no")
    assert "Close VS Code?" in nxt
    assert closed == []


def test_stop_ends_the_whole_sequence(monkeypatch):
    closed = []
    fred = _fake(monkeypatch, ["Chrome", "VS Code"], closed)
    fred.end_of_day()

    assert "Stopped" in fred._handle_pending_confirmation("stop")
    assert fred.pending_chain == []
    assert fred.pending_action is None
    assert closed == []


def test_nothing_open_still_recaps_and_offers_shutdown(monkeypatch):
    fred = _fake(monkeypatch, [], [])
    opening = fred.end_of_day()
    assert "You did three things." in opening
    assert "Shut the machine down?" in opening


def test_wind_down_phrases_route_before_the_kill_rule():
    # "end of day" starts with "end ", which the kill_process rule
    # otherwise claims as "kill the process called 'of day'".
    from orchestrator.dispatcher import Dispatcher

    dispatcher = Dispatcher()
    for phrase in ["end of day", "wind down", "I'm done for today",
                   "call it a day", "end of day, wind down"]:
        assert dispatcher.match(phrase) == {"tool": "end_of_day", "arguments": {}}, phrase

    assert dispatcher.match("end chrome")["tool"] == "kill_process"


def test_window_list_skips_shell_windows_the_hud_and_duplicates(monkeypatch):
    from tools import machine_tools

    class W:
        def __init__(self, title):
            self.title = title

    monkeypatch.setattr(machine_tools.gw, "getAllWindows", lambda: [
        W("Program Manager"), W("Spotify Premium"), W("  "),
        W("Spotify Premium"), W("FRED · HUD - Google Chrome"), W("Notepad"),
    ])
    assert machine_tools.open_window_titles() == ["Spotify Premium", "Notepad"]
