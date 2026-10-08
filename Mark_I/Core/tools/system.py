"""Tools: screen brightness, media keys, clipboard, processes, screenshot, network status, lock/power.

Sockets
  in : register(registry) adds get_brightness, set_brightness, adjust_brightness, media_control, get_clipboard, set_clipboard, list_processes, kill_process (destructive, previewed),
       take_screenshot, get_network_status, lock_screen, power_action (destructive: sleep/hibernate/restart/shutdown/cancel)
  out: spoken-style strings; Windows failures become ToolError(unavailable | failed); nothing here raises past the registry
Guards: kill_process never touches protected processes (system and shell processes, and FRED's own playground / model servers / python); its confirmation names the real targets
(count per name) before anything dies. The clipboard is read-only-capped (600 chars spoken); screenshots go to ~/Pictures/FRED_screenshots. Shutdown/restart carry a countdown
(`cancel` stops it) so a mistaken yes is recoverable.
Self-check: python tools/system.py (pure logic; real effects only in tests/live_tools.py)
"""
import ctypes
import socket
import subprocess
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audio_dev import STEPS
from tools.registry import ToolError, ToolSpec

PROTECTED = {"system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe", "explorer.exe", "dwm.exe",
             "fontdrvhost.exe", "sihost.exe", "taskhostw.exe", "python.exe", "pythonw.exe", "py.exe", "llama-server.exe", "ollama.exe", "claude.exe", "cmd.exe", "powershell.exe", "conhost.exe", "wt.exe"}
MEDIA_KEYS = {"playpause": 0xB3, "play": 0xB3, "pause": 0xB3, "toggle": 0xB3, "next": 0xB0, "skip": 0xB0, "previous": 0xB1, "back": 0xB1, "stop": 0xB2}
SAID = {0xB3: "Toggled playback.", 0xB0: "Skipped to the next track.", 0xB1: "Went back a track.", 0xB2: "Stopped playback."}
SPEAK_CHARS = 600


def next_level(cur: int, direction: str, amount: str = "normal") -> int:
    s = STEPS.get(str(amount).lower(), STEPS["normal"])
    return max(0, min(100, cur + (-s if str(direction).lower().startswith("d") else s)))


def _sbc():
    try:
        import screen_brightness_control as sbc
        return sbc
    except Exception as ex:
        raise ToolError("unavailable", "I can't control the screen brightness here.", f"screen_brightness_control: {ex!r}")


def _level() -> int:
    try:
        v = _sbc().get_brightness()
        return int(v[0] if isinstance(v, list) else v)
    except ToolError:
        raise
    except Exception as ex:
        raise ToolError("unavailable", "This screen doesn't let me read its brightness.", f"get_brightness: {ex!r}")


def get_brightness() -> str:
    return f"Brightness is at {_level()} percent."


def set_brightness(level: int) -> str:
    try:
        _sbc().set_brightness(level)
    except ToolError:
        raise
    except Exception as ex:
        raise ToolError("unavailable", "This screen doesn't let me change its brightness.", f"set_brightness: {ex!r}")
    return f"Brightness set to {level} percent."


def adjust_brightness(direction: str, amount: str = "normal") -> str:
    cur = _level()
    new = next_level(cur, direction, amount)
    if new == cur:
        return f"Brightness is already at {cur} percent."
    set_brightness(new)
    return f"Brightness {'up' if new > cur else 'down'} to {new} percent."


def media_control(action: str = "playpause") -> str:
    key = MEDIA_KEYS.get(str(action).strip().lower().replace(" ", ""))
    if key is None:
        raise ToolError("bad_args", "I know play, pause, next, previous and stop.", f"unknown media action {action!r}")
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)
    return SAID[key]


def get_clipboard() -> str:
    import win32clipboard as wc
    try:
        wc.OpenClipboard()
        try:
            text = wc.GetClipboardData(wc.CF_UNICODETEXT) if wc.IsClipboardFormatAvailable(wc.CF_UNICODETEXT) else ""
        finally:
            wc.CloseClipboard()
    except Exception as ex:
        raise ToolError("unavailable", "I can't read the clipboard right now.", f"clipboard: {ex!r}")
    if not text:
        return "The clipboard is empty, or holds something that isn't text."
    return text[:SPEAK_CHARS] + (f" ... and {len(text) - SPEAK_CHARS} more characters." if len(text) > SPEAK_CHARS else "")


def set_clipboard(text: str) -> str:
    import win32clipboard as wc
    try:
        wc.OpenClipboard()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardText(text, wc.CF_UNICODETEXT)
        finally:
            wc.CloseClipboard()
    except Exception as ex:
        raise ToolError("unavailable", "I can't write to the clipboard right now.", f"clipboard: {ex!r}")
    return "Copied."


def _procs(target: str, rows=None) -> list:
    """(name, pid) of every process the target matches: a PID exactly, or a name substring. Protected processes are never returned."""
    import psutil
    t = str(target).strip().lower()
    if not t:
        return []
    out = []
    for name, pid in (rows if rows is not None else [(p.info["name"] or "", p.info["pid"]) for p in psutil.process_iter(["pid", "name"])]):
        if name.lower() in PROTECTED:
            continue
        if (t.isdigit() and int(t) == pid) or (not t.isdigit() and t in name.lower()):
            out.append((name, pid))
    return out


