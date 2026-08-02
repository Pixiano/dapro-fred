# Core/utils/hud_manager.py
#
# Owns the HUD from inside FRED: starts its server quietly at boot and
# opens the window only when the tray is clicked.
#
# The split matters. The SERVER is cheap (stdlib http.server + a 1s
# telemetry poll) and has to be up early, because it is what records the
# session while you are not looking. The WINDOW is a whole Chrome, and
# the user asked not to see it unless they ask for it — so the browser is
# only launched on demand.
#
# The server runs as its own OS process rather than a thread in FRED. It
# blocks in serve_forever, wants its own lifetime, and must be killable
# without touching the voice pipeline; a crash in the HUD should cost the
# HUD only.

import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
HUD_DIR = PROJECT_DIR / "hud"
SERVER = HUD_DIR / "server.py"
PORT = 8777
URL = f"http://127.0.0.1:{PORT}/"

# Throwaway profile: the HUD is a kiosk appliance, and pointing it at the
# real Chrome profile would drag in extensions, sessions and a restore
# prompt after any hard shutdown.
PROFILE_DIR = Path(os.environ.get("TEMP", ".")) / "fred-hud-profile"

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _server_is_up(timeout=0.6) -> bool:
    try:
        with urllib.request.urlopen(URL + "state", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _find_chrome():
    for path in CHROME_CANDIDATES:
        if path and Path(path).is_file():
            return path
    return None


class HudManager:
    """Nothing here may raise into FRED: the HUD is an accessory, and a
    missing Chrome or a busy port must never stop the voice assistant
    from starting."""

    def __init__(self):
        self.server = None
        self.window = None

    def start_server(self):
        """Called at boot. Idempotent — an already-running server (say a
        previous FRED that didn't shut down cleanly) is adopted rather
        than fought over the port."""
        if not SERVER.is_file():
            print(f"[hud] server not found at {SERVER}")
            return
        if _server_is_up():
            # ASCII only in these prints: they go to a cp1252 console
            # under pythonw and a dash turns into mojibake.
            print(f"[hud] server already up on :{PORT} - reusing it")
            return
        try:
            self.server = subprocess.Popen(
                [sys.executable, str(SERVER)],
                cwd=str(HUD_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
            print(f"[hud] server started (pid {self.server.pid})")
        except OSError as e:
            print(f"[hud] server failed to start: {e}")

    def show(self):
        """Tray action. Re-focusing an already-open HUD is left to the
        window manager: launching Chrome again with the same profile
        raises the existing window instead of making a second one."""
        if not _server_is_up():
            # Covers the case where the server died, or the user clicked
            # before it finished binding.
            self.start_server()

        chrome = _find_chrome()
        if chrome is None:
            print("[hud] Chrome not found — opening in the default browser")
            try:
                os.startfile(URL)
            except OSError as e:
                print(f"[hud] could not open a browser: {e}")
            return
        try:
            self.window = subprocess.Popen(
                [chrome, "--kiosk", f"--user-data-dir={PROFILE_DIR}",
                 "--no-first-run", "--disable-features=Translate", URL],
                creationflags=_NO_WINDOW,
            )
            print("[hud] window opened")
        except OSError as e:
            print(f"[hud] window failed to open: {e}")

    def stop(self):
        """Take the HUD down with FRED. The window goes first so the page
        isn't briefly showing a dead server."""
        for name, proc in (("window", self.window), ("server", self.server)):
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.terminate()
                print(f"[hud] {name} stopped")
            except OSError:
                pass
        self.window = self.server = None
