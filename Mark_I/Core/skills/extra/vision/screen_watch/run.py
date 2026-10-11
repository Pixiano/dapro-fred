"""screen_watch: the voice side of the screen pause switch (proactive/gate.py). Add-on skill (skills/extra).

Sockets
  in : run(action)    pause | resume | status
  out: a spoken confirmation; pause writes the flag file <data_dir>/screen_paused.flag (the reader sees it within a second), resume removes it and clears config.screen_paused.
       Pausing stops the screen reading for the deadline watch (and the later screen memory); the scam watch keeps reading.
"""
from proactive import gate
from tools.registry import ToolError


def run(action: str = "status") -> str:
    if action == "pause":
        gate.set_paused(True)
        return "Okay, I've stopped reading your screen. The scam watch stays on."
    if action == "resume":
        gate.set_paused(False)
        return "Okay, I'm reading the screen again."
    if action == "status":
        return "I am not reading your screen for deadlines right now." if gate.paused() else "I am reading your screen for deadlines. The scam watch is always on."
    raise ToolError("bad_args", "Say pause or resume.", f"unknown action {action!r}")
