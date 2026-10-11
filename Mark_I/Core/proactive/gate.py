"""Speak gate and pause switch for unasked screen offers. Rule based, fixed, no model (the judge stays in shadow mode).

Sockets
  in : Gate(bus, clock=time.time, now=None, fullscreen=None, present=None)       listens to the bus: audio.vad / audio.hotkey (user active), clock.state (mid-turn), tts.start / tts.end, presence.state
       gate.decide(urgent=False) -> "speak" | "toast" | "hold"                    pure given the state; gate.note_spoken() records a spoken offer for the caps
       fullscreen() -> bool (the reader's view: foreground window covers the screen); now() -> aware datetime (tests)
       paused() / set_paused(on)                                                  the pause switch: config.screen_paused OR the file <data_dir>/screen_paused.flag
  out: hold  = wait (user talking, mid-turn, FRED speaking or spoke < gate_after_fred_s ago, user away): ask again later
       toast = toast only (quiet hours, fullscreen video, or the spoken caps are used up)
       speak = go ahead
       urgent (a deadline closer than deadline_urgent_h) overrides quiet hours, fullscreen and the caps, but never the holds.
Quiet hours: quiet_start..quiet_end (23:30..06:00); when the user was active (spoke, hotkey) in the last quiet_active_min the quiet period starts later, up to quiet_latest (01:00).
Caps: at most one spoken offer per offer_gap_s and offers_per_day per day.
The scam watch does not use this gate and is not paused by the switch.
Self-check: python proactive/gate.py
"""
import sys
import time
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


def _flag() -> Path:
    return config.data_dir() / "screen_paused.flag"


def paused() -> bool:
    return config.get().screen_paused or _flag().exists()


def set_paused(on: bool):
    if on:
        _flag().write_text("paused", encoding="utf-8")
    else:
        _flag().unlink(missing_ok=True)
        config.get().screen_paused = False


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class Gate:
    def __init__(self, bus, clock=time.time, now=None, fullscreen=None, present=None):
        self.clock, self._now, self.fullscreen, self.present = clock, now or (lambda: datetime.now(config.IST)), fullscreen or (lambda: False), present
        self.talking, self.turn_at, self.speaking, self.spoke_at, self.active_at, self.here = False, -1e12, False, -1e12, -1e12, True
        self.offers: list = []                                              # epoch seconds of spoken offers
        bus.subscribe(self._on, kinds=("vad", "hotkey", "state", "start", "end"))

    def _on(self, e):
        k, s, d, t = e.kind, e.source, e.data, self.clock()
        if s == "audio" and k == "vad":
            self.talking = bool(d.get("speaking"))
            self.talking and setattr(self, "active_at", t)
        elif s == "audio" and k == "hotkey" and d.get("down"):
            self.active_at = t
        elif s == "clock" and k == "state":
            if d.get("state") == "ADDRESSED":
                self.turn_at, self.active_at = t, t
            elif d.get("state") in ("IGNORE", "LISTEN"):
                self.turn_at = -1e12
        elif s == "tts" and k == "start":
            self.speaking = True
        elif s == "tts" and k == "end":
            self.speaking, self.spoke_at, self.turn_at = False, t, -1e12
        elif s == "presence" and k == "state":
            self.here = bool(d.get("present"))

    def quiet(self) -> bool:
        c, n = config.get(), self._now()
        start = _mins(c.quiet_start)
        off = (n.hour * 60 + n.minute - start) % 1440                       # minutes since quiet_start
        if off >= (_mins(c.quiet_end) - start) % 1440:
            return False
        extended = (_mins(c.quiet_latest) - start) % 1440
        return not (off < extended and self.clock() - self.active_at <= c.quiet_active_min * 60)

    def decide(self, urgent: bool = False) -> str:
        c, t = config.get(), self.clock()
        if self.talking or self.speaking or t - self.turn_at < c.gate_turn_s or t - self.spoke_at < c.gate_after_fred_s or not self.here:
            return "hold"
        if urgent:
            return "speak"
        if self.quiet() or self.fullscreen():
            return "toast"
        day = self._now().date()
        if self.offers and t - self.offers[-1] < c.offer_gap_s or sum(datetime.fromtimestamp(o, config.IST).date() == day for o in self.offers) >= c.offers_per_day:
            return "toast"
        return "speak"

    def note_spoken(self):
        self.offers = self.offers[-50:] + [self.clock()]


def _selfcheck():
    import tempfile
    import events as ev
    config.set_data_dir(tempfile.mkdtemp())
    hour = [datetime(2026, 10, 12, 15, 0, tzinfo=config.IST)]
    bus, clk = ev.EventBus(), [hour[0].timestamp()]
    fs = [False]
    g = Gate(bus, clock=lambda: clk[0], now=lambda: hour[0], fullscreen=lambda: fs[0])
    assert g.decide() == "speak" and g.decide(urgent=True) == "speak"
    bus.emit("audio", "vad", speaking=True)
    assert g.decide(urgent=True) == "hold"                                  # holds beat urgency
    bus.emit("audio", "vad", speaking=False)
    assert g.decide() == "speak"
    bus.emit("clock", "state", state="ADDRESSED")
    assert g.decide() == "hold"
    clk[0] += 46
    assert g.decide() == "speak"                                            # a turn that never ended expires
    bus.emit("tts", "start")
    assert g.decide() == "hold"
    bus.emit("tts", "end")
    assert g.decide() == "hold"
    clk[0] += 121
    assert g.decide() == "speak"
    bus.emit("presence", "state", present=False)
    assert g.decide() == "hold"
    bus.emit("presence", "state", present=True)
    fs[0] = True
    assert g.decide() == "toast" and g.decide(urgent=True) == "speak"
    fs[0] = False
    g.note_spoken()
    assert g.decide() == "toast" and g.decide(urgent=True) == "speak"       # 10 min gap
    clk[0] += 601
    assert g.decide() == "speak"
    for _ in range(10):
        g.offers.append(clk[0] - 700)
    assert g.decide() == "toast"                                            # 10 a day
    g.offers.clear()
    at = lambda h, m: datetime(2026, 10, 12, h, m, tzinfo=config.IST)
    for (h, m), q in (((23, 29), False), ((23, 31), True), ((3, 0), True), ((5, 59), True), ((6, 1), False)):
        hour[0] = at(h, m)
        g.active_at = -1e12
        assert g.quiet() is q, (h, m)
        assert g.decide() == ("toast" if q else "speak") and g.decide(urgent=True) == "speak"
    hour[0] = at(23, 50)                                                    # active in the last 20 min: quiet starts later, up to 01:00
    g.active_at = clk[0] - 600
    assert not g.quiet()
    g.active_at = clk[0] - 1300
    assert g.quiet()
    hour[0] = at(1, 10)
    g.active_at = clk[0] - 60
    assert g.quiet()                                                        # past 01:00 nothing extends it
    hour[0] = at(0, 30)
    assert not g.quiet()
    assert not paused()
    set_paused(True)
    assert paused() and _flag().exists()
    set_paused(False)
    assert not paused()
    config.get().screen_paused = True
    assert paused()
    config.reload()
    print("gate ok")


if __name__ == "__main__":
    _selfcheck()
