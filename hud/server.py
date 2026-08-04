#!/usr/bin/env python3
"""
FRED HUD server — serves the Iron Man HUD page and its /state feed.

    python server.py              real telemetry, 127.0.0.1:8777
    python server.py --mock       scripted state loop + fake stats, :8778

Stdlib + psutil only. nvidia-smi is shelled out to for GPU numbers; if it
is missing (no NVIDIA card, driver not on PATH) every GPU field comes back
null and the HUD shows "--" rather than failing.

Loopback only, on purpose: this exposes live machine telemetry, and there
is no reason for anything off this box to be able to read it.

THE BUS
-------
State arrives as plain files under ~/voice-line/, read-only — this server
never writes the state/waveform/systems files, so whatever owns the bus can
never be corrupted by the HUD looking at them:

    ~/voice-line/state      one word: idle | listening | thinking | speaking | alert
    ~/voice-line/waveform   voice amplitude, one or more floats in 0..1

Neither file has to exist. Nothing there yet just means "idle", which is
also exactly what a crashed or stopped producer looks like — one code path
for both.

The one deliberate exception is the console's text box: typed text has
nowhere else to go, so POST /command writes command.json and waits on
command_reply.json — see submit_command(). FRED (Core/ui/pill_app.py) is
the only thing that reads the former and writes the latter.

Two rules make it robust against a producer that updates one file but not
the other:

  STOMP TOLERANCE — a fresh waveform outranks the state file. If audio is
  demonstrably flowing right now, the HUD says speaking even if the state
  file still reads "thinking" (or was never updated at all). Sound coming
  out of the speakers is the more trustworthy signal.

  STALENESS — a state file nobody has touched in STALE_SECONDS is ignored
  and the HUD eases back to idle. A producer that dies mid-turn therefore
  decays to calm instead of freezing the HUD in "thinking" forever.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import psutil
except ImportError:
    sys.exit("psutil is required:  pip install psutil")

try:
    import sounddevice as sd
except ImportError:
    sd = None  # /devices reports itself unavailable rather than crashing the server

HERE = Path(__file__).resolve().parent
BUS_DIR = Path.home() / "voice-line"

DEFAULT_PORT = 8777
MOCK_PORT = 8778

POLL_SECONDS = 1.0          # stats refresh; the page interpolates between these
STALE_SECONDS = 5.0         # state file older than this -> idle
STOMP_FRESH_SECONDS = 0.4   # waveform newer than this -> speaking, no argument
SPEAK_LEVEL_FLOOR = 0.02    # ignore a fresh-but-silent waveform

COMMAND_TIMEOUT = 45.0      # a "thinking" turn can run long; wait it out
COMMAND_POLL = 0.25

VALID_STATES = ("idle", "listening", "thinking", "speaking", "alert")

# Suppress the console window nvidia-smi would otherwise flash on Windows
# once a second when the server is launched with pythonw.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# =========================================================
# BUS
# =========================================================

def _parse_floats(text):
    out = []
    for chunk in text.replace(",", " ").split():
        try:
            out.append(float(chunk))
        except ValueError:
            pass
    return out


def read_bus():
    """(state, level) from ~/voice-line/. Read-only; never raises."""
    now = time.time()
    state = None
    state_age = None
    level = 0.0
    wave_age = None

    try:
        p = BUS_DIR / "state"
        if p.is_file():
            state_age = now - p.stat().st_mtime
            words = p.read_text(encoding="utf-8", errors="replace").split()
            if words and words[0].lower() in VALID_STATES:
                state = words[0].lower()
    except OSError:
        pass

    try:
        p = BUS_DIR / "waveform"
        if p.is_file():
            wave_age = now - p.stat().st_mtime
            vals = _parse_floats(p.read_text(encoding="utf-8", errors="replace"))
            if vals:
                # Peak of the recent tail: a pulse should track the loudest
                # part of the moment, not an average dragged down by gaps
                # between syllables.
                level = max(0.0, min(1.0, max(vals[-16:])))
    except OSError:
        pass

    if wave_age is not None and wave_age <= STOMP_FRESH_SECONDS and level > SPEAK_LEVEL_FLOOR:
        return "speaking", level

    if state is not None and state_age is not None and state_age <= STALE_SECONDS:
        return state, level

    return "idle", 0.0


def submit_command(text):
    """
    Hands typed text to FRED and waits for the matching reply.

    A fresh id per request means a slow reply from a stale request
    (FRED restarted, or two commands landed close together) is never
    mistaken for the answer to this one — only a command_reply.json
    whose id matches counts.
    """
    req_id = uuid.uuid4().hex
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


# =========================================================
# TELEMETRY
# =========================================================

_GPU_FIELDS = ("utilization.gpu", "memory.used", "memory.total",
               "temperature.gpu", "power.draw", "power.limit")


def read_gpu():
    """GPU numbers via nvidia-smi, or None if unavailable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + ",".join(_GPU_FIELDS),
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=_NO_WINDOW,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        parts = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]

        def num(i):
            # Fields a given card/driver doesn't support come back as
            # "[N/A]" rather than being omitted, so parse per-field.
            try:
                return float(parts[i])
            except (ValueError, IndexError):
                return None

        return {
            "gpu_pct": num(0), "vram_used_mb": num(1), "vram_total_mb": num(2),
            "gpu_temp_c": num(3), "power_w": num(4), "power_limit_w": num(5),
        }
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _wasapi_index():
    """PortAudio lists every physical device once per host API (MME,
    DirectSound, WASAPI, WDM-KS) — confirmed 2026-08-04: ~35 entries for
    4 real devices on a 2-mic/2-speaker machine. WASAPI is the one that
    matches what Windows itself calls the default device, so filtering
    to it removes the duplicates. See Core/audio/device_info.py's
    identical filter — duplicated rather than imported because this
    server is deliberately stdlib-only otherwise (see module docstring)
    and doesn't import the Core package."""
    try:
        for i, api in enumerate(sd.query_hostapis()):
            if api["name"] == "Windows WASAPI":
                return i
    except Exception:
        pass
    return None


