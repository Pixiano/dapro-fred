# Core/tests/test_end_of_day.py
#
# The wind-down sequence: ONE confirmation covers the whole thing —
# closing every open window a few seconds apart in the background, then
# shutting down. Rewritten 2026-08-18 (see orchestrator.py's end_of_day/
# _run_end_of_day docstrings): the old version asked yes/no once per
# window via pending_chain, which is what this file used to test. What's
# tested now: yes schedules the background close+shutdown sequence with
# the right titles/order, and either no or stop simply cancels the whole
# thing outright — there's no longer a multi-step chain to abort out of.

import types

from orchestrator.orchestrator import FREDOrchestrator


class _FakeAPScheduler:
    """Records add_job calls instead of actually scheduling anything."""

    def __init__(self):
        self.jobs = []

    def add_job(self, func, args=None, kwargs=None, **_ignored):
        self.jobs.append((func, args or [], kwargs or {}))


def _fake(monkeypatch, titles, closed):
    """An orchestrator with just enough wired up to run end_of_day."""
    fred = FREDOrchestrator.__new__(FREDOrchestrator)
    fred.pending_action = None
    fred.pending_chain = []
    fred.llm = None
    fred.last_turn_id = "t"
    fred._turn_utterance = ""
    fred.tools = types.SimpleNamespace(
        execute=lambda name, **kw: closed.append((name, kw)) or "done",
    )
    fake_aps = _FakeAPScheduler()
    fred.scheduler = types.SimpleNamespace(
        _scheduler=fake_aps,
        _next_job_id=lambda prefix: f"{prefix}_test",
    )

    import orchestrator.orchestrator as module
    monkeypatch.setattr(module.machine_tools, "open_window_titles", lambda: titles)
    monkeypatch.setattr(module.session_summary, "summarise_today",
                        lambda llm=None: "You did three things.")
    monkeypatch.setattr(module.tool_call_log, "log_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(module.event_log, "log", lambda *a, **k: None)
    return fred, fake_aps


def test_single_confirmation_schedules_close_sequence(monkeypatch):
    closed = []
    fred, aps = _fake(monkeypatch, ["Chrome", "VS Code"], closed)

    opening = fred.end_of_day()
    assert "2 window(s)" in opening
    assert "You did three things." in opening
    assert "Proceed? (yes/no)" in opening
    assert aps.jobs == []  # nothing scheduled until confirmed

    result = fred._handle_pending_confirmation("yes")
    assert "Closing them now." in result
    assert fred.pending_action is None

    # Two window-close jobs (in order) plus one shutdown job at the end.
    assert len(aps.jobs) == 3
    from orchestrator.orchestrator import _close_window_and_announce, _shutdown_and_announce
    assert aps.jobs[0] == (_close_window_and_announce, ["Chrome"], {})
    assert aps.jobs[1] == (_close_window_and_announce, ["VS Code"], {})
    assert aps.jobs[2] == (_shutdown_and_announce, [fred.tools], {})
    assert closed == []  # nothing actually run synchronously — all scheduled


def test_no_cancels_the_whole_sequence(monkeypatch):
    closed = []
    fred, aps = _fake(monkeypatch, ["Chrome", "VS Code"], closed)
    fred.end_of_day()

    result = fred._handle_pending_confirmation("no")
    assert "Cancelled" in result
    assert aps.jobs == []
    assert fred.pending_action is None


def test_stop_also_cancels_the_whole_sequence(monkeypatch):
    # No pending_chain to abort out of anymore — "stop" and "no" both
    # just decline the single confirmation the same way.
    closed = []
    fred, aps = _fake(monkeypatch, ["Chrome", "VS Code"], closed)
    fred.end_of_day()

    result = fred._handle_pending_confirmation("stop")
    assert "Cancelled" in result
    assert fred.pending_chain == []
    assert fred.pending_action is None
    assert aps.jobs == []
    assert closed == []


def test_nothing_open_still_recaps_and_offers_shutdown(monkeypatch):
    fred, _aps = _fake(monkeypatch, [], [])
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
