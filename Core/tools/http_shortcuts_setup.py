# Core/tools/http_shortcuts_setup.py
#
# One-time provisioning: build an HTTP Shortcuts (Waboodoo, Android) import
# file wired to Core/web/phone_api.py's POST /command endpoint, and push it
# onto the paired phone. Not a chat tool, not registered in the orchestrator
# — "generate a phone-app config and import it" is a setup action you run
# once from a shell, same category as web/phone_api.py's own load_tokens().
#
#   Core/venv/Scripts/python.exe Core/tools/http_shortcuts_setup.py
#
# HOW THE IMPORT ACTUALLY REACHES THE PHONE
# ------------------------------------------
# HTTP Shortcuts has no "import this local file" adb intent worth relying
# on: its only file-shaped intent filters are ACTION_SEND (needs a
# content:// URI with a permission grant we have no provider for) and a
# documented deep link, https://http-shortcuts.rmy.ch/import?url=<URL>,
# which tells the app to fetch the export JSON itself over HTTP. That means
# something on this PC has to serve the file — and a fresh listening port
# needs a Windows Firewall inbound rule, which needs admin elevation this
# shell doesn't have (confirmed: `netsh advfirewall ... add rule` fails
# with "requires elevation" here).
#
# `adb reverse` sidesteps all of it: it tunnels a port on the PHONE's
# localhost through the existing USB/adb connection to a port on the PC's
# localhost. No LAN traffic, no firewall rule, because the phone's HTTP
# request never leaves the phone's own loopback interface. The server
# below binds 127.0.0.1 for exactly that reason — it is never meant to be
# reachable any other way.
#
# WHY THE SHORTCUTS THEMSELVES STILL TARGET THE LAN IP
# ------------------------------------------------------
# The import transport (adb reverse) and the shortcuts' own request target
# (phone_api.py at PORT) are unrelated. Once imported, the shortcuts run
# whenever — wired, wireless, or the PC turned off entirely — so each one
# has to carry a real, resolvable address, not a loopback tunnel that only
# exists during this script's run. That address is this machine's LAN IP,
# confirmed reachable because `adb shell ip addr show wlan0` on the paired
# phone put it on the same 192.168.0.0/24 subnet as this PC.
#
# SCHEMA VERSION
# --------------
# version=91 / compatibilityVersion=90 are hardcoded, not computed: they
# are HTTP Shortcuts 4.7.0's ImportExport.kt constants (checked against
# tag v4.7.0 in Waboodoo/HTTP-Shortcuts, which matches the versionName
# `adb shell dumpsys package` reports for the phone's installed copy). A
# newer app version raising these would make an import from this file
# throw ImportVersionMismatchException — that's the sign this script's
# constants need bumping to match, not a sign of a bug here.

import http.server
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from config.settings import DATA_DIR
from web.phone_api import PORT as FRED_PORT, load_tokens

APP_PACKAGE = "ch.rmy.android.http_shortcuts"
IMPORT_SCHEMA_VERSION = 91
IMPORT_COMPATIBILITY_VERSION = 90

# Any free-ish port. Only ever touched over the adb-reverse loopback
# tunnel below, never bound to 0.0.0.0, so which port it is barely
# matters — picked to be unlikely to collide with anything else running.
_SERVE_PORT = 8780

EXPORT_PATH = DATA_DIR / "http_shortcuts_export.json"

# Which of DEFAULT_DEVICES' tokens this physical phone's shortcuts use.
# One phone == one token; phone-2 is provisioned the same way, by hand,
# if a second device ever needs it.
DEVICE_NAME = "phone-1"


def _lan_ip() -> str:
    """
    This machine's LAN IPv4 — the address the phone's shortcuts must dial,
    since they run independently of this script and of adb afterwards.

    The connect() below sends no packet (UDP, and 8.8.8.8 need not be
    reachable) — it just makes the OS pick the outbound interface/address
    for that route, which is the standard no-dependency way to ask "what's
    my LAN IP" without parsing `ipconfig`/`ip addr` output.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def _adb(*args, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def _connected_serial() -> str:
    """The one attached device's serial, or "" if zero or several."""
    out = _adb("devices").stdout
    ready = [
        line.split("\t")[0] for line in out.splitlines()[1:]
        if line.strip().endswith("\tdevice")
    ]
    return ready[0] if len(ready) == 1 else ""


