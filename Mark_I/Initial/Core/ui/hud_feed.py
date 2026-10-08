"""Feeds the HUD (ui/hud/server.py, the browser page) from the event bus, over the plain-file bus in ~/voice-line/ that the page's server reads: state, waveform, a heartbeat, and the typed console.

Same contract as the old build's voice_line.py, so the HUD page and server are unchanged:
  state     one word: idle | listening | thinking | speaking | alert; re-stamped every second while not idle (the reader ignores a state file older than 5 s)
  waveform  FRED's voice level 0..1, written only while speaking, at most 10 per second (a fresh waveform makes the reader say "speaking" even if the state file is stale)
  command.json / command_reply.json   text typed in the HUD console: on_command(text) -> reply text is answered into the reply file; a command older than 15 s is ignored
Everything is best effort: a failed write never reaches FRED, and FRED runs the same with no HUD open.

Sockets
  in : HudFeed(bus, speaker=None, on_command=None, bus_dir=None).start() / stop()      bus events as the pill: clock.state (IGNORE returns a listening HUD to idle), tts.start/end, proactive.warn (-> alert, held 2.5 s); a listening/thinking state that nothing resolves falls back to idle after 60 s
  out: files under bus_dir (default ~/voice-line); stop() leaves the state at idle
Self-check: python ui/hud_feed.py (temp folder)
"""
import json
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import log

WAVE_EVERY_S, BEAT_S, CMD_POLL_S, CMD_MAX_AGE_S, ALERT_HOLD_S, STUCK_S = 0.1, 1.0, 0.4, 15.0, 2.5, 60.0
STATES = {("clock", "LISTEN"): "listening", ("clock", "BARGE_IN"): "listening", ("clock", "END_OF_TURN"): "thinking"}


class HudFeed:
    def __init__(self, bus, speaker=None, on_command=None, bus_dir=None):
        self.bus, self.speaker, self.on_command = bus, speaker, on_command
        self.dir = Path(bus_dir) if bus_dir else Path.home() / "voice-line"
        self.state, self._alert_until, self._seen, self._stop, self._unsub = "idle", 0.0, None, threading.Event(), None
        self._since = time.monotonic()

    def _write(self, name, text):
        try:
            (self.dir / name).write_text(text, encoding="utf-8")
        except OSError:
            pass                                                         # never a reason to fail a turn

    def _set(self, state):
        self.state, self._since = state, time.monotonic()
        if state != "alert" and time.monotonic() < self._alert_until:
            return                                                       # an alert holds the HUD red for a moment: it polls only about once a second
        self._write("state", state)

    def _on(self, e):
        if e.source == "clock" and e.kind == "state":
            s = STATES.get(("clock", e.data.get("state")))
            if s:
                self._set(s)
            elif e.data.get("state") == "IGNORE" and self.state == "listening":
                self._set("idle")                                        # a false wake: without this the HUD would stay "listening" (and keep its heartbeat) for ever
        elif (e.source, e.kind) == ("tts", "start"):
            self._set("speaking")
        elif (e.source, e.kind) == ("tts", "end"):
            self._set("idle")
        elif (e.source, e.kind) == ("proactive", "warn"):
            self._alert_until = time.monotonic() + ALERT_HOLD_S
            self.state = "alert"
            self._write("state", "alert")

    def start(self):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        self._write("state", "idle")
        self._unsub = self.bus.subscribe(self._on, sources=("clock", "tts", "proactive"))
        self._stop.clear()
        threading.Thread(target=self._loop, daemon=True, name="hud-feed").start()

    def stop(self):
        self._stop.set()
        self._unsub and self._unsub()
        self._write("state", "idle")

    def _loop(self):
        last_beat = last_wave = last_cmd = 0.0
        while not self._stop.wait(0.05):
            now = time.monotonic()
            if self._alert_until and now >= self._alert_until:
                self._alert_until = 0.0
                self.state = "idle" if self.state == "alert" else self.state
                self._write("state", self.state)
            if self.state in ("listening", "thinking") and now - self._since > STUCK_S:
                self._set("idle")                                        # a turn that never answered
            if self.state == "speaking" and self.speaker and now - last_wave >= WAVE_EVERY_S:
                last_wave = now
                self._write("waveform", f"{min(1.0, max(0.0, self.speaker.level)):.4f}")
            if self.state != "idle" and now - last_beat >= BEAT_S:
                last_beat = now
                self._write("state", self.state)
            if now - last_cmd >= CMD_POLL_S:                             # not every 50 ms: that was a file read twenty times a second, all day
                last_cmd = now
                self._command(now)

    def _command(self, now):
        p = self.dir / "command.json"
        try:
            age = time.time() - p.stat().st_mtime
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        rid, text = data.get("id"), str(data.get("text", "")).strip()
        if not rid or not text or rid == self._seen or not self.on_command:
            return
        self._seen = rid
        if age > CMD_MAX_AGE_S:
            return                                                       # left over from a closed tab: nobody is waiting

        def work():
            try:
                reply = self.on_command(text)
            except Exception as ex:
                log.session("hud_error", error=type(ex).__name__)
                reply = "I hit a problem and couldn't finish that."
            self._write("command_reply.json", json.dumps({"id": rid, "text": reply}))
        threading.Thread(target=work, daemon=True).start()


def _selfcheck():
    import tempfile
    import events as ev
    d, bus = Path(tempfile.mkdtemp()), ev.EventBus()
    sp = type("S", (), {"level": 0.5})()
    got = []
    f = HudFeed(bus, sp, on_command=lambda t: got.append(t) or f"echo {t}", bus_dir=d)
    f.start()
    rd = lambda n: (d / n).read_text(encoding="utf-8")
    assert rd("state") == "idle"
    bus.emit("clock", "state", state="LISTEN")
    assert rd("state") == "listening"
    bus.emit("clock", "state", state="END_OF_TURN")
    assert rd("state") == "thinking"
    bus.emit("clock", "state", state="IGNORE")
    assert rd("state") == "thinking"                                                       # IGNORE only undoes a listening state
    bus.emit("tts", "start")
    time.sleep(0.4)
    assert rd("state") == "speaking" and float(rd("waveform")) == 0.5                      # level only while speaking
    bus.emit("tts", "end")
    assert rd("state") == "idle"
    bus.emit("proactive", "warn", title="x")
    assert rd("state") == "alert"
    bus.emit("tts", "end")
    assert rd("state") == "alert"                                                          # the alert holds against a quick return to idle
    time.sleep(ALERT_HOLD_S + 0.3)
    assert rd("state") == "idle"
    (d / "command.json").write_text(json.dumps({"id": "a1", "text": "what time is it"}), encoding="utf-8")
    t0 = time.time()
    while not (d / "command_reply.json").exists() and time.time() - t0 < 3:
        time.sleep(0.05)
    assert json.loads(rd("command_reply.json")) == {"id": "a1", "text": "echo what time is it"}
    time.sleep(1)
    assert got == ["what time is it"]                                                      # answered once, not on every poll
    bus.emit("clock", "state", state="LISTEN")
    bus.emit("clock", "state", state="IGNORE")
    assert rd("state") == "idle"                                                           # a false wake word does not leave the HUD listening
    globals()["STUCK_S"] = 0.3
    bus.emit("clock", "state", state="END_OF_TURN")
    time.sleep(0.8)
    assert rd("state") == "idle"                                                           # nothing answered: back to idle
    f.stop()
    assert rd("state") == "idle"
    print("hud feed ok")


if __name__ == "__main__":
    _selfcheck()
