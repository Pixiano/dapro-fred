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

from config.settings import VAULT_DIR

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

    # An existing vault-relative path wins over the Documents/FRED
    # anchor. The model asks for vault files by their vault-relative path
    # ("daily/2026-08/2026-08-04.md") because that is how MAP.md and the
    # retrieval labels present them — and every one of those landed under
    # Documents/FRED instead. Confirmed 2026-08-04: read_file answered
    # "File not found" for a daily note that existed, and append_to_file
    # silently CREATED Documents/FRED/daily/2026-08/2026-08-04.md and
    # reported success, so a note Vatsal dictated went to a phantom file
    # while the real one stayed untouched.
    #
    # Deliberately an exists() check on the literal path, not
    # vault_files.resolve_vault_file: that resolver does fuzzy substring
    # matching, which is right for "open my priorities" and badly wrong
    # here, where a brand-new "shopping-list.txt" must never be captured
    # by a vault file that happens to share a substring.
    vault_path = VAULT_DIR.joinpath(*parts)
    if vault_path.exists():
        return vault_path

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


# Phrase words per top-level AST operator, keyed off the actual parsed
# expression rather than guessed by regex — Python's ast already resolves
# operator precedence, so the root node of "3+4*5" is correctly Add even
# though the text starts by looking like Mult. Reusing that structure is
# more reliable than re-detecting it.
_OP_PHRASE = {
    ast.Add: "The sum of {l} and {r} is {v}.",
    ast.Sub: "{l} minus {r} is {v}.",
    ast.Mult: "The product of {l} and {r} is {v}.",
    ast.Div: "{l} divided by {r} is {v}.",
    ast.FloorDiv: "{l} divided by {r}, rounded down, is {v}.",
    ast.Mod: "{l} modulo {r} is {v}.",
    ast.Pow: "{l} to the power of {r} is {v}.",
}

# A rendered operand longer than this reads as a run-on clause rather than
# a number ("the sum of 3 + 4 * 5 - 2 and 7 is..."), so anything past it
# falls back to the plain answer instead of a broken-sounding sentence.
_MAX_OPERAND_CHARS = 20


def _describe_calculation(root, value, percent_of: tuple = None) -> str:
    """
    A spoken sentence for the result, or "" if the shape doesn't map to
    one cleanly — the caller falls back to a plain answer rather than
    forcing a phrase that would read worse than none.
    """
    if percent_of:
        pct, base = percent_of
        return f"{pct}% of {base} is {value}."

    if (
        isinstance(root, ast.Call)
        and isinstance(root.func, ast.Name)
        and root.func.id == "sqrt"
        and len(root.args) == 1
    ):
        arg = ast.unparse(root.args[0])
        if len(arg) <= _MAX_OPERAND_CHARS:
            return f"The square root of {arg} is {value}."
        return ""

    if isinstance(root, ast.BinOp) and type(root.op) in _OP_PHRASE:
        left, right = ast.unparse(root.left), ast.unparse(root.right)
        # Both sides must be plain numbers, not just short. "3+4*5"'s root
        # is Add(3, Mult(4,5)) — right unparses to "4 * 5", short enough
        # to pass a length check but still an operator symbol a TTS voice
        # would read literally. Rather than recurse into nested phrasing,
        # bail to the plain answer once either side isn't just a number.
        if (
            len(left) <= _MAX_OPERAND_CHARS and len(right) <= _MAX_OPERAND_CHARS
            and re.fullmatch(r"-?\d+(\.\d+)?", left)
            and re.fullmatch(r"-?\d+(\.\d+)?", right)
        ):
            return _OP_PHRASE[type(root.op)].format(l=left, r=right, v=value)

    return ""


