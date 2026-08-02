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

# How much "a bit" moves a level, versus a plain "turn it up". Both
# are what people actually say — nobody speaks in percentages to an
# assistant — and neither previously did anything: the only volume
# setter took an absolute number, so "turn it up a bit" either had to
# be guessed at by the model or missed entirely.
_NUDGE_SMALL = 10
_NUDGE_NORMAL = 20


def adjust_volume(direction: str, amount: str = "normal") -> str:
    """
    Move the volume relative to where it is now.

    direction: "up" or "down". amount: "small" ("a bit", "slightly"),
    "normal", or "large" ("a lot", "way up").
    """
    volume = _get_volume_interface()
    current = round(volume.GetMasterVolumeLevelScalar() * 100)

    step = {
        "small": _NUDGE_SMALL,
        "normal": _NUDGE_NORMAL,
        "large": _NUDGE_NORMAL * 2,
    }.get(str(amount).lower(), _NUDGE_NORMAL)

    if str(direction).lower().startswith("d"):
        step = -step

    level = max(0, min(100, current + step))
    volume.SetMasterVolumeLevelScalar(level / 100, None)

    if level == current:
        return f"Volume is already at {level}%."

    return f"Volume {'up' if step > 0 else 'down'} to {level}%."


def adjust_brightness(direction: str, amount: str = "normal") -> str:
    """Same relative move for screen brightness — see adjust_volume."""
    try:
        current = sbc.get_brightness()[0]
    except Exception as e:
        return f"Couldn't read brightness: {e}"

    step = {
        "small": _NUDGE_SMALL,
        "normal": _NUDGE_NORMAL,
        "large": _NUDGE_NORMAL * 2,
    }.get(str(amount).lower(), _NUDGE_NORMAL)

    if str(direction).lower().startswith("d"):
        step = -step

    level = max(0, min(100, current + step))

    try:
        sbc.set_brightness(level)
    except Exception as e:
        return f"Couldn't set brightness: {e}"

    if level == current:
        return f"Brightness is already at {level}%."

    return f"Brightness {'up' if step > 0 else 'down'} to {level}%."


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

# The clipboard is unbounded — a copied article or a whole file's worth
# of code is a perfectly normal thing to find in it, and this result is
# read aloud. Without a cap, "what's in my clipboard" can commit FRED to
# several minutes of uninterruptible speech.
_CLIPBOARD_SPEAK_CHARS = 600


def get_clipboard() -> str:
    """
    Read the current clipboard contents, truncated to something
    speakable (see _CLIPBOARD_SPEAK_CHARS).
    """

    content = pyperclip.paste()

    if not content:
        return "Clipboard is empty."

    content = content.strip()

    if len(content) > _CLIPBOARD_SPEAK_CHARS:
        head = content[:_CLIPBOARD_SPEAK_CHARS].rsplit(" ", 1)[0]
        remaining = len(content) - len(head)
        return f"{head}... (about {remaining} more characters on the clipboard)"

    return content


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


def matching_processes(name_or_pid: str) -> list:
    """
    Every running process kill_process would act on, without killing
    anything. Split out from kill_process so the orchestrator's
    confirmation prompt can show the actual targets before the user
    says yes — the substring match below matches "code" against
    Code.exe, every one of its helper processes, and anything else with
    "code" anywhere in its name, and a confirmation that only echoes the
    raw argument back ("about to run kill_process (name_or_pid=code)")
    gives no way to notice that before it happens.
    """
    target = str(name_or_pid).strip()
    matches = []

    for proc in psutil.process_iter(["pid", "name"]):
        info = proc.info
        name = info.get("name") or ""
        pid = info.get("pid")

        if target.isdigit() and int(target) == pid:
            matches.append((name, pid))
        elif target and target.lower() in name.lower():
            matches.append((name, pid))

    return matches


def kill_process(name_or_pid: str) -> str:
    """
    Kill a process by name or PID. Destructive — unsaved work in
    that process is lost. See matching_processes for what "matches"
    means; the orchestrator shows that same list before this ever runs.
    """

    target = str(name_or_pid).strip()
    matches = matching_processes(target)
    killed = []

    for name, pid in matches:
        try:
            psutil.Process(pid).kill()
            killed.append(f"{name} (PID {pid})")
        except psutil.NoSuchProcess:
            continue  # already gone between the preview and this call

    if not killed:
        return f"No process found matching '{target}'."

    return "Killed: " + ", ".join(killed)


# =========================================================
# FILE NAVIGATION
# =========================================================

# Directories a user search is never actually asking about, pruned
# during the walk rather than filtered after it.
#
# Measured 2026-08-02: searching the home folder for "dossier" took
# 103.6s, because Path.rglob("*") descends into everything — AppData
# (tens of thousands of files), package caches, node_modules, virtualenvs,
# .git object stores, the local model store. The same search scoped to
# Desktop took 0.6s. The walk itself was the entire cost; nothing about
# the matching was slow.
#
# Pruning has to happen DURING traversal to help at all, which rglob
# cannot do — hence os.walk, whose `dirs` list can be edited in place to
# stop it descending. Names are matched case-insensitively because
# Windows paths are.
_SKIP_DIRS = {
    "appdata", "application data", "$recycle.bin", "onedrivetemp",
    "node_modules", "__pycache__", "site-packages", "venv", ".venv",
    ".git", ".svn", ".hg", ".cache", ".conda", ".gradle", ".nuget",
    ".pyenv", ".lmstudio", ".ollama", ".cargo", ".rustup", ".npm",
    ".vscode", ".idea", "temp", "tmp",
}


def _walk_pruned(base: Path):
    """
    Yield files under `base`, skipping the directories in _SKIP_DIRS and
    anything hidden (a leading dot). Depth is not limited — pruning the
    heavy trees is what makes this fast, not a depth cap, and a cap
    would silently miss genuinely deep files.
    """
    for dirpath, dirs, files in os.walk(base, topdown=True):
        # In-place edit is load-bearing: os.walk reads this list AFTER
        # the yield to decide where to go next, so reassigning it
        # (dirs = [...]) instead of slicing would prune nothing.
        dirs[:] = [
            d for d in dirs
            if d.lower() not in _SKIP_DIRS and not d.startswith(".")
        ]
        folder = Path(dirpath)
        for name in files:
            yield folder / name


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
        for path in _walk_pruned(base):
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

    # Give a follow-up ("open it") something to refer to — the spoken
    # result deliberately carries no paths, so the referent has to live
    # somewhere the next turn can reach.
    found_cache.set_last(matches)

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

    # Path.rename() on Windows raises FileExistsError rather than
    # overwriting — confirmed directly, unlike POSIX rename() which
    # overwrites unconditionally. So this was never a silent-overwrite
    # risk on this OS, but it WAS an unhandled crash: a destination
    # collision surfaced as a raw "[WinError 183] Cannot create a file
    # when that file already exists: '...'" instead of a clear answer.
    # Checked explicitly so the behavior doesn't depend on which OS
    # this happens to run on, and so it's a sentence instead of a
    # stack trace either way.
    if dest.exists():
        return f"{dest.name} already exists there — not overwriting it."

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

    if dest.exists():
        return f"{dest.name} already exists — not overwriting it."

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
