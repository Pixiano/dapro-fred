# Core/tools/assist_tools.py
#
# Additions chosen for what actually comes up day to day on this machine:
# quick maths, laptop status, music control, jotting things down, poking
# around folders, connectivity, and power.
#
# Every function returns a short, speakable string. These get read aloud,
# so no tables, no markdown, no paths longer than they need to be.

import ast
import os
import operator
import re
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

# Where loose files go when no directory is given. Anything else lands
# wherever the process happened to start, which for a background app is
# effectively random.
DEFAULT_DOCS = Path(os.path.expanduser("~")) / "Documents" / "FRED"


def _ensure_docs() -> Path:
    DEFAULT_DOCS.mkdir(parents=True, exist_ok=True)
    return DEFAULT_DOCS


# Folder names that belong to the user's home, not to FRED's own folder.
# Without this, the model saying "Documents/FRED" got it anchored under
# Documents/FRED again, producing Documents\FRED\Documents\FRED.
_HOME_FOLDERS = {
    "documents", "downloads", "desktop", "pictures", "music", "videos",
    "onedrive", "appdata",
}


def resolve_user_path(raw: str, default_dir: Path = None) -> Path:
    """
    Turn a spoken path into a real one.

    A bare name like "homework" is anchored to Documents/FRED rather than
    the working directory, expanding ~ and %VARS% on the way. Relative
    paths from a voice assistant are a trap: the process runs detached, so
    "notes.txt" would otherwise land next to whatever launched it.

    Two exceptions keep the anchoring from compounding:
      - a path starting with a real home folder ("Downloads/x") resolves
        against the home directory
      - a path already starting with FRED's own folder name is taken as
        relative to its parent, so it isn't nested inside itself
    """
    text = os.path.expandvars(os.path.expanduser(str(raw).strip().strip('"')))
    path = Path(text)

    if path.is_absolute():
        return path

    parts = [p for p in path.parts if p not in (".", "")]
    if not parts:
        return default_dir or _ensure_docs()

    first = parts[0].lower()
    home = Path(os.path.expanduser("~"))

    if first in _HOME_FOLDERS:
        return home.joinpath(*parts)

    if first == DEFAULT_DOCS.name.lower():
        return DEFAULT_DOCS.parent.joinpath(*parts)

    base = default_dir or _ensure_docs()
    return base.joinpath(*parts)


# =========================================================
# MATHS
# =========================================================

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}

_FUNCS = {
    "sqrt": lambda x: x ** 0.5, "abs": abs, "round": round,
    "min": min, "max": max, "sum": sum, "int": int, "float": float,
}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("only numbers")
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS.get(node.func.id)
        if not fn:
            raise ValueError(f"unknown function {node.func.id}")
        return fn(*[_eval_node(a) for a in node.args])
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval_node(e) for e in node.elts]
    raise ValueError("unsupported expression")


def calculate(expression: str) -> str:
    """
    Evaluate an arithmetic expression exactly.

    Exists because a 4B model does arithmetic by pattern, not by
    calculating — it answered a bat-and-ball question wrong until forced
    to reason, and longer sums are worse. Delegating to Python makes the
    answer correct by construction.

    Parsed via ast with an explicit operator whitelist, never eval() —
    the input reaches here from speech through an LLM, so it must not be
    able to execute anything.
    """
    text = str(expression or "").strip()
    if not text:
        return "Give me something to calculate."

    # "17% of 300" and "17 percent of 300" -> 17/100*300. Handled before
    # the bare-% rule below, which would otherwise leave a stray "of".
    text = re.sub(
        r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+",
        r"(\1/100)*",
        text,
        flags=re.IGNORECASE,
    )

    # Spoken forms the model tends to pass through verbatim.
    for word, symbol in (
        ("plus", "+"), ("minus", "-"), ("times", "*"),
        ("multiplied by", "*"), ("divided by", "/"),
        ("^", "**"), ("x", "*"),
    ):
        if word.isalpha() or " " in word:
            text = text.replace(f" {word} ", f" {symbol} ")
        else:
            text = text.replace(word, symbol)
    text = text.replace("%", "/100").rstrip("=").strip()

    try:
        value = _eval_node(ast.parse(text, mode="eval").body)
    except Exception:
        return f"I couldn't read \"{expression}\" as a calculation."

    if isinstance(value, float):
        # Trimmed for speech — "fourteen point two nine" is an answer,
        # ten decimal places read aloud is noise.
        rounded = round(value, 4)
        value = int(rounded) if rounded == int(rounded) else rounded
    return f"{expression} = {value}"