def calculate(expression: str) -> str:
    """
    Evaluate an arithmetic expression exactly, and phrase the result as a
    sentence rather than an equation.

    Exists because a 4B model does arithmetic by pattern, not by
    calculating — it answered a bat-and-ball question wrong until forced
    to reason, and longer sums are worse. Delegating to Python makes the
    answer correct by construction; phrasing it here rather than handing
    "12 * 8 = 96" to the LLM for a second generation pass to turn into
    words means one deterministic tool call replaces two model calls, and
    the wording can never drift from the actual arithmetic.

    Parsed via ast with an explicit operator whitelist, never eval() —
    the input reaches here from speech through an LLM, so it must not be
    able to execute anything.
    """
    text = str(expression or "").strip()
    if not text:
        return "Give me something to calculate."

    # "17% of 300" and "17 percent of 300" -> 17/100*300. Captured before
    # rewriting so the phrase can still say "17% of 300", rather than
    # reporting the top-level op after rewrite, which would be Mult —
    # "the product of 0.17 and 300" is technically true and a strange
    # thing to hear in answer to "what's 17 percent of 300".
    percent_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    percent_of = (
        (percent_match.group(1), percent_match.group(2)) if percent_match else None
    )

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
        root = ast.parse(text, mode="eval").body
        value = _eval_node(root)
    except Exception:
        return f"I couldn't read \"{expression}\" as a calculation."

    if isinstance(value, float):
        # Trimmed for speech — "fourteen point two nine" is an answer,
        # ten decimal places read aloud is noise.
        rounded = round(value, 4)
        value = int(rounded) if rounded == int(rounded) else rounded

    return _describe_calculation(root, value, percent_of) or f"That comes out to {value}."


# =========================================================
# MACHINE STATUS
# =========================================================

def get_system_status() -> str:
    """Battery, CPU, memory, disk and uptime in one spoken line."""
    import psutil

    # Full sentences rather than "CPU 5%. RAM 62% (19.3 of 31.1 GB)." — the
    # label:value fragments read like a log line, not something said aloud.
    sentences = []

    battery = None
    try:
        battery = psutil.sensors_battery()
    except Exception:
        pass
    if battery is not None:
        pct = int(battery.percent)
        if battery.power_plugged:
            sentences.append(f"Your battery is at {pct}% and charging.")
        else:
            sentence = f"Your battery is at {pct}%."
            if battery.secsleft and battery.secsleft > 0:
                sentence = (
                    f"Your battery is at {pct}%, "
                    f"about {battery.secsleft // 60} minutes left."
                )
            sentences.append(sentence)

    cpu = psutil.cpu_percent(interval=0.3)
    memory = psutil.virtual_memory()
    sentences.append(
        f"CPU usage is at {cpu:.0f}%, and you're using {memory.percent:.0f}% "
        f"of your RAM — {memory.used / 2**30:.1f} of {memory.total / 2**30:.1f} gigabytes."
    )

    try:
        disk = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        sentences.append(f"You've got {disk.free / 2**30:.0f} gigabytes of disk space free.")
    except Exception:
        pass

    try:
        uptime = timedelta(seconds=int(time.time() - psutil.boot_time()))
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes = remainder // 60
        bits = ([f"{hours} hour{'s' if hours != 1 else ''}"] if hours else []) + (
            [f"{minutes} minute{'s' if minutes != 1 else ''}"] if minutes or not hours else []
        )
        sentences.append(f"The PC's been running for {' and '.join(bits)}.")
    except Exception:
        pass

    return " ".join(sentences)


def get_network_status() -> str:
    """Whether the machine is online, plus the current Wi-Fi network."""
    online = False
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2.5):
            online = True
    except OSError:
        online = False

    if not online:
        return "You're not connected to the internet right now."

    sentence = "You're online"

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
            sentence += f", connected to {ssid}"
            if signal:
                sentence += f" with {signal} signal"
    except Exception:
        pass

    sentence += "."

    try:
        sentence += f" Your local IP is {socket.gethostbyname(socket.gethostname())}."
    except Exception:
        pass

    parts = [sentence]

    return "".join(parts)


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

