"""Glance: look at the screen while the user is talking, so the description is ready when the words are.

Frames are taken at speech start, again every `glance_mid_s` while the user is still talking (up to `glance_max_frames` in all, the end frame included), and at speech end.
Each frame is OCR'd (CPU) and described by the vision slot in its own thread through gpu.run at SENSE priority: the start/mid descriptions run while the user speaks (GPU
otherwise idle), so only the end frame can add latency, and a frame identical to the previous one (sha256 of a downscaled copy) is not described again.
Whisper (INTERACTIVE) jumps ahead of any queued description, so a glance never delays the transcript.

Sockets
  in : bus event audio.vad {speaking}  (from audio/vad.py);   ScreenSense.latest() for the pixels
  out: Glance.take(timeout=8) -> list[Description], oldest first, for the utterance that just ended (waits for unfinished descriptions; on timeout returns what is done)
       bus event screen.glance {frames, skipped, ms} when the end frame's description is done
       Description.text is generic ("what is on the screen"), grounded by the frame's own OCR text; question-specific answers still go through Describer.describe(question=...)
Failures (no frame yet, model down, GPU timeout) are logged on the bus as screen.glance_error and the frame is dropped; they never raise into the audio thread.
Self-check: python senses/glance.py
"""
import hashlib
import sys
import threading
import time
from pathlib import Path

import cv2

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
import gpu
from senses import ocr
from senses.describe import Describer


def _fp(rgb) -> bytes:
    return hashlib.sha256(cv2.resize(rgb, (96, 54), interpolation=cv2.INTER_AREA).tobytes()).digest()


class Glance:
    def __init__(self, bus: ev.EventBus, screen, describer: Describer | None = None):
        self.bus, self.screen, self.describer = bus, screen, describer or Describer(bus=bus)
        self._lock = threading.RLock()
        self._frames: list[threading.Thread] = []
        self._out: list = []              # (order, Description)
        self._last_fp = None
        self._skipped = 0
        self._timer: threading.Timer | None = None
        self._speaking = False
        self._t0 = 0.0
        self._unsub = bus.subscribe(self._on, kinds={"vad"}, sources={"audio"})

    def close(self):
        self._unsub()
        if self._timer:
            self._timer.cancel()

    def _on(self, e):
        if e.data.get("speaking"):
            with self._lock:
                self._frames, self._out, self._last_fp, self._skipped, self._t0, self._speaking = [], [], None, 0, time.monotonic(), True
            self._snap()
            self._arm()
        else:
            with self._lock:
                self._speaking = False
            if self._timer:
                self._timer.cancel()
            self._snap(final=True)

    def _arm(self):
        with self._lock:
            if not self._speaking or len(self._frames) >= config.get().glance_max_frames - 1:   # keep the last slot for the end frame
                return
            self._timer = threading.Timer(config.get().glance_mid_s, lambda: (self._snap(), self._arm()) if self._speaking else None)
            self._timer.daemon = True
            self._timer.start()

    def _snap(self, final=False):
        rgb = self.screen.crop()                      # full frame copy, RGB
        if rgb is None:
            return self.bus.emit("screen", "glance_error", error="no frame yet")
        fp = _fp(rgb)
        with self._lock:
            if fp == self._last_fp:
                self._skipped += 1
                return self._finish() if final else None
            self._last_fp, order = fp, len(self._frames)
            t = threading.Thread(target=self._work, args=(rgb, order, final), daemon=True)
            self._frames.append(t)
        t.start()

    def _work(self, rgb, order, final):
        try:
            d = gpu.run("glance", gpu.SENSE, lambda: self.describer.describe(rgb, ocr_text=ocr.read(rgb)), timeout=30)
            with self._lock:
                self._out.append((order, d))
        except Exception as ex:
            self.bus.emit("screen", "glance_error", error=repr(ex))
        if final:
            self._finish()

    def _finish(self):
        with self._lock:
            n, sk = len(self._frames), self._skipped
        self.bus.emit("screen", "glance", frames=n, skipped=sk, ms=int((time.monotonic() - self._t0) * 1000))

    def take(self, timeout: float = 8.0) -> list:
        end = time.monotonic() + timeout
        with self._lock:
            threads = list(self._frames)
        for t in threads:
            t.join(max(0.0, end - time.monotonic()))
        with self._lock:
            return [d for _, d in sorted(self._out, key=lambda x: x[0])]


def _selfcheck():
    import numpy as np
    from models.fake import FakeChat
    from senses.screen import ListSource, ScreenSense

    config.get().glance_mid_s = 0.15
    config.get().ocr_enabled = False                     # OCR is covered in ocr.py; here descriptions are unverified
    bus = ev.EventBus()
    a = np.full((1080, 1920, 4), 40, dtype=np.uint8)
    b = a.copy()
    b[200:400, 300:900, :3] = 230
    screen = ScreenSense(bus, ListSource([a]))
    screen.tick()
    d = Describer(chat=FakeChat(["screen A", "screen B", "never used"]), bus=bus)
    g = Glance(bus, screen, d)
    done = []
    bus.subscribe(lambda e: done.append(dict(e.data)), kinds={"glance"})

    vad = lambda on: bus.emit("audio", "vad", speaking=on)
    vad(True)
    time.sleep(0.4)                                      # long utterance: mid frame fires, but the screen did not change -> skipped
    vad(False)
    out = g.take()
    assert [x.text for x in out] == ["screen A"] and done[-1]["skipped"] >= 1, (out, done)

    # short utterance, screen changes during it: start and end frames both described, in order
    screen._source = ListSource([b]); screen.tick()
    d.chat.script[:] = ["screen B"]
    vad(True); vad(False)
    assert [x.text for x in g.take()] == ["screen B"]    # start frame already B (screen changed before it), end frame identical -> one description
    screen._source = ListSource([a]); screen.tick(); d.chat.script[:] = ["back to A"]
    vad(True)
    screen._source = ListSource([b]); screen.tick()
    d.chat.script.append("now B")
    vad(False)
    assert [x.text for x in g.take()] == ["back to A", "now B"], g.take()
    g.close()
    config.reload()
    print("glance ok")


if __name__ == "__main__":
    _selfcheck()