def read_devices():
    """
    Mic/speaker catalog for the HUD's hover dropdown. The catalog itself
    (sd.query_devices()) is host-level and fine to read from this
    process; the *selection* is FRED's, published to the bus by
    Core/audio/device_info.py — see its module docstring for why that
    hop is needed instead of just reading sd.default.device here.
    """
    if sd is None:
        return {"input": [], "output": [], "selected_input": None, "selected_output": None}

    try:
        devices = list(enumerate(sd.query_devices()))
    except Exception:
        devices = []

    wasapi = _wasapi_index()
    if wasapi is not None:
        devices = [(i, d) for i, d in devices if d["hostapi"] == wasapi]

    inputs = [{"index": i, "name": d["name"]} for i, d in devices if d["max_input_channels"] > 0]
    outputs = [{"index": i, "name": d["name"]} for i, d in devices if d["max_output_channels"] > 0]

    selected_input = selected_output = None
    try:
        sel = json.loads((BUS_DIR / "audio_devices.json").read_text(encoding="utf-8"))
        selected_input = sel.get("input")
        selected_output = sel.get("output")
    except (OSError, ValueError):
        pass

    return {"input": inputs, "output": outputs,
            "selected_input": selected_input, "selected_output": selected_output}


def read_subsystems():
    """Which FRED models are resident, as published on the bus."""
    try:
        raw = (BUS_DIR / "systems").read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


SESSIONS_DIR = HERE.parent / "Core" / "data" / "logs" / "sessions"
TURN_WINDOW_SECONDS = 300      # radar scope depth
LOG_TAIL_BYTES = 64_000        # plenty for a day's tail; avoids re-reading a big file


def _session_events():
    """Today's session log, newest last. Only the tail is read."""
    path = SESSIONS_DIR / f"session_{time.strftime('%Y-%m-%d')}.jsonl"
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as f:
            if size > LOG_TAIL_BYTES:
                f.seek(size - LOG_TAIL_BYTES)
                f.readline()          # discard the partial line seek landed in
            lines = f.readlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events


