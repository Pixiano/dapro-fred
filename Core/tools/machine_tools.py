# Core/tools/machine_tools.py
#
# Phase 14 — "Hands on the Machine." Real control of the PC: windows,
# volume/brightness, clipboard, screenshots, processes, and file
# navigation. Everything here runs locally — no network involved.
#
# Functions whose names appear in DESTRUCTIVE_TOOLS (see registry
# wiring in orchestrator.py) must ask for confirmation before running.
# That gate lives in the orchestrator, not here — these functions
# always just do the thing when called.

import os
from pathlib import Path
from datetime import datetime

from tools.assist_tools import resolve_user_path
from tools import found_cache

import psutil
import pyperclip
import pygetwindow as gw
import screen_brightness_control as sbc
from mss import mss
from pycaw.pycaw import AudioUtilities


# =========================================================
# WINDOW MANAGEMENT
# =========================================================

def list_windows() -> str:
    """
    List titles of all open, visible windows.
    """

    titles = [w.title for w in gw.getAllWindows() if w.title.strip()]

    if not titles:
        return "No open windows found."

    return "\n".join(f"- {t}" for t in titles)


def _find_window(title: str):

    matches = gw.getWindowsWithTitle(title)

    if not matches:
        raise ValueError(f"No window found matching '{title}'.")

    return matches[0]


def focus_window(title: str) -> str:
    """
    Bring a window to the foreground by (partial) title match.
    """

    window = _find_window(title)
    window.activate()

    return f"Focused window: {window.title}"


def minimize_window(title: str) -> str:
    """
    Minimize a window by (partial) title match.
    """

    window = _find_window(title)
    window.minimize()

    return f"Minimized: {window.title}"


def maximize_window(title: str) -> str:
    """
    Maximize a window by (partial) title match.
    """

    window = _find_window(title)
    window.maximize()

    return f"Maximized: {window.title}"


def close_window(title: str) -> str:
    """
    Close a window by (partial) title match. Destructive — may
    discard unsaved work in that window.
    """

    window = _find_window(title)
    closed_title = window.title
    window.close()

    return f"Closed: {closed_title}"


# =========================================================
# VOLUME
# =========================================================

def _get_volume_interface():

    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> str:
    """
    Get the current system volume (0-100) and mute state.
    """

    volume = _get_volume_interface()
    level = round(volume.GetMasterVolumeLevelScalar() * 100)
    muted = bool(volume.GetMute())

    return f"Volume: {level}%{' (muted)' if muted else ''}"


def set_volume(level: int) -> str:
    """
    Set system volume to a percentage (0-100).
    """

    level = max(0, min(100, int(level)))

    volume = _get_volume_interface()
    volume.SetMasterVolumeLevelScalar(level / 100, None)

    return f"Volume set to {level}%"


def mute(should_mute: bool = True) -> str:
    """
    Mute or unmute system audio.
    """

    volume = _get_volume_interface()
    volume.SetMute(1 if should_mute else 0, None)

    return "Muted" if should_mute else "Unmuted"


# =========================================================
# BRIGHTNESS
# =========================================================

def get_brightness() -> str:
    """
    Get current screen brightness (0-100).
    """

    try:
        levels = sbc.get_brightness()
        return f"Brightness: {levels[0]}%"
    except Exception as e:
        return f"Couldn't read brightness: {e}"


def set_brightness(level: int) -> str:
    """
    Set screen brightness to a percentage (0-100).
    """

    level = max(0, min(100, int(level)))

    try:
        sbc.set_brightness(level)
        return f"Brightness set to {level}%"
    except Exception as e:
        return f"Couldn't set brightness: {e}"


# =========================================================
# CLIPBOARD
# =========================================================

def get_clipboard() -> str:
    """
    Read the current clipboard contents.
    """

    content = pyperclip.paste()

    return content if content else "Clipboard is empty."


def set_clipboard(text: str) -> str:
    """
    Write text to the clipboard.
    """

    pyperclip.copy(text)

    return "Copied to clipboard."


# =========================================================
# SCREENSHOTS
# =========================================================

def take_screenshot(save_path: str = "") -> str:
    """
    Capture the screen and save it as a PNG.
    """

    if not save_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Pictures/Screenshots, not the working directory — a background
        # app's CWD is wherever it happened to be launched from, so the
        # old default put screenshots somewhere unfindable.
        folder = Path(os.path.expanduser("~")) / "Pictures" / "Screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"screenshot_{timestamp}.png"
    else:
        path = resolve_user_path(save_path)

    if not path.suffix:
        path = path.with_suffix(".png")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with mss() as sct:
            sct.shot(output=str(path))
    except Exception as e:
        return f"Couldn't save the screenshot: {e}"

    return f"Screenshot saved: {path}"


