# Core/web/phone_api.py
#
# The LAN front door: lets a phone hand FRED a command and get the reply.
#
# Phase 0 of the companion-app plan. There is deliberately no app yet —
# any HTTP client works, and the intended one is the "HTTP Shortcuts"
# Android app. If that turns out to be good enough, the Kotlin app never
# needs writing.
#
#   Core/venv/Scripts/python.exe Core/web/phone_api.py
#   -> http://<this machine>:8779
#
# WHY A SEPARATE SERVER FROM hud/server.py
# ----------------------------------------
# hud/server.py is bound to 127.0.0.1 and must stay that way: it serves
# live machine telemetry (GPU, power, session turns, diagnostics) that has
# no business on the network. This process shares its file bus and nothing
# else. Adding a 0.0.0.0 flag to the HUD server would have put that
# telemetry one config mistake away from the LAN.
#
# WHAT GOES OVER THE WIRE, AND WHY THAT IS ACCEPTED
# -------------------------------------------------
# FRED's replies can contain vault content — retrieval injects vault
# chunks into turns, and the vault's AGENT-BOOTSTRAP.md forbids personal/
# and people/ content leaving this machine. A reply crossing the LAN to
# Vatsal's own phone is judged to be still "on this machine's network"
# rather than leaving it, which is why this binds to the LAN at all. That
# judgement is only sound while:
#
#   - the bind stays behind a firewall rule scoped to the local subnet,
#   - the WiFi is Vatsal's own and not a shared/guest network,
#   - this is never port-forwarded, UPnP'd, or tunnelled.
#
# Break any of those and the vault rule breaks with it. There is no TLS
# here on purpose: a self-signed cert on a LAN is a pinning chore that
# only helps against an attacker already on the WiFi, at which point the
# token is the control that matters.

import hmac
import json
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from config.settings import DATA_DIR

HOST = "0.0.0.0"
PORT = 8779

# Same bus hud/server.py uses. Path duplicated rather than imported:
# hud/server.py is a script outside Core/, and reaching across the tree to
# import a module called "server" costs more than one Path expression.
BUS_DIR = Path.home() / "voice-line"

# Matches hud/server.py's COMMAND_TIMEOUT/COMMAND_POLL. A thinking turn
# runs long; wait it out rather than returning a timeout the phone can't
# do anything useful with.
COMMAND_TIMEOUT = 45.0
COMMAND_POLL = 0.25

# One token per device, named so a lost phone can be revoked on its own
# rather than by rotating everything. Generated on first run.
TOKENS_PATH = DATA_DIR / "phone_tokens.json"
DEFAULT_DEVICES = ("phone-1", "phone-2")

# The bus is a single command.json slot, so two devices posting at the
# same instant would have one overwrite the other. Requests from THIS
# process are serialised here.
#
# ponytail: in-process lock only. hud/server.py is a separate process on
# the same bus, so a phone and the HUD console posting in the same
# 250ms still race — the loser gets "FRED didn't respond in time" rather
# than a wrong answer, because submit_command only accepts a reply whose
# id matches its own request. Give the bus per-request filenames if that
# ever stops being rare enough to ignore.
_bus_lock = threading.Lock()


def load_tokens() -> dict:
    """
    {device_name: token}, creating the file on first run.

    Written 0600-ish by virtue of living in DATA_DIR on a single-user
    box; this is a LAN shared secret, not a credential store.
    """
    if TOKENS_PATH.exists():
        try:
            data = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError):
            print(f"[phone_api] {TOKENS_PATH} unreadable — regenerating")

    tokens = {name: secrets.token_hex(16) for name in DEFAULT_DEVICES}
    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_PATH.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    return tokens


def check_token(supplied: str, tokens: dict) -> str:
    """
    Device name for a valid token, else "".

    compare_digest against every token rather than a dict lookup: a plain
    `in` comparison on secrets leaks length and prefix information through
    timing. Every candidate is checked even after a match, so the work
    done doesn't depend on which device it was.
    """
    supplied = supplied or ""
    matched = ""
    for name, token in tokens.items():
        if hmac.compare_digest(supplied, token):
            matched = name
    return matched


