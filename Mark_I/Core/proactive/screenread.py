"""Screen reader: about once a second, OCR the FOREGROUND WINDOW and hand the text to the watchers (scam watch, deadline watch). No model.

The tile diff of senses/screen.py is used only to skip the OCR when nothing changed since the last read. It never waits for stillness: a window that keeps changing (video,
animation) is read every second. Changes of screen_noise_tiles tiles or fewer (a caret, a clock) do not count, but the window is still re-read every screen_reread_s, and at once
when the foreground window or its title changes. Only the foreground window is read, never the whole frame (text of other windows is not mixed in).

Sockets
  in : Reader(bus, screen, ocr_read=None, foreground=None, clock=time.monotonic).start() / stop() / tick()
       screen = senses.screen.ScreenSense (latest(), crop(bbox, pad));  ocr_read(rgb) -> str|None (default senses.ocr.read);  foreground() -> (hwnd, title, (l, t, r, b)) (default win32gui)
       add(fn, always=False)   fn(text, info) with info = {"title", "hwnd"}; called only when the text of the window changed
                               always=True (the scam watch): called whenever the reader runs, whatever the pause switch says; others only while not proactive.gate.paused()
       fullscreen() -> bool    the foreground window covers the whole frame (a video or a game)
  out: the texts go to the callbacks only, never to the bus or the logs (stats() has counts). Nothing is read when no callback is interested (paused and no scam watch).
       an OCR engine that is missing stops the reader and logs ocr_unavailable once.
Self-check: python proactive/screenread.py
"""
import hashlib
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log
from proactive import gate


def win_foreground():
    import win32gui
    h = win32gui.GetForegroundWindow()
    return (h, win32gui.GetWindowText(h), win32gui.GetWindowRect(h)) if h else (0, "", (0, 0, 0, 0))


class Reader:
    def __init__(self, bus, screen, ocr_read=None, foreground=None, clock=time.monotonic):
        self.bus, self.screen, self._ocr, self._fg, self.clock = bus, screen, ocr_read, foreground or win_foreground, clock
        self._subs: list = []                                   # (fn, always)
        self._dirty, self._key, self._hash, self._last, self._was_paused = True, None, None, -1e12, False
        self._full = False
        self._n = {"reads": 0, "skips": 0, "ocr_ms": 0.0}
        self._unsub, self._thread, self._stop = None, None, threading.Event()

    def add(self, fn, always: bool = False):
        self._subs.append((fn, always))

    def fullscreen(self) -> bool:
        return self._full

    def stats(self) -> dict:
        return {**self._n, "ocr_ms": round(self._n["ocr_ms"] / max(1, self._n["reads"]), 1)}

    def _on_change(self, e):
        if e.data.get("tiles", 0) > config.get().screen_noise_tiles:
            self._dirty = True

    def tick(self) -> str:
        """One step: 'idle' (nobody wants text), 'noframe', 'skip' (nothing changed), 'same' (read, same text), 'read' (callbacks called)."""
        c, p = config.get(), gate.paused()
        subs = [fn for fn, always in self._subs if always or not p]
        if p != self._was_paused:
            self._was_paused, self._hash = p, None                  # after a pause every watcher sees the window afresh
        if not subs:
            return "idle"
        frame = self.screen.latest()
        if frame is None:
            return "noframe"
        h, title, rect = self._fg()
        fh, fw = frame.shape[:2]
        l, t, r, b = max(0, rect[0]), max(0, rect[1]), min(fw, rect[2]), min(fh, rect[3])
        if r - l < 40 or b - t < 40:
            return "noframe"                                        # minimised or nothing to read
        self._full = rect[0] <= 0 and rect[1] <= 0 and rect[2] >= fw and rect[3] >= fh
        now, key = self.clock(), (h, title)
        if not (self._dirty or key != self._key or now - self._last >= c.screen_reread_s):
            self._n["skips"] += 1
            return "skip"
        self._dirty, self._key, self._last = False, key, now
        if self._ocr is None:
            from senses import ocr
            self._ocr = ocr.read
        t0 = time.perf_counter()
        try:
            text = self._ocr(self.screen.crop((l, t, r, b), pad=0)) or ""
        except Exception as ex:
            if type(ex).__name__ == "OcrUnavailable":
                log.session("ocr_unavailable", error=str(ex)[:80])
                self._stop.set()
                return "idle"
            raise
        self._n["reads"] += 1
        self._n["ocr_ms"] += (time.perf_counter() - t0) * 1000
        digest = hashlib.sha1(text.encode("utf-8", "ignore")).digest()
        if digest == self._hash or not text.strip():
            return "same"
        self._hash = digest
        for fn in subs:
            try:
                fn(text, {"title": title, "hwnd": h})
            except Exception as ex:
                log.session("screenread_watcher_error", error=type(ex).__name__)
        return "read"

    def _run(self):
        while not self._stop.wait(config.get().screen_read_s):
            try:
                self.tick()
            except Exception as ex:
                log.session("screenread_error", error=type(ex).__name__)

    def start(self):
        if self._thread:
            return
        self._unsub = self.bus.subscribe(self._on_change, kinds=("change",), sources=("screen",))
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="screenread")
        self._thread.start()

    def stop(self):
        self._unsub and self._unsub()
        self._stop.set()
        self._thread and self._thread.join(3)
        self._thread = self._unsub = None