def _shortcut(*, name: str, body: str, headers: list, url: str) -> dict:
    """One HTTP Shortcuts ImportExportShortcut — POST + a text body, the
    exact shape phone_api.py's do_POST already accepts as a bare
    text/plain body (see the comment on its except clause)."""
    return {
        # Dashed UUID string, not .hex — HTTP Shortcuts' ImportExportBase
        # validates every id against UUID.fromString(), which rejects the
        # bare-hex form.
        "id": str(uuid.uuid4()),
        "name": name,
        "method": "POST",
        "url": url,
        "headers": headers,
        "requestBodyType": "custom_text",
        "bodyContent": body,
        # "dialog" is the lightest popup HTTP Shortcuts has — enough to
        # read FRED's reply without a full-screen window eating the tap
        # that triggered the shortcut from a widget.
        "responseHandling": {"uiType": "dialog", "successOutput": "response"},
    }


def build_export(lan_ip: str, token: str) -> dict:
    url = f"http://{lan_ip}:{FRED_PORT}/command"
    headers = [{"key": "X-FRED-Token", "value": token}]

    # Two free-text variables. HTTP Shortcuts prompts for a variable's
    # value the moment a shortcut referencing it (via {{key}}) runs — no
    # separate "prompt" flag needed on the shortcut itself.
    ask_var_key, alarm_var_key = "fred_ask_query", "fred_alarm_time"
    variables = [
        {
            "id": str(uuid.uuid4()), "key": ask_var_key, "type": "text",
            "title": "Ask FRED", "message": "What do you want to ask?",
        },
        {
            "id": str(uuid.uuid4()), "key": alarm_var_key, "type": "text",
            "title": "Set an alarm", "message": "What time? (e.g. 7:30 am)",
        },
    ]

    shortcuts = [
        _shortcut(
            name="Ask FRED", url=url, headers=headers,
            body=f"{{{{{ask_var_key}}}}}",
        ),
        _shortcut(
            name="What's on my screen?", url=url, headers=headers,
            body="what's on my screen",
        ),
        # Exact phrase, by agreement with the find_otp tool being built
        # separately — this shortcut doesn't know or care how that tool
        # works, only that this sentence is what routes to it.
        _shortcut(
            name="Find my OTP", url=url, headers=headers,
            body="find my otp",
        ),
        _shortcut(
            name="Set an alarm", url=url, headers=headers,
            body=f"set an alarm for {{{{{alarm_var_key}}}}}",
        ),
    ]

    return {
        "version": IMPORT_SCHEMA_VERSION,
        "compatibilityVersion": IMPORT_COMPATIBILITY_VERSION,
        "categories": [{
            "id": str(uuid.uuid4()),
            "name": "FRED",
            "shortcuts": shortcuts,
        }],
        "variables": variables,
    }


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # one-shot script; the exit-code/prints below are enough


