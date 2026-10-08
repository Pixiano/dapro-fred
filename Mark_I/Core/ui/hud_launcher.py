"""Owns the HUD from inside Mark I: starts its server quietly at boot, opens the window only when asked (tray). Ported from the old FRED's utils/hud_manager.py.

Sockets
  in : HudLauncher().start_server()  idempotent: a server already on :8777 is reused;  .show()  open the HUD in a kiosk Chrome (default browser if Chrome is missing);  .stop()
  out: the server (ui/hud/server.py) runs as its own OS process, so a HUD crash costs only the HUD. Nothing here raises into Mark I: the HUD is an accessory.
Self-check: python ui/hud_launcher.py (fake process and port, no browser)
"""
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HUD_DIR = Path(__file__).resolve().parent / "hud"
SERVER = HUD_DIR / "server.py"
PORT = 8777
URL = f"http://127.0.0.1:{PORT}/"
PROFILE_DIR = Path(os.environ.get("TEMP", ".")) / "mark1-hud-profile"      # throwaway profile: a kiosk appliance, not the real Chrome profile
CHROME = (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
          str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"))
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _server_is_up(timeout=0.6) -> bool:
    try:
        with urllib.request.urlopen(URL + "state", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


class HudLauncher:
    def __init__(self):
        self.server = self.window = None

    def start_server(self):
        if not SERVER.is_file() or _server_is_up():
            return
        try:
            self.server = subprocess.Popen([sys.executable, str(SERVER)], cwd=str(HUD_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except OSError as ex:
            print(f"[hud] server failed to start: {ex}")

    def show(self):
        if not _server_is_up():
            self.start_server()
        chrome = next((p for p in CHROME if p and Path(p).is_file()), None)
        try:
            if chrome is None:
                os.startfile(URL)
            else:
                self.window = subprocess.Popen([chrome, "--kiosk", f"--user-data-dir={PROFILE_DIR}", "--no-first-run", "--disable-features=Translate", URL], creationflags=_NO_WINDOW)
        except OSError as ex:
            print(f"[hud] window failed to open: {ex}")

    def stop(self):
        for proc in (self.window, self.server):                                  # the window first, so the page never shows a dead server
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
        self.window = self.server = None


def _selfcheck():
    global _server_is_up
    made = []

    class P:
        def __init__(self, *a, **k): made.append(a[0][-1]); self.dead = False
        def poll(self): return 1 if self.dead else None
        def terminate(self): self.dead = True
    real_popen, real_up = subprocess.Popen, _server_is_up
    subprocess.Popen, _server_is_up = P, lambda timeout=0.6: False
    try:
        h = HudLauncher()
        h.start_server()
        assert made == [str(SERVER)] and h.server is not None
        h.stop()
        assert h.server is None
        _server_is_up = lambda timeout=0.6: True
        h.start_server()
        assert len(made) == 1 and h.server is None                                  # a running server is reused, not fought over
    finally:
        subprocess.Popen, _server_is_up = real_popen, real_up
    print("hud launcher ok")


if __name__ == "__main__":
    _selfcheck()