def _selfcheck():
    import tempfile
    import numpy as np
    import events as ev
    from senses.screen import ListSource, ScreenSense
    config.set_data_dir(tempfile.mkdtemp())
    bus = ev.EventBus()
    base = np.full((720, 1280, 4), 40, dtype=np.uint8)
    s = ScreenSense(bus, ListSource([base]))
    s.tick()
    win = {"v": (7, "Course portal", (100, 50, 900, 600))}
    ocr_calls, got, scam = [], [], []
    texts = {"v": "Physics lab due Friday 17 Oct"}
    def ocr(rgb):
        ocr_calls.append(rgb.shape)
        return texts["v"]
    now = [0.0]
    r = Reader(bus, s, ocr_read=ocr, foreground=lambda: win["v"], clock=lambda: now[0])
    r.add(lambda t, i: got.append((t, i["title"])))
    r.add(lambda t, i: scam.append(t), always=True)
    r._unsub = bus.subscribe(r._on_change, kinds=("change",), sources=("screen",))
    assert r.tick() == "read" and ocr_calls == [(550, 800, 3)] and got == [("Physics lab due Friday 17 Oct", "Course portal")]     # only the window rect is read
    assert r.tick() == "skip" and len(ocr_calls) == 1                                                                              # nothing changed
    bus.emit("screen", "change", tiles=2)                                                                                           # a caret: noise
    assert r.tick() == "skip"
    now[0] = 11.0
    assert r.tick() == "same" and len(ocr_calls) == 2 and len(got) == 1                                                             # periodic re-read, same text: no callback
    bus.emit("screen", "change", tiles=3)
    texts["v"] = "Physics lab due Friday 17 Oct\nSubmit by 6 pm"
    assert r.tick() == "read" and len(got) == 2                                                                                     # a real change is read at once, no waiting for stillness
    for i in range(3):                                                                                                              # continuous change (a video): a read every tick
        bus.emit("screen", "change", tiles=500)
        texts["v"] = f"frame {i} of a video"
        assert r.tick() == "read"
    win["v"] = (8, "Other", (0, 0, 1280, 720))
    texts["v"] = "other window"
    assert r.tick() == "read" and r.fullscreen()                                                                                    # a new foreground window is read at once; it covers the screen
    n_scam, n_got = len(scam), len(got)
    gate.set_paused(True)
    texts["v"] = "something new"
    bus.emit("screen", "change", tiles=50)
    assert r.tick() == "read" and len(scam) == n_scam + 1 and len(got) == n_got                                                     # paused: the scam watch still reads, the others do not
    gate.set_paused(False)
    bus.emit("screen", "change", tiles=50)
    assert r.tick() == "read" and len(got) == n_got + 1                                                                             # resumed: the same text reaches the others afresh
    win["v"] = (9, "min", (-32000, -32000, -31840, -31972))
    assert r.tick() == "noframe"
    r2 = Reader(bus, s, ocr_read=ocr, foreground=lambda: win["v"])
    gate.set_paused(True)
    assert r2.tick() == "idle" and r2.stats()["reads"] == 0                                                                         # paused and no scam watch: nothing is read
    gate.set_paused(False)
    def boom(rgb):
        raise type("OcrUnavailable", (RuntimeError,), {})("no engine")
    win["v"] = (7, "t", (0, 0, 800, 600))
    r3 = Reader(bus, s, ocr_read=boom, foreground=lambda: win["v"])
    r3.add(lambda t, i: None)
    assert r3.tick() == "idle" and r3._stop.is_set()
    r4 = Reader(bus, s, ocr_read=ocr, foreground=lambda: (7, "live", (0, 0, 800, 600)))                                              # the thread
    r4.add(lambda t, i: got.append(t))
    config.get().screen_read_s = 0.05
    r4.start()
    time.sleep(0.4)
    r4.stop()
    assert r4.stats()["reads"] >= 1 and r4._thread is None
    config.reload()
    print("screenread ok")


if __name__ == "__main__":
    _selfcheck()