# =========================================================
# MACHINE STATUS
# =========================================================

def get_system_status() -> str:
    """Battery, CPU, memory, disk and uptime in one spoken line."""
    import psutil

    parts = []

    battery = None
    try:
        battery = psutil.sensors_battery()
    except Exception:
        pass
    if battery is not None:
        state = "charging" if battery.power_plugged else "on battery"
        parts.append(f"Battery {int(battery.percent)}% ({state})")
        if not battery.power_plugged and battery.secsleft and battery.secsleft > 0:
            parts.append(f"~{battery.secsleft // 60} min left")

    parts.append(f"CPU {psutil.cpu_percent(interval=0.3):.0f}%")

    memory = psutil.virtual_memory()
    parts.append(
        f"RAM {memory.percent:.0f}% "
        f"({memory.used / 2**30:.1f} of {memory.total / 2**30:.1f} GB)"
    )

    try:
        disk = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        parts.append(f"Disk {disk.free / 2**30:.0f} GB free")
    except Exception:
        pass

    try:
        uptime = timedelta(seconds=int(time.time() - psutil.boot_time()))
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        parts.append(f"up {hours}h {remainder // 60}m")
    except Exception:
        pass

    return ". ".join(parts) + "."


def get_network_status() -> str:
    """Whether the machine is online, plus the current Wi-Fi network."""
    online = False
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2.5):
            online = True
    except OSError:
        online = False

    if not online:
        return "No internet connection."

    parts = ["Online"]

    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ssid, signal = None, None
        for line in result.stdout.splitlines():
            clean = line.strip()
            # "BSSID" also starts with SSID, so require the exact key.
            if clean.lower().startswith("ssid") and ":" in clean and not clean.lower().startswith("bssid"):
                ssid = clean.split(":", 1)[1].strip()
            elif clean.lower().startswith("signal") and ":" in clean:
                signal = clean.split(":", 1)[1].strip()
        if ssid:
            parts.append(f"on {ssid}" + (f" ({signal} signal)" if signal else ""))
    except Exception:
        pass

    try:
        parts.append(f"local IP {socket.gethostbyname(socket.gethostname())}")
    except Exception:
        pass

    return ", ".join(parts) + "."


# =========================================================
# MEDIA
# =========================================================

# Virtual-key codes for the media keys every player honours, so this
# works with Spotify, YouTube in a browser, VLC, anything.
_MEDIA_KEYS = {
    "play": 0xB3, "pause": 0xB3, "playpause": 0xB3, "toggle": 0xB3,
    "next": 0xB0, "skip": 0xB0, "forward": 0xB0,
    "previous": 0xB1, "prev": 0xB1, "back": 0xB1,
    "stop": 0xB2,
}


def media_control(action: str = "playpause") -> str:
    """Send a media key: play/pause, next, previous or stop."""
    import ctypes

    key = _MEDIA_KEYS.get(str(action or "").strip().lower().replace(" ", ""))
    if key is None:
        return (
            f"'{action}' isn't a media action I know. "
            "Try play, pause, next, previous or stop."
        )

    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)

    spoken = {0xB3: "Toggled playback", 0xB0: "Skipped forward",
              0xB1: "Went back", 0xB2: "Stopped playback"}
    return spoken.get(key, "Done") + "."