def list_processes(filter_name: str = "") -> str:
    import psutil
    f, counts = filter_name.lower().strip(), {}
    for p in psutil.process_iter(["name"]):
        n = p.info["name"] or ""
        if not f or f in n.lower():
            counts[n] = counts.get(n, 0) + 1
    rows = [f"{n} x{c}" if c > 1 else n for n, c in sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))[:40]]
    return "\n".join(rows) or (f"No processes match {filter_name}." if f else "No processes found.")


def preview_kill(args: dict) -> str:
    m = _procs(args.get("name_or_pid", ""))
    if not m:
        return f"{args.get('name_or_pid')} (nothing matches)"
    by = {}
    for n, _ in m:
        by[n] = by.get(n, 0) + 1
    return "kills " + ", ".join(f"{n} x{c}" if c > 1 else n for n, c in by.items())


def kill_process(name_or_pid: str) -> str:
    import psutil
    m = _procs(name_or_pid)
    if not m:
        raise ToolError("not_found", f"I can't find a process called {name_or_pid} that I'm allowed to stop.", f"no unprotected process matches {name_or_pid!r}")
    done = []
    for n, pid in m:
        try:
            psutil.Process(pid).kill()
            done.append(n)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            raise ToolError("denied", f"Windows won't let me stop {n}.", f"access denied killing {n} ({pid})")
    return f"Stopped {', '.join(sorted(set(done)))}." if done else "Those were already gone."


def take_screenshot() -> str:
    import mss
    import mss.tools
    d = Path.home() / "Pictures" / "FRED_screenshots"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    try:
        with mss.mss() as s:
            im = s.grab(s.monitors[0])
            mss.tools.to_png(im.rgb, im.size, output=str(f))
    except Exception as ex:
        raise ToolError("failed", "I couldn't capture the screen.", f"mss: {ex!r}")
    return f"Saved a screenshot as {f.name}."


def get_network_status() -> str:
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=2.5).close()
    except OSError:
        return "You're not connected to the internet right now."
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        ssid = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.strip().startswith("SSID") and "BSSID" not in l), "")
    except Exception:
        ssid = ""
    return f"You're online{(' on ' + ssid) if ssid else ''}."


def lock_screen() -> str:
    if not ctypes.windll.user32.LockWorkStation():
        raise ToolError("failed", "I couldn't lock the screen.", "LockWorkStation returned 0")
    return "Locked."


POWER = {"sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "hibernate": ["shutdown", "/h"], "restart": ["shutdown", "/r", "/t", "10"], "reboot": ["shutdown", "/r", "/t", "10"],
         "shutdown": ["shutdown", "/s", "/t", "10"], "cancel": ["shutdown", "/a"]}


