# install_startup.py
#
# Register (or remove) FRED so it starts automatically at log-on.
#
#   python install_startup.py          install
#   python install_startup.py --status show current state
#   python install_startup.py --remove uninstall
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
SHORTCUT = STARTUP_DIR / "FRED.lnk"


def _make_shortcut() -> str:
    """
    Create the .lnk via PowerShell's WScript.Shell COM object.

    Done through PowerShell rather than pywin32 so this has no extra
    dependency, and works even if the venv is rebuilt.
    """
    script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{SHORTCUT}')
$s.TargetPath = '{PYTHONW}'
$s.Arguments = '"{ENTRY}"'
$s.WorkingDirectory = '{PROJECT_DIR}'
$s.WindowStyle = 7
$s.Description = 'FRED - hold Left Ctrl+Alt to talk'
$s.Save()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return f"FAILED: {result.stderr.strip() or result.stdout.strip()}"
    return "ok"


def install() -> int:
    missing = [p for p in (PYTHONW, ENTRY) if not p.exists()]
    if missing:
        for path in missing:
            print(f"  missing: {path}")
        return 1

    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    outcome = _make_shortcut()

    if outcome != "ok" or not SHORTCUT.exists():
        print(f"Could not create the startup shortcut. {outcome}")
        return 1

    print(f"Installed. FRED will start at log-on.\n  {SHORTCUT}")
    print(f"  runs: {PYTHONW.name} {ENTRY.name}")
    print("\nRemove any time with:  python install_startup.py --remove")
    print("or just delete that .lnk from the Startup folder.")
    return 0


def remove() -> int:
    if not SHORTCUT.exists():
        print("Not installed — nothing to remove.")
        return 0
    try:
        SHORTCUT.unlink()
    except OSError as e:
        print(f"Couldn't remove {SHORTCUT}: {e}")
        return 1
    print(f"Removed. FRED will no longer start at log-on.")
    return 0


def status() -> int:
    print(f"startup shortcut : {SHORTCUT}")
    print(f"installed        : {SHORTCUT.exists()}")
    print(f"pythonw present  : {PYTHONW.exists()}  ({PYTHONW})")
    print(f"entry present    : {ENTRY.exists()}  ({ENTRY})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Start FRED at Windows log-on")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--remove", action="store_true", help="uninstall")
    group.add_argument("--status", action="store_true", help="show current state")
    args = parser.parse_args()

    if args.remove:
        return remove()
    if args.status:
        return status()
    return install()


if __name__ == "__main__":
    sys.exit(main())