# =========================================================
# PROCESS CONTROL
# =========================================================

def list_processes(filter_name: str = "") -> str:
    """
    List running processes, optionally filtered by name substring.
    """

    filter_name = filter_name.lower().strip()
    rows = []

    for proc in psutil.process_iter(["pid", "name"]):
        info = proc.info
        name = info.get("name") or ""

        if filter_name and filter_name not in name.lower():
            continue

        rows.append(f"- {name} (PID {info.get('pid')})")

    if not rows:
        return f"No processes found matching '{filter_name}'." if filter_name else "No processes found."

    return "\n".join(rows[:50])


def kill_process(name_or_pid: str) -> str:
    """
    Kill a process by name or PID. Destructive — unsaved work in
    that process is lost.
    """

    target = str(name_or_pid).strip()
    killed = []

    for proc in psutil.process_iter(["pid", "name"]):
        info = proc.info
        name = info.get("name") or ""
        pid = info.get("pid")

        if target.isdigit() and int(target) == pid:
            proc.kill()
            killed.append(f"{name} (PID {pid})")
        elif target.lower() in name.lower():
            proc.kill()
            killed.append(f"{name} (PID {pid})")

    if not killed:
        return f"No process found matching '{target}'."

    return "Killed: " + ", ".join(killed)


# =========================================================
# FILE NAVIGATION
# =========================================================

def search_files(query: str, directory: str = "") -> str:
    """
    Search for files by name (substring match) under a directory,
    defaulting to the user's home folder.

    Returns names only, not full absolute paths — FRED's replies are
    spoken aloud (see persona.md's "no file paths" rule), and a raw
    "C:\\Users\\...\\file.txt" list is exactly the shape a small model
    tends to just parrot instead of summarising. Formatting the result
    itself to be speech-safe, the same way move_file/rename_file already
    report "X to Y" instead of raw Path reprs, doesn't depend on the
    follow-up phrasing pass reliably rewriting it.
    """

    base = resolve_user_path(directory) if directory else Path.home()

    if not base.exists():
        return f"Directory not found: {base}"

    query = query.lower()

    # Suggestion #2 (found-things index): a repeat of the same
    # (directory, query) pair skips the rglob walk entirely if every
    # cached path still exists — see tools/found_cache.py for the
    # staleness handling.
    cached = found_cache.get(query, str(base))
    if cached is not None:
        matches = [Path(p) for p in cached]
    else:
        matches = []
        for path in base.rglob("*"):
            if query in path.name.lower():
                matches.append(path)
                if len(matches) >= 50:
                    break
        # Positive results only — a miss has no invalidation trigger
        # (nothing marks the cache dirty when a matching file is later
        # created), so caching "not found" risks a permanently stale
        # false negative. A hit is safe because it's re-verified with
        # Path.exists() on every read.
        if matches:
            found_cache.put(query, str(base), [str(p) for p in matches])

    if not matches:
        return f"No files matching '{query}' found under {base}."

    shown = matches[:10]
    names = ", ".join(f"{p.name} (in {p.parent.name})" for p in shown)
    summary = f"Found {len(matches)} file(s) matching '{query}': {names}"
    if len(matches) > len(shown):
        summary += f", and {len(matches) - len(shown)} more"
    return summary


def move_file(source: str, destination: str) -> str:
    """
    Move (or rename) a file or folder to a new location.
    """

    src = Path(source)

    if not src.exists():
        return f"Source not found: {src}"

    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)

    src.rename(dest)

    # "to" rather than "->" — spoken output, and a literal arrow either
    # gets read as a stray symbol or silently dropped.
    return f"Moved {src} to {dest}"


def rename_file(path: str, new_name: str) -> str:
    """
    Rename a file or folder in place.
    """

    src = Path(path)

    if not src.exists():
        return f"Path not found: {src}"

    dest = src.parent / new_name
    src.rename(dest)

    return f"Renamed {src.name} to {dest.name}"


def read_file(path: str, max_chars: int = 4000) -> str:
    """
    Read a text file's contents (truncated for very large files).
    """

    target = Path(path)

    if not target.exists():
        return f"File not found: {target}"

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Couldn't read file: {e}"

    if len(content) > max_chars:
        return content[:max_chars] + f"\n... [truncated, {len(content)} chars total]"

    return content


def delete_file(path: str) -> str:
    """
    Delete a file or folder. Destructive — irreversible.
    """

    target = Path(path)

    if not target.exists():
        return f"Path not found: {target}"

    if target.is_dir():
        import shutil
        shutil.rmtree(target)
    else:
        target.unlink()

    return f"Deleted: {target}"