# The countdown the user gets to say "cancel shutdown" in. The end-of-day
# sequence hands the machine over to this same timer rather than exiting
# FRED first — FRED has to be alive to hear the cancellation.
SHUTDOWN_DELAY = 10


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
        "shutdown": ["shutdown", "/s", "/t", str(SHUTDOWN_DELAY)],
        "power off": ["shutdown", "/s", "/t", str(SHUTDOWN_DELAY)],
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
    # The delay above is deliberate: it leaves a window to say
    # "cancel that" before anything actually happens.
    delay = 5 if verb in ("restart", "reboot") else SHUTDOWN_DELAY
    return f"{verb.capitalize()} in {delay} seconds — say cancel shutdown to stop it."


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

    # Count both kinds up front. A truncated listing used to end
    # mid-sentence with no indication of what was cut — observed in
    # session_2026-08-02, where a 53-item Desktop was cut at 40 and the
    # visible remainder was all folders, so the files the user actually
    # asked about were the part that got dropped. Saying "31 folders and
    # 22 files" costs one line and tells the model (and the user)
    # exactly what it isn't seeing.
    folders = [e for e in entries if e.is_dir()]
    files = [e for e in entries if not e.is_dir()]

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

    header = (
        f"{len(entries)} item(s) in {path} "
        f"({len(folders)} folder(s), {len(files)} file(s)):"
    )
    if len(entries) > int(limit):
        header += f" showing the first {limit}"
    return header + "\n" + "\n".join(lines)


# Where a bare filename is actually likely to live. Ordered: the places
# a person drops a file they're about to talk about come first.
_COMMON_FILE_HOMES = ("Desktop", "Downloads", "Documents", "Pictures")


def _find_bare_filename(name: str):
    """
    Look for a bare filename in the obvious user folders (top level
    only — no recursive walk, this must stay instant).

    Needed because resolve_user_path anchors a bare name under
    Documents/FRED, which is right for files FRED creates and wrong for
    files that already exist somewhere else. Confirmed 2026-08-02:
    FRED found dossier.pdf on the Desktop, was asked to open it, and
    resolved it to Documents/FRED/dossier.pdf, which doesn't exist.
    """
    home = Path(os.path.expanduser("~"))
    for folder in _COMMON_FILE_HOMES:
        candidate = home / folder / name
        if candidate.exists():
            return candidate
    return None


def open_last_found(which: int = 1) -> str:
    """
    Open a file from the most recent search — what "open it" or "open
    that one" means right after FRED has reported finding something.

    `which` is 1-based to match how it gets said out loud ("open the
    second one"), not 0-based.

    Exists because search results are deliberately spoken without paths
    (persona.md forbids reading them aloud), which left the follow-up
    with nothing to name. The referent lives in found_cache instead of
    in conversation history, so it survives the phrasing pass and
    doesn't depend on the model having repeated the filename correctly.
    """
    from tools import found_cache

    paths = found_cache.get_last()

    if not paths:
        return "I don't have a recent search result to open."

    index = max(1, int(which)) - 1
    if index >= len(paths):
        return (
            f"There were only {len(paths)} result(s) — "
            f"I can't open number {int(which)}."
        )

    target = Path(paths[index])

    try:
        os.startfile(str(target))
    except Exception as e:
        return f"Couldn't open {target.name}: {e}"

    return f"Opened {target.name}."


def open_path(path: str) -> str:
    """
    Open a file or folder with whatever Windows normally uses for it.
    Distinct from launch_application (which starts a program) and
    open_website (which needs a URL).
    """
    target = resolve_user_path(path)

    # A bare filename that isn't where it was anchored is probably an
    # existing file elsewhere, not a missing one — see _find_bare_filename.
    if not target.exists() and not Path(path).is_absolute() and len(Path(path).parts) == 1:
        found = _find_bare_filename(Path(path).name)
        if found is not None:
            target = found

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