def power_action(action: str) -> str:
    verb = str(action).strip().lower()
    cmd = POWER.get(verb)
    if cmd is None:
        raise ToolError("bad_args", "I can sleep, hibernate, restart, shut down, or cancel a pending shutdown.", f"unknown power action {action!r}")
    r = subprocess.run(cmd, capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode not in (0, None) and verb != "sleep":
        raise ToolError("failed", f"I couldn't {verb}.", f"{cmd[0]} exited {r.returncode}: {r.stderr[:100]!r}")
    return "Cancelled the pending shutdown." if verb == "cancel" else (f"{verb.capitalize()} in 10 seconds. Say cancel shutdown to stop it." if verb in ("restart", "reboot", "shutdown") else f"{verb.capitalize()}.")


def register(reg):
    N = {"type": "object", "properties": {}}
    bcues = (r"brightness", r"brighter", r"dimmer", r"dim (?:the|my) (?:screen|display)", r"screen (?:is )?(?:too )?(?:bright|dark)", r"less bright", r"more bright")
    reg.register(ToolSpec("get_brightness", get_brightness, description="Read the current screen brightness. Only reports; changes nothing.", group="brightness", say=True, cues=bcues, examples=("how bright is the screen",)))
    reg.register(ToolSpec("set_brightness", set_brightness, {"type": "object", "required": ["level"], "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}},
                          "Set the screen brightness to an exact percentage.", group="brightness", say=True, cues=bcues, examples=("set brightness to sixty percent",)))
    reg.register(ToolSpec("adjust_brightness", adjust_brightness, {"type": "object", "required": ["direction"], "properties": {"direction": {"type": "string", "enum": ["up", "down"]}, "amount": {"type": "string", "enum": ["small", "normal", "large"], "default": "normal"}}},
                          "Make the screen brighter or dimmer relative to now: 'make the screen less bright', 'a bit brighter'.", group="brightness", say=True, cues=bcues, examples=("make the screen less bright", "dim the display", "brighten my screen")))
    reg.register(ToolSpec("media_control", media_control, {"type": "object", "properties": {"action": {"type": "string", "enum": ["playpause", "next", "previous", "stop"], "default": "playpause"}}},
                          "Control media playback: play/pause, next, previous, stop. Use for 'pause the music', 'skip this song'.", group="media", say=True,
                          cues=(r"pause (?:the )?(?:music|song|video|playback)", r"play (?:the )?(?:music|next|previous)", r"skip", r"next (?:song|track)", r"previous (?:song|track)", r"resume", r"kill the music", r"stop (?:the )?(?:music|playback)"),
                          examples=("skip this song", "pause the music", "play the next track")))
    reg.register(ToolSpec("get_clipboard", get_clipboard, description="Read the text on the clipboard.", group="clipboard", reads_untrusted=True, cues=(r"clipboard", r"what did i copy", r"what i copied"), examples=("what did I copy", "what's on my clipboard")))
    reg.register(ToolSpec("set_clipboard", set_clipboard, {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}, "Put text on the clipboard.", group="clipboard", say=True,
                          cues=(r"clipboard", r"copy (?:this|that|it)"), examples=("copy this text", "put that on my clipboard")))
    reg.register(ToolSpec("list_processes", list_processes, {"type": "object", "properties": {"filter_name": {"type": "string"}}}, "List running processes (grouped by name), optionally filtered.", group="processes",
                          cues=(r"processes", r"what(?:'s| is) (?:using|eating|hogging)", r"using (?:all )?my (?:cpu|ram|memory)", r"eating (?:all )?my (?:cpu|ram|memory)", r"what(?:'s| is) running"), examples=("what's eating all my RAM", "show running processes")))
    reg.register(ToolSpec("kill_process", kill_process, {"type": "object", "required": ["name_or_pid"], "properties": {"name_or_pid": {"type": "string"}}},
                          "Force-stop a program by name or PID (unsaved work is lost; asks the user first).", group="processes", destructive=True, preview=preview_kill,
                          cues=(r"kill", r"force quit", r"end (?:that |the )?task", r"not responding", r"stopped responding", r"frozen", r"close (?:it|that|this)"), examples=("chrome is frozen kill it", "force quit that app", "this app is not responding")))
    reg.register(ToolSpec("take_screenshot", take_screenshot, description="Capture the whole screen to a PNG file.", group="screenshot", say=True, cues=(r"screenshot", r"capture (?:the|my) screen", r"grab a picture of my screen"), examples=("take a screenshot", "capture the screen")))
    reg.register(ToolSpec("get_network_status", get_network_status, description="Report whether the PC is online and on which Wi-Fi network.", group="network", say=True,
                          cues=(r"am i (?:even )?(?:online|connected)", r"internet", r"wi-?fi", r"connected to the internet", r"network"), examples=("am I online", "what wifi am I on", "is the internet working")))
    reg.register(ToolSpec("lock_screen", lock_screen, description="Lock the computer.", group="power", say=True, cues=(r"lock (?:my |the )?(?:pc|computer|screen|laptop|machine)", r"lock it"), examples=("lock my pc",)))
    reg.register(ToolSpec("power_action", power_action, {"type": "object", "required": ["action"], "properties": {"action": {"type": "string", "enum": ["sleep", "hibernate", "restart", "shutdown", "cancel"]}}},
                          "Sleep, hibernate, restart or shut down the computer, or cancel a pending shutdown (asks the user first, except cancel).", group="power", destructive=True, say=True,
                          cues=(r"shut ?down", r"restart", r"reboot", r"go to sleep", r"put (?:the |my )?(?:computer|pc|laptop|machine) to sleep", r"hibernate", r"power off", r"cancel (?:the )?shutdown"), examples=("shut down the computer", "restart the machine", "put the computer to sleep")))


def _selfcheck():
    assert next_level(50, "up") == 70 and next_level(5, "down", "large") == 0
    rows = [("chrome.exe", 10), ("chrome.exe", 11), ("Code.exe", 20), ("python.exe", 30), ("explorer.exe", 40), ("llama-server.exe", 50), ("notepad.exe", 60)]
    assert _procs("chrome", rows) == [("chrome.exe", 10), ("chrome.exe", 11)] and _procs("60", rows) == [("notepad.exe", 60)] and _procs("code", rows) == [("Code.exe", 20)]
    assert _procs("python", rows) == [] and _procs("explorer", rows) == [] and _procs("llama", rows) == [] and _procs("30", rows) == [] and _procs("", rows) == []      # protected: never offered, never killed
    assert "nothing matches" in preview_kill({"name_or_pid": "zzzz_no_such_proc"})
    for bad, fn in (("explode", media_control), ("selfdestruct", power_action)):
        try:
            fn(bad)
            raise AssertionError
        except ToolError as ex:
            assert ex.kind == "bad_args"
    from tools.registry import Registry
    r = Registry()
    register(r)
    assert {n for n in r.names() if r.spec(n).destructive} == {"kill_process", "power_action"} and r.spec("kill_process").preview
    assert r.call("kill_process", {"name_or_pid": "python"}).error_kind == "not_found" and r.call("list_processes", {"filter_name": "python"}).ok
    assert r.call("media_control", {"action": "sideways"}).error_kind == "bad_args" and r.call("set_brightness", {"level": 500}).error_kind == "bad_args"
    print("system ok")


if __name__ == "__main__":
    _selfcheck()