# =========================================================
# POWER
# =========================================================

def power_action(action: str) -> str:
    """Lock, sleep, restart or shut down. Destructive — confirmed first."""
    verb = str(action or "").strip().lower()

    if verb in ("lock", "lock screen"):
        ctypes_ok = os.system("rundll32.exe user32.dll,LockWorkStation") == 0
        return "Locked." if ctypes_ok else "Couldn't lock the screen."

    commands = {
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "hibernate": ["shutdown", "/h"],
        "restart": ["shutdown", "/r", "/t", "5"],
        "reboot": ["shutdown", "/r", "/t", "5"],
        "shutdown": ["shutdown", "/s", "/t", "5"],
        "power off": ["shutdown", "/s", "/t", "5"],
        "cancel": ["shutdown", "/a"],
    }

    command = commands.get(verb)
    if not command:
        return (
            f"'{action}' isn't a power action I know. "
            "Try lock, sleep, restart, shutdown, or cancel."
        )

    try:
        subprocess.run(
            command, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        return f"Couldn't {verb}: {e}"

    if verb == "cancel":
        return "Cancelled the pending shutdown."
    # The 5s delay above is deliberate: it leaves a window to say
    # "cancel that" before anything actually happens.
    return f"{verb.capitalize()} in 5 seconds — say cancel shutdown to stop it."


# =========================================================
# FILES
# =========================================================

def append_to_file(filename: str, text: str) -> str:
    """
    Add a line to a file, creating it if needed. This is the quick-capture
    path — "add milk to the shopping list" shouldn't require reading the
    file, editing it and writing it back.
    """
    path = resolve_user_path(filename)
    if not path.suffix:
        path = path.with_suffix(".txt")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.exists() and path.stat().st_size > 0
        with open(path, "a", encoding="utf-8") as f:
            if existing:
                f.write("\n")
            f.write(str(text))
    except OSError as e:
        return f"Couldn't write to {path.name}: {e}"

    return f"Added to {path.name}: \"{text}\""


def list_directory(directory: str = "", limit: int = 40) -> str:
    """What's in a folder — folders first, then files, with sizes."""
    path = resolve_user_path(directory) if directory else _ensure_docs()

    if not path.exists():
        return f"No such folder: {path}"
    if not path.is_dir():
        return f"{path.name} is a file, not a folder."

    try:
        entries = sorted(
            path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
        )
    except OSError as e:
        return f"Couldn't read {path}: {e}"

    if not entries:
        return f"{path} is empty."

    lines = []
    for entry in entries[: int(limit)]:
        if entry.is_dir():
            lines.append(f"- {entry.name}/")
        else:
            try:
                size = entry.stat().st_size
                lines.append(f"- {entry.name} ({size / 1024:.0f} KB)")
            except OSError:
                lines.append(f"- {entry.name}")

    header = f"{len(entries)} item(s) in {path}:"
    if len(entries) > int(limit):
        header += f" (first {limit})"
    return header + "\n" + "\n".join(lines)


def open_path(path: str) -> str:
    """
    Open a file or folder with whatever Windows normally uses for it.
    Distinct from launch_application (which starts a program) and
    open_website (which needs a URL).
    """
    target = resolve_user_path(path)

    if not target.exists():
        return f"Couldn't find {target}."

    try:
        os.startfile(str(target))
    except Exception as e:
        return f"Couldn't open {target.name}: {e}"

    return f"Opened {target.name}."


# =========================================================
# TIMERS
# =========================================================

def format_duration(minutes: float) -> str:
    total = int(round(float(minutes) * 60))
    hours, remainder = divmod(total, 3600)
    mins, secs = divmod(remainder, 60)
    bits = []
    if hours:
        bits.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        bits.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if secs and not hours:
        bits.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(bits) or "0 minutes"
