"""System alerts: battery and disk emergencies as bus events, so the judge's critical rule has something to fire on. (P6)

Sockets
  in : SystemWatch(bus, read=None, now=time.time).start() / stop() / tick()      read() -> {"battery": percent|None, "plugged": bool, "disk_free_gb": float, "disk_free_pct": float}  (default psutil)
  out: bus event system.alert {text, critical: True, what: "battery"|"disk"}   when battery <= alert_battery_pct and unplugged, or the system drive has < alert_disk_gb free or < alert_disk_pct percent free
       one alert per kind per alert_cooldown_s (30 min); a desktop has no battery (read gives None) and never alerts on it. Nothing is spoken here: the judge decides (shadow mode now), speech wording comes later.
Self-check: python proactive/alerts.py
"""
import os
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


def read() -> dict:
    import psutil
    b, d = psutil.sensors_battery(), psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
    return {"battery": b.percent if b else None, "plugged": b.power_plugged if b else True, "disk_free_gb": d.free / 1e9, "disk_free_pct": 100 - d.percent}


class SystemWatch:
    def __init__(self, bus, read=read, now=time.time):
        self.bus, self.read, self.now, self._last, self._stop = bus, read, now, {}, threading.Event()

    def tick(self):
        c, s = config.get(), self.read()
        found = []
        if s["battery"] is not None and not s["plugged"] and s["battery"] <= c.alert_battery_pct:
            found.append(("battery", f"battery at {s['battery']:.0f} percent"))
        if s["disk_free_gb"] < c.alert_disk_gb or s["disk_free_pct"] < c.alert_disk_pct:
            found.append(("disk", f"system drive nearly full, {s['disk_free_gb']:.0f} GB free"))
        for what, text in found:
            if self.now() - self._last.get(what, -1e12) >= c.alert_cooldown_s:
                self._last[what] = self.now()
                self.bus.emit("system", "alert", text=text, critical=True, what=what)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.wait(60.0):
            try:
                self.tick()
            except Exception:
                pass

    def stop(self):
        self._stop.set()


def _selfcheck():
    import events as ev
    config.reload()
    bus, got, t, st = ev.EventBus(), [], [0.0], {"battery": 50, "plugged": False, "disk_free_gb": 200.0, "disk_free_pct": 40.0}
    bus.subscribe(lambda e: got.append(e.data), kinds=("alert",), sources=("system",))
    w = SystemWatch(bus, lambda: dict(st), lambda: t[0])
    w.tick(); assert not got                                                         # healthy
    st["battery"] = 8; w.tick(); w.tick()
    assert got == [{"text": "battery at 8 percent", "critical": True, "what": "battery"}]    # once, not every tick
    st["plugged"] = True; t[0] = 5000; w.tick(); assert len(got) == 1                # charging: no alert
    st.update(plugged=False, disk_free_gb=4.0, disk_free_pct=2.0); w.tick()
    assert [g["what"] for g in got] == ["battery", "battery", "disk"]                # cooldown over: battery again; disk is new
    st.update(battery=None, plugged=True, disk_free_gb=200.0, disk_free_pct=40.0); t[0] = 99999; w.tick(); assert len(got) == 3   # desktop with a healthy disk
    print("alerts ok")


if __name__ == "__main__":
    _selfcheck()
