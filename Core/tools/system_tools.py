# Core/tools/system_tools.py

import os
import re
import shutil
import webbrowser
import subprocess
import winreg
from pathlib import Path
from datetime import datetime

from tools.assist_tools import resolve_user_path


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

# Where to hunt for an installed .exe when PATH / App Paths both miss.
_SEARCH_ROOTS = [
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    os.environ.get("LOCALAPPDATA", ""),
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


def launch_application(app_name: str) -> str:
    """
    Launch a desktop application by friendly name, resolving it via
    an alias map, then PATH, then Windows' App Paths registry, then a
    search of common install directories.
    """

    raw = app_name.strip()
    key = raw.lower()

    target = _APP_ALIASES.get(key, raw)

    # shell:/ms-settings:/etc. protocol targets — let the shell open them.
    if target.endswith(":") or target.startswith(("shell:", "ms-")):
        try:
            os.startfile(target)
            return f"Launched {raw}"
        except Exception as e:
            return f"Failed to launch {raw}: {e}"

    exe_name = target if target.lower().endswith(".exe") else f"{target}.exe"

    # 1. PATH
    resolved = shutil.which(target) or shutil.which(exe_name)

    # 2. App Paths registry
    if not resolved:
        resolved = _resolve_from_app_paths(exe_name)

    # 3. common install dirs
    if not resolved:
        resolved = _resolve_from_search_roots(exe_name)

    if not resolved:
        return (
            f"Couldn't find '{raw}' on this PC. "
            "Try the exact app name, or open it once manually so I can learn its path."
        )

    try:
        os.startfile(resolved)
        return f"Launched {raw}"
    except Exception as e:
        return f"Failed to launch {raw}: {e}"


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