"""Tools: the speaker volume (system level) and system status.

Sockets
  in : register(registry) adds get_volume, set_volume, adjust_volume, get_system_status
  out: spoken-style strings (say=True); every Windows/COM failure becomes ToolError(unavailable)
FRED's own voice mute is not here: it belongs to the voice output (P4), not to the system mixer.
COM (pycaw) is initialised per call on the worker thread, as the registry runs each tool in its own thread.
Self-check: python tools/audio_dev.py (pure logic; the real mixer is touched only by tests/live_tools.py)
"""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.registry import ToolError, ToolSpec

STEPS = {"small": 10, "normal": 20, "large": 40}


def _endpoint():
    try:
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetSpeakers().EndpointVolume
    except Exception as ex:
        raise ToolError("unavailable", "I can't reach the speakers right now.", f"audio endpoint: {ex!r}")


def next_level(current: int, direction: str, amount: str = "normal") -> int:
    step = STEPS.get(str(amount).lower(), STEPS["normal"])
    return max(0, min(100, current + (-step if str(direction).lower().startswith("d") else step)))


def get_volume() -> str:
    return f"Volume is at {round(_endpoint().GetMasterVolumeLevelScalar() * 100)} percent."


def set_volume(level: int) -> str:
    _endpoint().SetMasterVolumeLevelScalar(level / 100, None)
    return f"Volume set to {level} percent."


def adjust_volume(direction: str, amount: str = "normal") -> str:
    ep = _endpoint()
    cur = round(ep.GetMasterVolumeLevelScalar() * 100)
    new = next_level(cur, direction, amount)
    if new == cur:
        return f"Volume is already at {cur} percent."
    ep.SetMasterVolumeLevelScalar(new / 100, None)
    return f"Volume {'up' if new > cur else 'down'} to {new} percent."


def get_system_status() -> str:
    import psutil
    b, d = psutil.sensors_battery(), psutil.disk_usage("C:\\")
    parts = [f"CPU {psutil.cpu_percent(interval=0.3):.0f} percent", f"memory {psutil.virtual_memory().percent:.0f} percent", f"disk {d.percent:.0f} percent full"]
    if b:
        parts.append(f"battery {b.percent:.0f} percent{', charging' if b.power_plugged else ''}")
    return ", ".join(parts).capitalize() + "."


def register(reg):
    cues = (r"volume", r"louder", r"quieter", r"turn (?:it )?(?:up|down)", r"too (?:loud|quiet)", r"how loud")
    reg.register(ToolSpec("get_volume", get_volume, description="Read the current speaker volume. Only reports the level; it does NOT change anything.", group="audio", say=True, cues=cues, examples=("how loud is it", "what's the volume at")))
    reg.register(ToolSpec("set_volume", set_volume, {"type": "object", "required": ["level"], "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}},
                          "Set the system volume to an exact percentage.", group="audio", say=True, cues=cues, examples=("set volume to forty percent", "volume fifty")))
    reg.register(ToolSpec("adjust_volume", adjust_volume, {"type": "object", "required": ["direction"], "properties": {
        "direction": {"type": "string", "enum": ["up", "down"]}, "amount": {"type": "string", "enum": ["small", "normal", "large"], "default": "normal"}}},
        "Change the speaker volume relative to where it is: use for 'turn it up/down', 'too loud', 'too quiet', 'a bit louder'. 'a bit' is small, 'a lot' is large.", group="audio", say=True, cues=cues,
        examples=("turn it up", "make it quieter", "a bit louder", "it's way too loud in here", "I can't hear anything, it's too quiet")))
    reg.register(ToolSpec("get_system_status", get_system_status, description="Report CPU, memory, disk and battery.", group="sysinfo", say=True,
                          cues=(r"battery", r"\bcpu\b", r"\bram\b", r"disk", r"system status", r"how(?:'s| is) (?:the|my) (?:pc|computer|machine)"),
                          examples=("how much battery do I have", "is my disk full", "how much RAM is being used")))


def _selfcheck():
    assert next_level(50, "up") == 70 and next_level(50, "down", "small") == 40 and next_level(95, "up", "large") == 100 and next_level(5, "down") == 0
    assert next_level(50, "Down", "huge") == 30
    from tools.registry import Registry
    r = Registry()
    register(r)
    assert {"get_volume", "set_volume", "adjust_volume", "get_system_status"} == set(r.names({"audio", "sysinfo"}))
    assert r.call("set_volume", {"level": 400}).error_kind == "bad_args" and r.call("adjust_volume", {"direction": "sideways"}).error_kind == "bad_args"
    s = r.call("get_system_status")
    assert s.ok and "memory" in s.data
    print("audio_dev ok")


if __name__ == "__main__":
    _selfcheck()
