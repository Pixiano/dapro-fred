"""volume: the Windows master speaker volume and mute, one skill with an action (get, set, up, down, mute, unmute).

Sockets
  in : run(action, level=None, amount="normal")   set needs level (0 to 100, else bad_args); up/down step by 10 / 20 / 40 for small / normal / large and stop at 0 and 100
  out: a spoken sentence ("Volume is at 40 percent." / "Volume up to 60 percent." / "Muted."); every Windows or COM failure is ToolError(unavailable)
FRED's own voice mute belongs to the voice output (P4), not here. COM (pycaw) is initialised per call because the registry runs each tool in its own thread.
Pure logic is `next_level`; the real mixer is touched only when the skill actually runs.
"""
from tools.registry import ToolError

STEPS = {"small": 10, "normal": 20, "large": 40}


def next_level(current: int, direction: str, amount: str = "normal") -> int:
    step = STEPS.get(str(amount).lower(), STEPS["normal"])
    return max(0, min(100, current + (step if direction == "up" else -step)))


def _endpoint():
    try:
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetSpeakers().EndpointVolume
    except Exception as ex:
        raise ToolError("unavailable", "I can't reach the speakers right now.", f"audio endpoint: {ex!r}")


def run(action: str, level: int | None = None, amount: str = "normal") -> str:
    if action == "set" and level is None:
        raise ToolError("bad_args", "To what level?", "action 'set' needs level (0 to 100)")
    ep = _endpoint()
    try:
        cur = round(ep.GetMasterVolumeLevelScalar() * 100)
        if action == "get":
            return "The speakers are muted." if ep.GetMute() else f"Volume is at {cur} percent."
        if action in ("mute", "unmute"):
            ep.SetMute(1 if action == "mute" else 0, None)
            return "Muted." if action == "mute" else f"Unmuted, volume is at {cur} percent."
        new = level if action == "set" else next_level(cur, action, amount)
        if new == cur and action != "set":
            return f"Volume is already at {cur} percent."
        ep.SetMute(0, None)                                                          # asking for a level means wanting to hear it
        ep.SetMasterVolumeLevelScalar(new / 100, None)
        return f"Volume set to {new} percent." if action == "set" else f"Volume {action} to {new} percent."
    except ToolError:
        raise
    except Exception as ex:
        raise ToolError("unavailable", "I can't reach the speakers right now.", f"audio endpoint: {ex!r}")