def _serve_once(directory: Path, port: int) -> http.server.ThreadingHTTPServer:
    """
    Loopback-only static file server for exactly one file, for exactly as
    long as this script runs. 127.0.0.1, never 0.0.0.0 — see the module
    docstring on why this must not be reachable except through the adb
    reverse tunnel.
    """
    handler = lambda *a, **kw: _QuietHandler(*a, directory=str(directory), **kw)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _tap_import_confirm(serial: str) -> bool:
    """
    Best-effort: find and tap whatever button confirms the import.

    Confirmed live 2026-08-20 this dialog's real confirm button is
    plain "OK" — the URL field auto-focuses on open and pops the
    keyboard, which both covers "OK" (off-screen in a screenshot taken
    right after the deep link fires) AND meant the original version of
    this function's `[Ii]mport` regex matched the "Import from URL"
    TITLE text instead (it contains "import" too, and came first in
    the dump) — silently tapping the URL text field, not the button.
    Fixed: dismiss the keyboard first (BACK closes it without closing
    the dialog), then search for "OK" specifically, "Import"-containing
    text only as a fallback for a differently-worded dialog.
    """
    _adb("-s", serial, "shell", "input", "keyevent", "KEYCODE_BACK", timeout=10)
    time.sleep(0.5)

    dump = _adb("-s", serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml", timeout=15)
    if dump.returncode != 0:
        return False
    xml = _adb("-s", serial, "shell", "cat", "/sdcard/ui.xml", timeout=15).stdout

    match = (
        re.search(r'text="OK"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        or re.search(
            r'text="[^"]*[Ii]mport[^"]*"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml,
        )
    )
    if not match:
        return False

    x1, y1, x2, y2 = (int(g) for g in match.groups())
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    tap = _adb("-s", serial, "shell", "input", "tap", str(cx), str(cy), timeout=10)
    return tap.returncode == 0


def main():
    serial = _connected_serial()
    if not serial:
        print("[http_shortcuts_setup] no single attached device — check `adb devices`")
        return 1

    installed = _adb("-s", serial, "shell", "pm", "list", "packages", APP_PACKAGE).stdout
    if APP_PACKAGE not in installed:
        print(f"[http_shortcuts_setup] {APP_PACKAGE} is not installed on {serial}. "
              "Sideloading it is a separate call — install it from Play Store "
              "or F-Droid first, then re-run this script.")
        return 1

    tokens = load_tokens()  # reads Core/data/phone_tokens.json; never regenerates
    token = tokens.get(DEVICE_NAME)
    if not token:
        print(f"[http_shortcuts_setup] no token for {DEVICE_NAME!r} in {tokens.keys()}")
        return 1

    lan_ip = _lan_ip()
    export = build_export(lan_ip, token)
    EXPORT_PATH.write_text(json.dumps(export, indent=2), encoding="utf-8")
    print(f"[http_shortcuts_setup] wrote {EXPORT_PATH}")
    print(f"[http_shortcuts_setup] shortcuts will POST to http://{lan_ip}:{FRED_PORT}/command")

    httpd = _serve_once(DATA_DIR, _SERVE_PORT)
    reverse = _adb("-s", serial, "reverse", f"tcp:{_SERVE_PORT}", f"tcp:{_SERVE_PORT}")
    if reverse.returncode != 0:
        print(f"[http_shortcuts_setup] adb reverse failed: {reverse.stderr.strip()}")
        httpd.shutdown()
        return 1

    try:
        fetch_url = f"http://127.0.0.1:{_SERVE_PORT}/{EXPORT_PATH.name}"
        deep_link = "https://http-shortcuts.rmy.ch/import?url=" + urllib.parse.quote(fetch_url, safe="")

        launch = _adb("-s", serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                       "-d", deep_link, timeout=15)
        print(f"[http_shortcuts_setup] launch intent: {launch.stdout.strip() or launch.stderr.strip()}")

        time.sleep(2.5)  # let the app fetch the file and render the import screen

        screenshot = DATA_DIR / "http_shortcuts_import_screen.png"
        _adb("-s", serial, "exec-out", "screencap", "-p", timeout=15)  # warm the codec path
        with open(screenshot, "wb") as f:
            proc = subprocess.run(["adb", "-s", serial, "exec-out", "screencap", "-p"],
                                   stdout=f, timeout=15)
        print(f"[http_shortcuts_setup] pre-confirm screenshot: {screenshot}")

        confirmed = _tap_import_confirm(serial)
        if confirmed:
            time.sleep(1.5)
            after = DATA_DIR / "http_shortcuts_after_import.png"
            with open(after, "wb") as f:
                subprocess.run(["adb", "-s", serial, "exec-out", "screencap", "-p"],
                                stdout=f, timeout=15)
            print(f"[http_shortcuts_setup] tapped Import — post-import screenshot: {after}")
        else:
            print("[http_shortcuts_setup] could not find/tap an Import confirm button — "
                  "check the pre-confirm screenshot and tap Import on the phone by hand.")
    finally:
        _adb("-s", serial, "reverse", "--remove", f"tcp:{_SERVE_PORT}")
        httpd.shutdown()

    return 0


if __name__ == "__main__":
    # Self-check: schema shape only, no adb/network — run anywhere.
    fake = build_export("192.168.0.107", "deadbeef" * 4)
    assert fake["version"] == 91 and fake["compatibilityVersion"] == 90
    assert len(fake["categories"][0]["shortcuts"]) == 4
    assert len(fake["variables"]) == 2
    urls = {s["url"] for s in fake["categories"][0]["shortcuts"]}
    assert urls == {"http://192.168.0.107:8779/command"}
    bodies = {s["name"]: s["bodyContent"] for s in fake["categories"][0]["shortcuts"]}
    assert bodies["Find my OTP"] == "find my otp"
    assert bodies["What's on my screen?"] == "what's on my screen"
    assert bodies["Ask FRED"] == "{{fred_ask_query}}"
    assert bodies["Set an alarm"] == "set an alarm for {{fred_alarm_time}}"
    for s in fake["categories"][0]["shortcuts"]:
        assert s["headers"] == [{"key": "X-FRED-Token", "value": "deadbeef" * 4}]
        assert s["method"] == "POST"
    print("self-check ok")

    sys.exit(main())
