# Core/tools/system_tools.py

import json
import os
import re
import shutil
import webbrowser
import subprocess
import winreg
from pathlib import Path
from datetime import datetime

from tools.assist_tools import resolve_user_path
from config.settings import DATA_DIR
from state import lockdown_log, lockdown_state


# =========================================================
# BROWSER TOOLS
# =========================================================

def open_website(url: str) -> str:
    """
    Open a website in the default browser.

    A scheme-less "google.com" is normalised to https:// first —
    webbrowser.open treats a bare host as a relative file path on
    Windows, which silently opened nothing at all.
    """

    target = str(url or "").strip()
    if not target:
        return "No website given."

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        target = "https://" + target.lstrip("/")

    webbrowser.open(target)

    return f"Opened {target}"


# =========================================================
# APPLICATION TOOLS
# =========================================================

# Friendly names people actually say -> a real executable name or a
# special "shell:" target Windows knows how to open. Resolving bare
# names like "chrome" or "file explorer" via PATH alone fails because
# they aren't on PATH (or aren't real exe names); these are handled
# explicitly so the common cases just work.
_APP_ALIASES = {
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "settings": "ms-settings:",
    "control panel": "control.exe",
    "spotify": "spotify.exe",
    "vscode": "code.exe",
    "vs code": "code.exe",
    "code": "code.exe",
    "obs": "obs64.exe",
    "obs studio": "obs64.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
}

# Self-healing on top of the hardcoded table above: whatever the Start
# Menu / search-root walk finds gets written here, so the next launch of
# the same name is a dict lookup instead of a directory walk — and apps
# with no hardcoded alias (LM Studio, anything vendor-custom) stop
# needing to be found the slow way more than once. Confirmed 2026-08-04:
# "LM Studio" isn't in _APP_ALIASES and has to walk Start Menu /
# LOCALAPPDATA every single time it's launched by name.
_LEARNED_ALIASES_PATH = DATA_DIR / "app_aliases.json"


def _load_learned_aliases() -> dict:
    try:
        return json.loads(_LEARNED_ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _learn_alias(key: str, resolved_path: str):
    aliases = _load_learned_aliases()
    aliases[key] = resolved_path
    try:
        _LEARNED_ALIASES_PATH.write_text(
            json.dumps(aliases, indent=2), encoding="utf-8"
        )
    except OSError:
        pass

# Where to hunt for an installed .exe when PATH / App Paths both miss.
#
# APPDATA (Roaming) matters as much as LOCALAPPDATA here — confirmed
# root cause of the "Spotify never launches" report: Spotify's desktop
# installer puts Spotify.exe under %APPDATA%\Spotify, not Program Files
# or %LOCALAPPDATA%, so it was invisible to every resolution step below
# until this was added.
_SEARCH_ROOTS = [
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    os.environ.get("LOCALAPPDATA", ""),
    os.environ.get("APPDATA", ""),
]

# Start Menu shortcut folders, per-user and all-users. A .lnk here can
# be handed straight to os.startfile — it resolves and launches exactly
# like double-clicking it in the Start Menu, so there's no need to
# decode the shortcut's actual target path.
_START_MENU_ROOTS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
]


def _resolve_from_app_paths(exe_name: str):
    """
    Look the executable up in Windows' App Paths registry — the same
    mechanism the Run dialog uses. Resolves installed apps (Chrome,
    etc.) that aren't on PATH. Returns a full path or None.
    """

    sub_key = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        f"\\{exe_name}"
    )

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub_key) as key:
                value, _ = winreg.QueryValueEx(key, None)
                if value and Path(value.strip('"')).exists():
                    return value.strip('"')
        except OSError:
            continue

    return None


def _resolve_from_start_menu(display_name: str):
    """
    Search Start Menu .lnk shortcuts by display name — resolves apps
    that install somewhere non-standard (per-user AppData, a
    vendor-specific folder, a UWP package) but that Windows always
    gives a Start Menu entry regardless of where they actually live.
    Returns the .lnk path itself (os.startfile handles it directly) or
    None.
    """

    name = display_name.lower()

    for root in _START_MENU_ROOTS:
        if not root or not root.exists():
            continue

        for lnk in root.rglob("*.lnk"):
            if name in lnk.stem.lower():
                return str(lnk)

    return None


def _resolve_from_search_roots(exe_name: str):
    """
    Last resort: walk common install directories looking for the exe.
    Returns a full path or None.
    """

    for root in _SEARCH_ROOTS:
        if not root or not os.path.isdir(root):
            continue

        for dirpath, _dirs, files in os.walk(root):
            if exe_name.lower() in (f.lower() for f in files):
                return os.path.join(dirpath, exe_name)

    return None


# Trailing punctuation/filler from STT transcription, not part of the
# app name. Confirmed root cause of every logged Spotify launch
# failure: "open Spotify" transcribes as "Spotify." — the trailing
# period survives into app_name, "Spotify." isn't in the alias table,
# and since it doesn't already end in ".exe" the code below appended
# one to the literal string, producing "Spotify..exe", which naturally
# resolves nowhere. "Spotify now." (same session) shows the same shape
# plus a trailing filler word.
_TRAILING_NOISE = re.compile(
    r"\s+(?:now|please|for me)\s*[.,!?]*$|[.,!?]+$", re.IGNORECASE
)