def submit_command(text: str) -> str:
    """
    Hand text to FRED over the file bus and wait for the matching reply.

    Lifted from hud/server.py:submit_command — that is the reference
    implementation and the one Core/ui/pill_app.py:_hud_command_loop was
    written against. Kept as a copy rather than an import for the same
    cross-tree reason as BUS_DIR; if the protocol ever changes, both ends
    and both copies change together.
    """
    req_id = uuid.uuid4().hex

    with _bus_lock:
        try:
            (BUS_DIR / "command.json").write_text(
                json.dumps({"id": req_id, "text": text}), encoding="utf-8"
            )
        except OSError:
            return "Couldn't reach FRED (bus write failed)."

        reply_path = BUS_DIR / "command_reply.json"
        deadline = time.time() + COMMAND_TIMEOUT
        while time.time() < deadline:
            time.sleep(COMMAND_POLL)
            try:
                data = json.loads(reply_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("id") == req_id:
                return str(data.get("text") or "").strip() or "(empty reply)"

    return "FRED didn't respond in time."


class Handler(BaseHTTPRequestHandler):

    server_version = "FREDPhoneAPI/1"
    tokens = {}

    def log_message(self, fmt, *args):
        # Default logging prints the full request line to stderr. Commands
        # are spoken instructions and belong in FRED's own session log,
        # not duplicated into a web server log.
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> str:
        device = check_token(self.headers.get("X-FRED-Token", ""), self.tokens)
        if not device:
            # No hint about whether the token was absent, malformed, or
            # simply wrong.
            self._send(401, {"error": "unauthorised"})
        return device

    def do_GET(self):
        if self.path == "/ping":
            if not self._authorised():
                return
            self._send(200, {"ok": True})
            return

        if self.path == "/phone/next":
            # Phase 2 hangs the PC->phone action queue here. Answering
            # 204 now means an app written against it degrades to "no
            # actions ever" instead of erroring.
            if not self._authorised():
                return
            self.send_response(204)
            self.end_headers()
            return

        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/command":
            self._send(404, {"error": "not found"})
            return

        device = self._authorised()
        if not device:
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        # A command is a sentence. Anything vastly larger is a mistake or
        # someone probing, and either way there is no reason to read it.
        if not 0 < length <= 8192:
            # Naming what arrived, because "bad request" alone sent us
            # guessing at a phone client's body settings for a round trip.
            # Headers only, never the body, which is the command text.
            #
            # ASCII only in these prints - they reach a cp1252 console
            # under pythonw and an em dash turns into mojibake. Same rule
            # as utils/hud_manager.py.
            print(
                f"[phone_api] {device}: rejected body - "
                f"length={self.headers.get('Content-Length')!r} "
                f"type={self.headers.get('Content-Type')!r} "
                f"encoding={self.headers.get('Transfer-Encoding')!r}"
            )
            self._send(400, {"error": "bad request"})
            return

        raw = self.rfile.read(length)
        try:
            text = str(json.loads(raw).get("text", "")).strip()
        except (ValueError, AttributeError):
            # Also accept a bare text/plain body, so an HTTP-Shortcuts
            # button doesn't have to build JSON to say "what's the time".
            text = raw.decode("utf-8", "replace").strip()

        if not text:
            self._send(400, {"error": "empty command"})
            return

        print(f"[phone_api] {device}: {text[:60]}")
        self._send(200, {"reply": submit_command(text)})


def main():
    Handler.tokens = load_tokens()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)

    print(f"[phone_api] listening on {HOST}:{PORT}")
    print(f"[phone_api] tokens in {TOKENS_PATH}")
    for name, token in Handler.tokens.items():
        print(f"[phone_api]   {name}: {token}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