def _ts(event):
    try:
        return time.mktime(time.strptime(event["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
    except (KeyError, ValueError, TypeError):
        return None


def read_turns(events):
    """
    Recent turns as {ago, duration}, for the radar scope.

    A turn is a user_speech paired with the next real reply. Filler is
    skipped deliberately — it is spoken before the model has produced
    anything, so pairing against it would report every turn as instant.
    """
    now = time.time()
    turns = []
    pending = None
    for event in events:
        kind = event.get("type")
        when = _ts(event)
        if when is None:
            continue
        if kind == "user_speech":
            pending = when
        elif kind == "fred_speech" and pending is not None and not event.get("filler"):
            if now - when <= TURN_WINDOW_SECONDS:
                turns.append({"ago": round(now - when, 1),
                              "duration": round(max(0.0, when - pending), 2)})
            pending = None
    return turns[-12:]


# Only events worth a line on a HUD — the log also carries bulky things
# (full transcripts, health checks) that would just be noise here.
_DIAG = {
    "tool_call":        lambda e: f"tool {e.get('tool', '?')}",
    "tool_event":       lambda e: str(e.get("label", "")).lower(),
    "ambiguous_choice": lambda e: f"ambiguous: {e.get('top')} / {e.get('alt')}",
    "error":            lambda e: f"error: {str(e.get('message', ''))[:48]}",
    "system":           lambda e: str(e.get("note", "")),
}


def read_diagnostics(events, limit=6):
    lines = []
    for event in events:
        fmt = _DIAG.get(event.get("type"))
        if not fmt:
            continue
        try:
            text = fmt(event)
        except Exception:
            continue
        if text:
            lines.append(text[:56])
    return lines[-limit:]


def _disk_pct():
    try:
        return psutil.disk_usage(str(HERE.anchor or "/")).percent
    except OSError:
        return None


def _swap_pct():
    try:
        return psutil.swap_memory().percent
    except (OSError, RuntimeError):
        return None


class Telemetry:
    """Polls in the background so an HTTP request never waits on nvidia-smi."""

    def __init__(self, mock=False):
        self.mock = mock
        self._lock = threading.Lock()
        self._snap = self._blank()
        self._t0 = time.time()
        psutil.cpu_percent(interval=None)      # prime the delta counter
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @staticmethod
    def _blank():
        return {"cpu": None, "ram_pct": None, "ram_used_gb": None,
                "ram_total_gb": None, "gpu_pct": None, "vram_used_mb": None,
                "vram_total_mb": None, "gpu_temp_c": None, "uptime_s": None,
                "disk_pct": None, "swap_pct": None,
                "power_w": None, "power_limit_w": None}

    def start(self):
        self._thread.start()

    def _sample(self):
        if self.mock:
            t = time.time() - self._t0
            wob = lambda p, lo, hi: lo + (hi - lo) * (0.5 + 0.5 * __import__("math").sin(t / p))
            return {
                "cpu": wob(7.0, 8, 82), "ram_pct": wob(11.0, 42, 71),
                "ram_used_gb": wob(11.0, 13.4, 22.7), "ram_total_gb": 32.0,
                "gpu_pct": wob(5.0, 3, 97), "vram_used_mb": wob(9.0, 1200, 12800),
                "vram_total_mb": 16311.0, "gpu_temp_c": wob(13.0, 38, 79),
                "uptime_s": 93600 + t,
                "disk_pct": wob(29.0, 61, 68), "swap_pct": wob(19.0, 4, 31),
                "power_w": wob(6.0, 14, 168), "power_limit_w": 180.0,
            }

        vm = psutil.virtual_memory()
        snap = {
            "cpu": psutil.cpu_percent(interval=None),
            "ram_pct": vm.percent,
            "ram_used_gb": (vm.total - vm.available) / (1024 ** 3),
            "ram_total_gb": vm.total / (1024 ** 3),
            "uptime_s": time.time() - psutil.boot_time(),
            "disk_pct": _disk_pct(),
            "swap_pct": _swap_pct(),
        }
        gpu = read_gpu()
        snap.update(gpu if gpu else {
            "gpu_pct": None, "vram_used_mb": None, "vram_total_mb": None,
            "gpu_temp_c": None, "power_w": None, "power_limit_w": None,
        })
        return snap

    def _loop(self):
        while True:
            try:
                snap = self._sample()
                with self._lock:
                    self._snap = snap
            except Exception as e:                      # never let the thread die
                print(f"[hud] telemetry sample failed: {e}", file=sys.stderr)
            time.sleep(POLL_SECONDS)

    def snapshot(self):
        with self._lock:
            return dict(self._snap)


# =========================================================
# MOCK STATE LOOP
# =========================================================

# Scripted so a full pass exercises every state in a fixed, repeatable
# order — that is the loop the verification pass walks.
MOCK_SCRIPT = [("idle", 4), ("listening", 3), ("thinking", 4),
               ("speaking", 6), ("alert", 3)]
MOCK_CYCLE = sum(d for _s, d in MOCK_SCRIPT)


def mock_turns(t0):
    """A fixed spread of turns so the radar has blips at every radius."""
    elapsed = time.time() - t0
    return [{"ago": (elapsed * 3 + i * 47) % TURN_WINDOW_SECONDS,
             "duration": 1.5 + (i * 2.3) % 9}
            for i in range(5)]


def mock_bus(t0):
    import math
    t = (time.time() - t0) % MOCK_CYCLE
    acc = 0.0
    for state, dur in MOCK_SCRIPT:
        if t < acc + dur:
            if state == "speaking":
                # Bursty envelope, so the core pulse looks like speech
                # rather than a sine wave.
                p = t - acc
                env = max(0.0, math.sin(p * 4.1)) ** 2
                return state, min(1.0, env * (0.55 + 0.45 * abs(math.sin(p * 9.7))))
            return state, 0.0
        acc += dur
    return "idle", 0.0


# =========================================================
# HTTP
# =========================================================

class Handler(BaseHTTPRequestHandler):
    server_version = "FredHUD/1.0"
    telemetry = None
    mock = False
    mock_t0 = 0.0

    def log_message(self, fmt, *args):
        pass  # the default access log is pure noise at 1 req/sec

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in ("/", "/index.html"):
            try:
                body = (HERE / "index.html").read_bytes()
            except OSError:
                self._send(500, b"index.html missing", "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8",
                       {"Cache-Control": "no-store"})
            return

        if path == "/state":
            if self.mock:
                state, level = mock_bus(self.mock_t0)
                payload = self.telemetry.snapshot()
                payload["subsystems"] = {"llm": True, "whisper": True, "kokoro": True, "muted": False}
                payload["turns"] = mock_turns(self.mock_t0)
                payload["diagnostics"] = [
                    "telemetry uplink established", "diagnostics pass 04 complete",
                    "harmonic drift corrected", "shield lattice re-knit",
                    "tool get_current_time", "gyro re-trim +0.05 deg",
                ]
            else:
                state, level = read_bus()
                payload = self.telemetry.snapshot()
                payload["subsystems"] = read_subsystems()
                events = _session_events()
                payload["turns"] = read_turns(events)
                payload["diagnostics"] = read_diagnostics(events)
            payload["state"] = state
            payload["level"] = round(level, 4)
            payload["ts"] = round(time.time(), 3)
            body = json.dumps(payload).encode("utf-8")
            self._send(200, body, "application/json",
                       {"Cache-Control": "no-store"})
            return

        if path == "/devices":
            body = json.dumps(read_devices()).encode("utf-8")
            self._send(200, body, "application/json", {"Cache-Control": "no-store"})
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/command":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            text = str(data.get("text", "")).strip()
        except (ValueError, TypeError):
            text = ""

        if not text:
            self._send(400, b'{"reply":"say something first"}', "application/json")
            return

        if self.mock:
            # Nothing is listening on the bus in mock mode — echo back so
            # the console still feels alive during a HUD-only preview.
            reply = f'Mock mode, no FRED listening. You said: "{text}"'
        else:
            reply = submit_command(text)

        body = json.dumps({"reply": reply}).encode("utf-8")
        self._send(200, body, "application/json", {"Cache-Control": "no-store"})


def main():
    ap = argparse.ArgumentParser(description="FRED HUD server")
    ap.add_argument("--mock", action="store_true",
                    help=f"scripted state loop + fake stats, on :{MOCK_PORT}")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    port = args.port or (MOCK_PORT if args.mock else DEFAULT_PORT)

    tel = Telemetry(mock=args.mock)
    tel.start()

    Handler.telemetry = tel
    Handler.mock = args.mock
    Handler.mock_t0 = time.time()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    mode = "MOCK" if args.mock else "live"
    print(f"[hud] {mode} on http://127.0.0.1:{port}/  (pid {os.getpid()})")
    if not args.mock:
        print(f"[hud] bus: {BUS_DIR}"
              f"{'' if BUS_DIR.is_dir() else '  (absent — will read as idle)'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[hud] stopped")


if __name__ == "__main__":
    main()