def _launch_failure_message(raw: str, e: Exception) -> str:
    """Distinguish *why* os.startfile failed once a real path was already
    found — a permission bounce and a corrupt/missing target need
    different fixes from the user, and both were previously collapsed
    into one generic 'Failed to launch' string."""
    if getattr(e, "winerror", None) == 5:
        return f"Found {raw} but don't have permission to launch it — try running FRED as administrator."
    return f"Found {raw} but launching it failed: {e}"


def launch_application(app_name: str) -> str:
    """
    Launch a desktop application by friendly name, resolving it via
    an alias map, then PATH, then Windows' App Paths registry, then a
    search of common install directories.
    """

    raw = _TRAILING_NOISE.sub("", app_name.strip()).strip()
    key = raw.lower()

    learned = _load_learned_aliases()
    target = _APP_ALIASES.get(key) or learned.get(key) or raw

    # shell:/ms-settings:/etc. protocol targets — let the shell open them.
    if target.endswith(":") or target.startswith(("shell:", "ms-")):
        try:
            os.startfile(target)
            return f"Launched {raw}"
        except OSError as e:
            return _launch_failure_message(raw, e)

    exe_name = target if target.lower().endswith(".exe") else f"{target}.exe"

    # 1. PATH
    resolved = shutil.which(target) or shutil.which(exe_name)

    # 2. App Paths registry
    if not resolved:
        resolved = _resolve_from_app_paths(exe_name)

    # 3. Start Menu shortcut, by display name — catches apps installed
    # somewhere neither of the above knows to look (Spotify's
    # %APPDATA% install, UWP packages, anything vendor-custom).
    learn = False
    if not resolved:
        resolved = _resolve_from_start_menu(raw)
        learn = resolved is not None

    # 4. common install dirs — last resort, a full directory walk. Only
    # these two slow paths get learned: PATH/App Paths hits are already
    # as fast as a lookup gets, nothing to cache.
    if not resolved:
        resolved = _resolve_from_search_roots(exe_name)
        learn = learn or resolved is not None

    if not resolved:
        return (
            f"Couldn't find '{raw}' on this PC — checked PATH, the App Paths "
            "registry, Start Menu shortcuts, and common install directories. "
            "Try the exact app name, or open it once manually so I can learn its path."
        )

    if learn and key not in _APP_ALIASES:
        _learn_alias(key, resolved)

    try:
        os.startfile(resolved)
        return f"Launched {raw}"
    except OSError as e:
        return _launch_failure_message(raw, e)


# =========================================================
# FILE TOOLS
# =========================================================

def create_text_file(
    filename: str,
    content: str = ""
) -> str:
    """
    Create a text file.

    A bare filename is anchored under Documents/FRED rather than the
    working directory — see resolve_user_path. Previously "notes.txt"
    landed wherever the process was launched from, which for a detached
    background app meant somewhere the user would never find it.
    """

    path = resolve_user_path(filename)

    if not path.suffix:
        path = path.with_suffix(".txt")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return f"Couldn't create {path.name}: {e}"

    return f"Created file: {path}"


def create_folder(folder_name: str) -> str:
    """
    Create a folder. Bare names go under Documents/FRED, as above.
    """

    path = resolve_user_path(folder_name)

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"Couldn't create {path.name}: {e}"

    return f"Created folder: {path}"


def convert_file(source_path: str, target_format: str) -> str:
    """
    Convert a file to another format by shelling out to ffmpeg (audio,
    video, image — whatever ffmpeg itself handles). Output is written
    next to the source with the new extension.

    Assumes ffmpeg is on PATH (confirmed present on this machine at
    C:\\ffmpeg\\bin\\ffmpeg.exe) — checked with shutil.which rather than
    hardcoding that location, so a missing/moved ffmpeg fails with a
    clear spoken message instead of a raw WinError.
    """

    source = resolve_user_path(source_path)
    if not source.exists():
        return f"Couldn't find {source.name}."
    if source.is_dir():
        return f"{source.name} is a folder — nothing to convert."

    fmt = str(target_format or "").strip().lstrip(".").lower()
    if not fmt:
        return "Give me a target format, like mp3 or png."

    if shutil.which("ffmpeg") is None:
        return "ffmpeg isn't installed or isn't on PATH — can't convert."

    dest = source.with_suffix(f".{fmt}")
    if dest.exists():
        return f"{dest.name} already exists there — not overwriting it."

    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(source), str(dest)],
            capture_output=True, text=True, timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return f"Converting {source.name} took too long and was stopped."
    except OSError as e:
        return f"Couldn't run ffmpeg: {e}"

    # ffmpeg's own stderr is a wall of codec/build info even on success —
    # never read aloud (see test_speech_safety.py); a failure gets a
    # short spoken reason instead of that raw dump.
    if result.returncode != 0 or not dest.exists():
        return f"Couldn't convert {source.name} to {fmt} — ffmpeg couldn't produce that format from this file."

    return f"Converted {source.name} to {dest.name}."


