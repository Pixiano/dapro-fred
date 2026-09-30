# install_startup.py
#
# Register (or remove) FRED so it starts automatically at log-on, and/or
# drop a desktop shortcut for launching it by hand.
#
#   python install_startup.py             install (start at log-on)
#   python install_startup.py --desktop   put a shortcut on the Desktop
#   python install_startup.py --status    show current state
#   python install_startup.py --remove    uninstall (add --desktop to
#                                          remove the desktop one instead)
#
# Uses the per-user Startup folder rather than Task Scheduler or the
# registry Run key. All three work; this one is chosen because it's the
# only one you can inspect and delete in Explorer without tooling — an
# assistant that launches itself should be trivially easy to stop doing
# that. It also needs no admin rights.
#
# The shortcut runs pythonw.exe, not python.exe, so no console window
# appears at log-on. The tray icon is the only UI.

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PYTHONW = PROJECT_DIR / "Core" / "venv" / "Scripts" / "pythonw.exe"
ENTRY = PROJECT_DIR / "fred_popup.py"

STARTUP_DIR = (
    Path(os.environ["APPDATA"])
    / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
)
STARTUP_SHORTCUT = STARTUP_DIR / "FRED.lnk"

DESKTOP_DIR = Path(os.environ["USERPROFILE"]) / "Desktop"
DESKTOP_SHORTCUT = DESKTOP_DIR / "FRED.lnk"


def _make_shortcut(shortcut: Path, description: str) -> str:
    """
    Create the .lnk via PowerShell's WScript.Shell COM object.

    Done through PowerShell rather than pywin32 so this has no extra
    dependency, and works even if the venv is rebuilt.
    """
    script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{shortcut}')
$s.TargetPath = '{PYTHONW}'
$s.Arguments = '"{ENTRY}" --greet-now'
$s.WorkingDirectory = '{PROJECT_DIR}'
$s.WindowStyle = 7
$s.Description = '{description}'
$s.Save()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return f"FAILED: {result.stderr.strip() or result.stdout.strip()}"
    return "ok"


def install(shortcut: Path, description: str, verb: str) -> int:
    missing = [p for p in (PYTHONW, ENTRY) if not p.exists()]
    if missing:
        for path in missing:
            print(f"  missing: {path}")
        return 1

    shortcut.parent.mkdir(parents=True, exist_ok=True)
    outcome = _make_shortcut(shortcut, description)

    if outcome != "ok" or not shortcut.exists():
        print(f"Could not create the shortcut. {outcome}")
        return 1

    print(f"Installed. {verb}\n  {shortcut}")
    print(f"  runs: {PYTHONW.name} {ENTRY.name} --greet-now")
    print(f"\nRemove any time with:  python install_startup.py --remove{' --desktop' if shortcut == DESKTOP_SHORTCUT else ''}")
    print(f"or just delete that .lnk.")
    return 0


def remove(shortcut: Path, noun: str) -> int:
    if not shortcut.exists():
        print(f"Not installed — nothing to remove ({noun}).")
        return 0
    try:
        shortcut.unlink()
    except OSError as e:
        print(f"Couldn't remove {shortcut}: {e}")
        return 1
    print(f"Removed. {noun}")
    return 0


def status() -> int:
    print(f"startup shortcut : {STARTUP_SHORTCUT}")
    print(f"installed        : {STARTUP_SHORTCUT.exists()}")
    print(f"desktop shortcut : {DESKTOP_SHORTCUT}")
    print(f"installed        : {DESKTOP_SHORTCUT.exists()}")
    print(f"pythonw present  : {PYTHONW.exists()}  ({PYTHONW})")
    print(f"entry present    : {ENTRY.exists()}  ({ENTRY})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Start FRED at Windows log-on, or from a Desktop shortcut")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--remove", action="store_true", help="uninstall")
    group.add_argument("--status", action="store_true", help="show current state")
    parser.add_argument("--desktop", action="store_true",
                         help="target the Desktop shortcut instead of the log-on Startup one")
    args = parser.parse_args()

    if args.status:
        return status()

    if args.desktop:
        if args.remove:
            return remove(DESKTOP_SHORTCUT, "FRED will no longer launch from the Desktop shortcut.")
        return install(DESKTOP_SHORTCUT, "FRED - hold Left Ctrl+Alt to talk",
                        "Double-click it to start FRED.")

    if args.remove:
        return remove(STARTUP_SHORTCUT, "FRED will no longer start at log-on.")
    return install(STARTUP_SHORTCUT, "FRED - hold Left Ctrl+Alt to talk",
                    "FRED will start at log-on.")


if __name__ == "__main__":
    sys.exit(main())