def print_file(path: str) -> str:
    """
    Print a file via its default app's print handler — os.startfile's
    "print" verb, the same thing Explorer's right-click > Print does.
    No new dependency (no win32print): this is a native Windows shell
    verb, just like open_path already uses the plain "open" verb.
    """

    target = resolve_user_path(path)
    if not target.exists():
        return f"Couldn't find {target.name}."
    if target.is_dir():
        return f"{target.name} is a folder — nothing to print."

    try:
        os.startfile(str(target), "print")
    except OSError as e:
        return f"Couldn't print {target.name}: {e}"

    return f"Sent {target.name} to the printer."


# =========================================================
# SYSTEM INFO TOOLS
# =========================================================

def get_current_time() -> str:
    """
    Local date and time, phrased for speech.

    Includes the weekday, which the previous version omitted — asked
    "what day is it today" it answered "It's 06:24:23 on 2026-07-30",
    which technically contains the date and yet doesn't answer the
    question. Seconds are dropped for the same reason: nobody asking the
    time out loud wants them.
    """

    now = datetime.now()
    clock = now.strftime("%I:%M %p").lstrip("0")

    return f"It's {clock} on {now.strftime('%A, %d %B %Y')}."


# =========================================================
# SYSTEM STATE
# =========================================================

# Spoken together with the DISENGAGE phrase only ("unlock fred 1111")
# — engaging stays a bare trigger, the friction is reserved for getting
# out. No popup, no storage, just a fixed shared PIN checked in code.
# ponytail: plain demo-grade constant, not a real secret; swap for
# something less trivial (and less trackable in git) before this
# protects anything that actually matters.
_LOCKDOWN_PIN = "1111"


def lockdown_engage() -> str:
    """
    Engage FRED's lockdown mode — bare trigger, no PIN needed (only
    lifting it does). Every other tool call gets refused while locked
    (see ToolRegistry.execute) — conversation still works.
    """

    if lockdown_state.is_locked():
        # A fast/small model has been observed emitting the same tool
        # call more than once for one utterance — without this, a
        # double-fire here would just be two identical no-op engages.
        return "Already locked down, sir."

    lockdown_state.set_locked(True)
    lockdown_log.log_event("engaged")

    from ui.pill_app import get_current_app
    app = get_current_app()
    if app is not None:
        _stand_down_models(app)

    return "Lockdown engaged, sir."


def lockdown_disengage(pin: str = "") -> str:
    """Lift FRED's lockdown mode — must be said together with the PIN
    ("unlock fred 1111")."""

    if not lockdown_state.is_locked():
        return "Not locked — nothing to lift."

    if pin.strip() != _LOCKDOWN_PIN:
        return "Still locked — wrong PIN."

    lockdown_state.set_locked(False)
    lockdown_log.log_event("disengaged")

    from ui.pill_app import get_current_app
    app = get_current_app()
    if app is not None:
        app.lifecycle.preload()  # same "bring back whatever was unloaded" call hotkey/wake already use

    return "Lockdown lifted, sir."


def _stand_down_models(app) -> None:
    """
    Unloads LLM/Whisper/Kokoro once the current turn is done with them —
    not synchronously here, since this function runs mid-turn (the
    finalize step right after this still needs the LLM to phrase the
    spoken reply, and TTS to speak it). Wake-word detection is untouched
    and keeps listening — that's what "unlock fred" arrives through.

    Self-healing on the way back: wake-triggered turns already call
    lifecycle.preload() on activation (see pill_app._on_hold_start), so
    even without lockdown_disengage()'s explicit preload() call above,
    the next "hey FRED" would reload things anyway.
    """
    import threading
    import time

    def run():
        for _ in range(100):  # ~10s max wait — ponytail: fixed, raise if turns run longer
            if not app.lifecycle.busy():
                break
            time.sleep(0.1)
        for model in (app.lifecycle.llm, app.lifecycle.stt, app.lifecycle.tts):
            if model is not None and model.is_loaded():
                model.unload()
        lockdown_log.log_event("standby")

    threading.Thread(target=run, daemon=True).start()


def describe_self(tool_names: list) -> str:
    """
    "What tools do you have" / "what model are you running", answered
    from the actually-running state rather than a doc that can drift
    from it: `tool_names` comes straight from the live ToolRegistry
    (see orchestrator._register_tools, which wires this one as a
    closure over self.tools.list_tools()), and the tier comes straight
    from config/settings.py's DEFAULT_TIER/MODEL_TIERS.
    """

    from config.settings import DEFAULT_TIER, MODEL_TIERS

    model_path = MODEL_TIERS.get(DEFAULT_TIER)
    model_name = model_path.stem if model_path else DEFAULT_TIER

    count = len(tool_names)
    sample = ", ".join(sorted(tool_names)[:6])

    return (
        f"I've got {count} tools wired up right now — things like {sample}, "
        f"and more. I'm running on the {DEFAULT_TIER} tier, model {model_name}."
    )